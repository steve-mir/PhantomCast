"""[FEATURE:RTX-UPSCALER] AI upscaling of the final preview frame.

Implements Feature 1 of docs/FEATURE_IMPLEMENTATION_GUIDE.md:

  * **Primary path (Windows + RTX GPU, opt-in):** NVIDIA Video Effects
    SDK "SuperRes" (RTX VSR) via the ctypes shim in ``_rtx_vsr.py``.
    Experimental and disabled unless the ``PCAST_NVVFX_HOME`` environment
    variable points at an installed NVIDIA Video Effects SDK runtime.
  * **Fallback path (all platforms):** Real-ESRGAN x2 ONNX, downloaded
    from the same facefusion Hugging Face mirror the face parser uses.
    Runs on CUDA / CoreML / DirectML / CPU via onnxruntime.

The upscaler is a pure post-processing stage applied to the *display*
frame only (preview popup, in-window preview, window projection). The
virtual camera and the live recorder intentionally receive the original
resolution — you don't want to push a 4K stream into OBS.

Scale semantics: the ONNX model is a fixed x2; other slider values are
reached by resampling the x2 output (scale < 2 downsamples it, scale > 2
upsamples it — detail beyond x2 is interpolated, not hallucinated).

Removal: delete this file + ``_rtx_vsr.py`` + every block marked
[FEATURE:RTX-UPSCALER] (grep for it). With the switch off, frames pass
through untouched.
"""

from typing import Any, List, Optional
import os
import re
import sys
import threading
import time

import cv2
import numpy as np
import onnxruntime

import modules.globals
from modules.typing import Frame

NAME = "PCAST.RTX-UPSCALER"

MODEL_FILE = "real_esrgan_x2.onnx"
# facefusion mirror — same host as bisenet_resnet_18.onnx (verified live).
MODEL_URL = (
    "https://huggingface.co/facefusion/models-3.0.0/resolve/main/"
    "real_esrgan_x2.onnx"
)
MODEL_NATIVE_SCALE = 2.0

# Frames wider than this are downscaled before inference so the upscaler
# stays interactive on CPU/CoreML. The output is still resized to the
# requested scale of the ORIGINAL frame.
MAX_INFER_WIDTH = 960

# Inference runs in fixed-size tiles: bounds activation memory (RRDB nets
# OOM on full frames) and gives CoreML a single static shape to compile.
TILE = 256
TILE_OVERLAP = 16

from modules.paths import MODELS_DIR as models_dir

_session: Optional[onnxruntime.InferenceSession] = None
_session_failed = False
_rtx_vsr: Any = None        # _rtx_vsr.RtxVsr instance when the SDK path works
_rtx_vsr_probed = False
_lock = threading.Lock()

# Telemetry for the UI / logs.
backend: str = "off"        # "nvvfx" | "real_esrgan" | "off"
last_ms: float = 0.0


def pre_check() -> bool:
    """Ensure the Real-ESRGAN model is present (downloads ~64MB once)."""
    from modules.utilities import conditional_download

    try:
        os.makedirs(models_dir, exist_ok=True)
    except OSError as e:
        print(f"[{NAME}] Failed to create models dir: {e}")
        return False
    model_path = os.path.join(models_dir, MODEL_FILE)
    if not os.path.exists(model_path):
        print(f"[{NAME}] Downloading {MODEL_FILE} (~64MB)...")
        conditional_download(models_dir, [MODEL_URL])
    return os.path.exists(model_path)


def has_rtx_gpu() -> bool:
    """Detect an NVIDIA RTX-class GPU (guide step 3)."""
    pattern = re.compile(r"GeForce RTX|RTX A\d|RTX \d{4}", re.IGNORECASE)
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        try:
            for i in range(pynvml.nvmlDeviceGetCount()):
                raw = pynvml.nvmlDeviceGetName(
                    pynvml.nvmlDeviceGetHandleByIndex(i)
                )
                name = raw.decode() if isinstance(raw, bytes) else str(raw)
                if pattern.search(name):
                    return True
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                if pattern.search(torch.cuda.get_device_name(i)):
                    return True
    except Exception:
        pass
    return False


def _build_providers() -> List[Any]:
    cfg: List[Any] = []
    for p in modules.globals.execution_providers:
        cfg.append(p)
    if "CPUExecutionProvider" not in [
        p if isinstance(p, str) else p[0] for p in cfg
    ]:
        cfg.append("CPUExecutionProvider")
    return cfg


def _get_rtx_vsr() -> Any:
    """Try the NVIDIA Video Effects SDK path once (Windows + RTX, opt-in)."""
    global _rtx_vsr, _rtx_vsr_probed
    if _rtx_vsr_probed:
        return _rtx_vsr
    _rtx_vsr_probed = True
    if sys.platform != "win32" or not os.environ.get("PCAST_NVVFX_HOME"):
        return None
    if not has_rtx_gpu():
        return None
    try:
        from modules.processors.frame._rtx_vsr import RtxVsr

        _rtx_vsr = RtxVsr()
        print(f"[{NAME}] NVIDIA Video Effects SuperRes active (experimental).")
    except Exception as e:
        print(f"[{NAME}] RTX VSR unavailable ({e}); using Real-ESRGAN.")
        _rtx_vsr = None
    return _rtx_vsr


def _get_session() -> Optional[onnxruntime.InferenceSession]:
    global _session, _session_failed
    with _lock:
        if _session is not None or _session_failed:
            return _session
        model_path = os.path.join(models_dir, MODEL_FILE)
        if not os.path.exists(model_path):
            if not pre_check():
                _session_failed = True
                return None
        try:
            _session = onnxruntime.InferenceSession(
                model_path, providers=_build_providers()
            )
            print(f"[{NAME}] Real-ESRGAN x2 loaded "
                  f"({_session.get_providers()[0]}).")
        except Exception as e:
            print(f"[{NAME}] Failed on {modules.globals.execution_providers}: "
                  f"{e}; retrying on CPU.")
            try:
                _session = onnxruntime.InferenceSession(
                    model_path, providers=["CPUExecutionProvider"]
                )
            except Exception as e2:
                print(f"[{NAME}] Could not load {MODEL_FILE}: {e2}")
                _session = None
                _session_failed = True
    return _session


def _run_esrgan_tile(session, tile_bgr: np.ndarray) -> Optional[np.ndarray]:
    x = tile_bgr[:, :, ::-1].astype(np.float32) * (1.0 / 255.0)   # BGR→RGB
    x = np.transpose(x, (2, 0, 1))[np.newaxis, ...]               # NCHW
    try:
        y = session.run(None, {session.get_inputs()[0].name: x})[0]
    except Exception as e:
        print(f"[{NAME}] inference failed: {e}")
        return None
    y = np.transpose(y[0], (1, 2, 0))
    y = np.clip(y * 255.0, 0.0, 255.0).astype(np.uint8)
    return np.ascontiguousarray(y[:, :, ::-1])                    # RGB→BGR


def _run_esrgan(frame_bgr: Frame) -> Optional[Frame]:
    """Tiled x2 super-resolution. Every tile is exactly TILE×TILE (edges
    are reflect-padded), overlap regions are discarded after inference so
    tile seams never show."""
    session = _get_session()
    if session is None:
        return None
    h, w = frame_bgr.shape[:2]
    stride = TILE - 2 * TILE_OVERLAP
    grid_h = -(-h // stride) * stride                # ceil to stride
    grid_w = -(-w // stride) * stride
    padded = cv2.copyMakeBorder(
        frame_bgr,
        TILE_OVERLAP, grid_h - h + TILE_OVERLAP,
        TILE_OVERLAP, grid_w - w + TILE_OVERLAP,
        cv2.BORDER_REFLECT,
    )
    out = np.empty((2 * grid_h, 2 * grid_w, 3), dtype=np.uint8)
    for y0 in range(0, grid_h, stride):
        for x0 in range(0, grid_w, stride):
            tile_out = _run_esrgan_tile(
                session, padded[y0:y0 + TILE, x0:x0 + TILE]
            )
            if tile_out is None:
                return None
            lo, hi = 2 * TILE_OVERLAP, 2 * (TILE_OVERLAP + stride)
            out[2 * y0:2 * (y0 + stride), 2 * x0:2 * (x0 + stride)] = (
                tile_out[lo:hi, lo:hi]
            )
    return out[: 2 * h, : 2 * w]


def upscale(frame_bgr: Frame, scale: float) -> Frame:
    """Upscale *frame_bgr* by *scale* (1.0-4.0). Returns the input frame
    unchanged when scale <= 1 or every backend fails."""
    global backend, last_ms
    if frame_bgr is None or scale <= 1.01:
        backend = "off"
        return frame_bgr

    t0 = time.perf_counter()
    h, w = frame_bgr.shape[:2]
    out_size = (int(round(w * scale)), int(round(h * scale)))

    # Primary: NVIDIA Video Effects SuperRes (opt-in, Windows + RTX).
    vsr = _get_rtx_vsr()
    if vsr is not None:
        try:
            out = vsr.run(frame_bgr, scale)
            if out is not None:
                if out.shape[1::-1] != out_size:
                    out = cv2.resize(out, out_size, interpolation=cv2.INTER_AREA)
                backend = "nvvfx"
                last_ms = (time.perf_counter() - t0) * 1000.0
                return out
        except Exception as e:
            print(f"[{NAME}] NvVFX run failed ({e}); falling back.")

    # Fallback: Real-ESRGAN x2 ONNX. Cap the inference size so CPU-class
    # hardware stays interactive.
    infer = frame_bgr
    if w > MAX_INFER_WIDTH:
        s = MAX_INFER_WIDTH / float(w)
        infer = cv2.resize(
            frame_bgr, (MAX_INFER_WIDTH, max(2, int(h * s))),
            interpolation=cv2.INTER_AREA,
        )
    out = _run_esrgan(infer)
    if out is None:
        backend = "off"
        return frame_bgr
    if out.shape[1::-1] != out_size:
        interp = cv2.INTER_AREA if out.shape[1] > out_size[0] else cv2.INTER_LANCZOS4
        out = cv2.resize(out, out_size, interpolation=interp)
    backend = "real_esrgan"
    last_ms = (time.perf_counter() - t0) * 1000.0
    return out

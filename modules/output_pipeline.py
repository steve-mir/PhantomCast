"""Output pipeline helpers — Virtual Camera, LUTs, resolution map, GPU helpers.

This module backs the 2.7-parity Camera/Output/UI features:

* :class:`VirtualCamSink` — thin wrapper around :mod:`pyvirtualcam` that
  pushes BGR frames to OBS Virtual Camera / v4l2loopback.  Falls back to a
  no-op when ``pyvirtualcam`` is not installed or no backend is reachable
  so the live preview never crashes.
* :func:`load_cube_lut` / :func:`apply_lut` — tiny .cube LUT parser plus a
  trilinear sampler that grades a BGR frame.  Designed to run at webcam
  resolution at >60fps even on CPU.
* :func:`resolution_map` — translates the user-facing dropdown
  (``"720p"`` / ``"1080p"`` / ...) into a ``(width, height, fps)`` tuple
  the :class:`VideoCapturer` understands.
* :func:`detect_gpus` — enumerates CUDA devices via ``pynvml`` (Windows /
  Linux NVIDIA only) so the GPU Changer dropdown can list real device
  names instead of just an index.
"""
from __future__ import annotations

import os
import platform
import threading
from typing import List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Resolution Switch
# ---------------------------------------------------------------------------

_RES_TABLE = {
    "Auto": (1920, 1080, 60),
    "480p": (854, 480, 30),
    "720p": (1280, 720, 60),
    "1080p": (1920, 1080, 60),
    "1440p": (2560, 1440, 30),
    "4K": (3840, 2160, 30),
}


def resolution_map(name: str) -> Tuple[int, int, int]:
    """Return (width, height, fps) for the dropdown label *name*.

    Unknown labels fall back to ``Auto`` so a corrupted ``switch_states.json``
    can never break startup.
    """
    return _RES_TABLE.get(name, _RES_TABLE["Auto"])


def resolution_labels() -> Tuple[str, ...]:
    """Ordered list of resolution dropdown labels."""
    return tuple(_RES_TABLE.keys())


# ---------------------------------------------------------------------------
# LUTs for color grading (.cube parser)
# ---------------------------------------------------------------------------

class LutTable:
    """Parsed Adobe .cube LUT (3D, RGB).

    The parser supports the common ``LUT_3D_SIZE``/``LUT_1D_SIZE`` headers
    plus optional ``DOMAIN_MIN`` / ``DOMAIN_MAX``.  Lines starting with
    ``#`` and ``TITLE`` are ignored.  Both 1D and 3D LUTs return a 3D LUT
    after parsing — 1D entries are broadcast across each axis.
    """

    __slots__ = ("size", "table", "domain_min", "domain_max", "name")

    def __init__(self, size: int, table: np.ndarray, name: str = "",
                 domain_min: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                 domain_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
        self.size = size
        # table is shape (size, size, size, 3) in [0,1] RGB
        self.table = table
        self.domain_min = np.asarray(domain_min, dtype=np.float32)
        self.domain_max = np.asarray(domain_max, dtype=np.float32)
        self.name = name


def load_cube_lut(path: str) -> LutTable:
    """Parse a .cube LUT file.  Raises ``ValueError`` on malformed input."""
    size = 0
    is_3d = True
    domain_min = (0.0, 0.0, 0.0)
    domain_max = (1.0, 1.0, 1.0)
    rows: List[List[float]] = []

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.upper().startswith("TITLE"):
                continue
            parts = line.split()
            head = parts[0].upper()
            if head == "LUT_3D_SIZE":
                size = int(parts[1])
                is_3d = True
                continue
            if head == "LUT_1D_SIZE":
                size = int(parts[1])
                is_3d = False
                continue
            if head == "DOMAIN_MIN":
                domain_min = (float(parts[1]), float(parts[2]), float(parts[3]))
                continue
            if head == "DOMAIN_MAX":
                domain_max = (float(parts[1]), float(parts[2]), float(parts[3]))
                continue
            if len(parts) >= 3 and not head.replace(".", "", 1).replace("-", "", 1).isalpha():
                try:
                    rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
                except ValueError:
                    continue

    if size <= 0:
        raise ValueError(f"{path}: missing LUT_3D_SIZE / LUT_1D_SIZE")

    arr = np.asarray(rows, dtype=np.float32)
    if is_3d:
        if arr.shape[0] != size ** 3:
            raise ValueError(
                f"{path}: expected {size**3} LUT rows, got {arr.shape[0]}"
            )
        # .cube spec: red varies fastest, then green, then blue.
        table = arr.reshape((size, size, size, 3), order="C")
        # Reorder to (R, G, B) -> table[r, g, b]
        table = np.transpose(table, (2, 1, 0, 3))
    else:
        if arr.shape[0] != size:
            raise ValueError(
                f"{path}: expected {size} 1D LUT rows, got {arr.shape[0]}"
            )
        # Build a separable 3D LUT from the 1D curve.
        lin = arr  # (size, 3)
        table = np.zeros((size, size, size, 3), dtype=np.float32)
        for r in range(size):
            for g in range(size):
                for b in range(size):
                    table[r, g, b, 0] = lin[r, 0]
                    table[r, g, b, 1] = lin[g, 1]
                    table[r, g, b, 2] = lin[b, 2]

    return LutTable(
        size=size, table=table, name=os.path.basename(path),
        domain_min=domain_min, domain_max=domain_max,
    )


def apply_lut(frame_bgr: np.ndarray, lut: LutTable, strength: float = 1.0) -> np.ndarray:
    """Apply *lut* to *frame_bgr* with trilinear interpolation.

    ``strength`` blends between the ungraded original and the fully-graded
    output (0.0 = original, 1.0 = full LUT).
    """
    if frame_bgr is None or lut is None or strength <= 0.0:
        return frame_bgr

    # BGR -> RGB normalised into the LUT's domain.
    f = frame_bgr.astype(np.float32) / 255.0
    rgb = f[..., ::-1]
    dmin, dmax = lut.domain_min, lut.domain_max
    rgb = (rgb - dmin) / np.maximum(dmax - dmin, 1e-6)
    rgb = np.clip(rgb, 0.0, 1.0)

    n = lut.size - 1
    # Indices and fractional offsets for trilinear interp
    idx = rgb * n
    i0 = np.floor(idx).astype(np.int32)
    i1 = np.minimum(i0 + 1, n)
    t = idx - i0  # (H, W, 3) in [0,1]

    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    tr, tg, tb = t[..., 0:1], t[..., 1:2], t[..., 2:3]

    tbl = lut.table
    c000 = tbl[r0, g0, b0]
    c100 = tbl[r1, g0, b0]
    c010 = tbl[r0, g1, b0]
    c110 = tbl[r1, g1, b0]
    c001 = tbl[r0, g0, b1]
    c101 = tbl[r1, g0, b1]
    c011 = tbl[r0, g1, b1]
    c111 = tbl[r1, g1, b1]

    c00 = c000 * (1 - tr) + c100 * tr
    c10 = c010 * (1 - tr) + c110 * tr
    c01 = c001 * (1 - tr) + c101 * tr
    c11 = c011 * (1 - tr) + c111 * tr
    c0 = c00 * (1 - tg) + c10 * tg
    c1 = c01 * (1 - tg) + c11 * tg
    out_rgb = c0 * (1 - tb) + c1 * tb

    out_rgb = np.clip(out_rgb, 0.0, 1.0)
    if strength < 1.0:
        out_rgb = out_rgb * strength + f[..., ::-1] * (1.0 - strength)

    out_bgr = (out_rgb[..., ::-1] * 255.0).astype(np.uint8)
    return out_bgr


# ---------------------------------------------------------------------------
# Virtual Camera (pyvirtualcam wrapper)
# ---------------------------------------------------------------------------

class VirtualCamSink:
    """Thread-safe wrapper around :class:`pyvirtualcam.Camera`.

    Designed to be opened lazily once the swap pipeline knows the actual
    output dimensions (camera + LUT may resize) and closed cleanly when
    the user toggles the switch off or stops the live preview.

    On macOS the OBS Virtual Camera plug-in must be installed; on Windows
    OBS provides the same plug-in; on Linux v4l2loopback is required.  When
    ``pyvirtualcam`` is not installed (or no backend is found) ``send()``
    becomes a no-op so the live pipeline keeps running unmodified.
    """

    def __init__(self) -> None:
        self._cam = None
        self._width = 0
        self._height = 0
        self._fps = 30
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        self.backend: str = ""

    # ---------- lifecycle ----------

    def open(self, width: int, height: int, fps: int = 30) -> bool:
        """Open or re-open the device.  Returns True on success."""
        with self._lock:
            self._close_locked()
            try:
                import pyvirtualcam  # type: ignore
            except Exception as e:  # noqa: BLE001
                self._last_error = (
                    "pyvirtualcam not installed (pip install pyvirtualcam) — "
                    f"virtual camera disabled: {e}"
                )
                return False

            try:
                self._cam = pyvirtualcam.Camera(
                    width=int(width),
                    height=int(height),
                    fps=int(fps),
                    fmt=pyvirtualcam.PixelFormat.BGR,
                    print_fps=False,
                )
                self._width = int(width)
                self._height = int(height)
                self._fps = int(fps)
                self.backend = getattr(self._cam, "backend", "") or ""
                self._last_error = None
                return True
            except Exception as e:  # noqa: BLE001
                self._last_error = (
                    f"virtual camera unavailable (no OBS plug-in / "
                    f"v4l2loopback?): {e}"
                )
                self._cam = None
                return False

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        cam = self._cam
        self._cam = None
        if cam is not None:
            try:
                cam.close()
            except Exception:
                pass
        self.backend = ""

    @property
    def is_open(self) -> bool:
        return self._cam is not None

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def size(self) -> Tuple[int, int]:
        return (self._width, self._height)

    # ---------- frame push ----------

    def send(self, frame_bgr: np.ndarray) -> None:
        """Push a BGR frame.  Silently drops if the device is closed or if
        the frame size changed (caller should call :meth:`open` again with
        the new dimensions)."""
        if frame_bgr is None:
            return
        with self._lock:
            cam = self._cam
            if cam is None:
                return
            try:
                h, w = frame_bgr.shape[:2]
                if w != self._width or h != self._height:
                    # Mid-stream resize — re-open with the new size.
                    self._close_locked()
                    if not self.open(w, h, self._fps):
                        return
                    cam = self._cam
                    if cam is None:
                        return
                cam.send(frame_bgr)
                # ``sleep_until_next_frame`` paces output to ``fps``.  We
                # call it only when our own loop is producing faster than
                # the configured rate; otherwise the caller already paced.
            except Exception as e:  # noqa: BLE001
                self._last_error = f"virtual cam send failed: {e}"
                self._close_locked()


_VCAM_SINGLETON: Optional[VirtualCamSink] = None


def get_virtual_cam() -> VirtualCamSink:
    """Process-wide singleton so the UI and live thread share the same sink."""
    global _VCAM_SINGLETON
    if _VCAM_SINGLETON is None:
        _VCAM_SINGLETON = VirtualCamSink()
    return _VCAM_SINGLETON


# ---------------------------------------------------------------------------
# GPU Changer / Multi-GPU
# ---------------------------------------------------------------------------

def detect_gpus() -> Tuple[List[str], int]:
    """Return ``(names, count)`` for available GPUs.

    Order of preference:

    1. ``pynvml`` (NVIDIA, exact device names + indices)
    2. ``torch.cuda`` (covers the common Windows + Linux NVIDIA setup)
    3. Single fallback entry matching the current onnxruntime providers.
    """
    names: List[str] = []
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        try:
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw = pynvml.nvmlDeviceGetName(h)
                names.append(raw.decode() if isinstance(raw, bytes) else str(raw))
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    names.append(torch.cuda.get_device_name(i))
        except Exception:
            pass

    if not names:
        # Probe onnxruntime's effective provider list as a last resort.
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
            if "CUDAExecutionProvider" in providers:
                names = ["CUDA: GPU 0"]
            elif "CoreMLExecutionProvider" in providers:
                names = ["Apple Silicon (CoreML)"]
            elif "DmlExecutionProvider" in providers:
                names = ["DirectML GPU 0"]
            else:
                names = ["CPU"]
        except Exception:
            names = ["CPU"]

    return names, len(names)


def apply_force_discrete_gpu() -> None:
    """Set NVIDIA Optimus / AMD switchable hints so dual-GPU laptops pick
    the discrete card.  Must be called before any GL/CUDA context is
    created, i.e. as early as possible at startup."""
    if platform.system() != "Windows":
        return
    # NVIDIA Optimus
    os.environ.setdefault("NVIDIA_VISIBLE_DEVICES", "all")
    # AMD PowerXpress / Switchable Graphics
    os.environ.setdefault("AmdPowerXpressRequestHighPerformance", "1")


def set_cuda_visible_device(device_id: int) -> None:
    """Pin the active CUDA device for onnxruntime sessions created later."""
    if device_id < 0:
        return
    os.environ["CUDA_VISIBLE_DEVICES"] = str(int(device_id))

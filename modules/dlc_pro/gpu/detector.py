"""Runtime GPU detection cascade for Phantom-Cast Pro.

Five-stage probe that decides whether to run on CUDA or fall back to CPU:

    1. NVIDIA hardware via WMIC / pynvml
    2. Driver version (>= 525.60 for CUDA 12.x)
    3. CUDA runtime DLLs reachable on PATH
    4. onnxruntime exposes CUDAExecutionProvider
    5. ONNX smoke test runs without raising

Each step contributes a :class:`GpuProbeResult`. A single FAIL anywhere drops
the app into CPU mode but is *recoverable* — the wizard re-runs the cascade
after the user installs the missing piece.

The result object is intentionally JSON-serialisable so the UI's diagnostics
panel can render it directly and the support email can copy it verbatim.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import List, Optional

from modules.dlc_pro.logger import get
from modules.dlc_pro.paths import settings_file
from modules.dlc_pro.gpu.bootstrap import prime_paths

log = get("gpu.detector")


MIN_DRIVER_VERSION = (525, 60)  # CUDA 12.x minimum on Windows
REQUIRED_DLLS_WIN = (
    "cudart64_12.dll",
    # cuDNN 9 is the modern file; we accept either 9 or 8.9
    ("cudnn64_9.dll", "cudnn64_8.dll"),
    "cublas64_12.dll",
    "cublasLt64_12.dll",
)


class GpuMode(str, Enum):
    AUTO = "auto"
    CUDA = "cuda"
    COREML = "coreml"
    CPU = "cpu"


class Severity(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class ProbeStep:
    name: str
    severity: Severity
    detail: str
    remedy: str = ""


@dataclass
class GpuProbeResult:
    has_nvidia_gpu: bool = False
    gpu_name: str = ""
    driver_version: str = ""
    compute_capability: str = ""
    cuda_runtime_ok: bool = False
    cudnn_ok: bool = False
    onnx_provider_ok: bool = False
    smoke_test_ok: bool = False
    selected: GpuMode = GpuMode.CPU
    steps: List[ProbeStep] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        d["selected"] = self.selected.value
        d["steps"] = [
            {**asdict(s), "severity": s.severity.value} for s in self.steps
        ]
        return json.dumps(d, indent=2)

    @property
    def usable(self) -> bool:
        return self.smoke_test_ok and self.selected != GpuMode.CPU


# ---------- Step 1: NVIDIA hardware presence ----------


def _probe_hardware(result: GpuProbeResult) -> None:
    name = ""
    # Try pynvml (cheap if installed)
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        if pynvml.nvmlDeviceGetCount() > 0:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            raw = pynvml.nvmlDeviceGetName(handle)
            name = raw.decode() if isinstance(raw, bytes) else str(raw)
        pynvml.nvmlShutdown()
    except Exception:
        pass

    # Fall back to WMIC on Windows
    if not name and sys.platform == "win32":
        try:
            out = subprocess.run(
                ["wmic", "path", "win32_videocontroller", "get", "Name"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout
            for line in out.splitlines():
                if "NVIDIA" in line.upper():
                    name = line.strip()
                    break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Linux/macOS: lspci or system_profiler
    if not name and sys.platform.startswith("linux"):
        try:
            out = subprocess.run(
                ["lspci"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            for line in out.splitlines():
                if "NVIDIA" in line.upper() and ("VGA" in line or "3D" in line):
                    name = line.split(":", 2)[-1].strip()
                    break
        except FileNotFoundError:
            pass

    result.has_nvidia_gpu = bool(name)
    result.gpu_name = name
    if name:
        result.steps.append(
            ProbeStep("hardware", Severity.OK, f"Detected: {name}")
        )
    else:
        result.steps.append(
            ProbeStep(
                "hardware",
                Severity.FAIL,
                "No NVIDIA GPU detected.",
                "Install an NVIDIA GPU or run the app in CPU mode (Settings → Execution).",
            )
        )


# ---------- Step 2: driver ----------


def _probe_driver(result: GpuProbeResult) -> None:
    if not result.has_nvidia_gpu:
        return
    smi = shutil.which("nvidia-smi")
    if not smi:
        result.steps.append(
            ProbeStep(
                "driver",
                Severity.FAIL,
                "nvidia-smi not found on PATH.",
                "Install the latest NVIDIA Game Ready or Studio driver.",
            )
        )
        return
    try:
        out = subprocess.run(
            [smi, "--query-gpu=driver_version,compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        if not out:
            raise RuntimeError("no GPUs reported")
        first = [c.strip() for c in out[0].split(",")]
        result.driver_version = first[0] if first else ""
        result.compute_capability = first[1] if len(first) > 1 else ""
        major_minor = re.match(r"(\d+)\.(\d+)", result.driver_version)
        if major_minor:
            ver = (int(major_minor.group(1)), int(major_minor.group(2)))
            if ver < MIN_DRIVER_VERSION:
                result.steps.append(
                    ProbeStep(
                        "driver",
                        Severity.FAIL,
                        f"Driver {result.driver_version} < required "
                        f"{MIN_DRIVER_VERSION[0]}.{MIN_DRIVER_VERSION[1]}.",
                        "Update NVIDIA driver from https://www.nvidia.com/Download/index.aspx",
                    )
                )
                return
        result.steps.append(
            ProbeStep(
                "driver",
                Severity.OK,
                f"Driver {result.driver_version}, compute capability "
                f"{result.compute_capability}",
            )
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        result.steps.append(
            ProbeStep("driver", Severity.FAIL, f"nvidia-smi failed: {e}",
                      "Reinstall NVIDIA driver.")
        )


# ---------- Step 3: CUDA runtime DLLs ----------


def _find_dll(name: str) -> Optional[str]:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            return cand
    return None


def _probe_runtime(result: GpuProbeResult) -> None:
    if not result.has_nvidia_gpu:
        return
    if sys.platform != "win32":
        # On Linux/macOS the loader paths handle this; trust onnxruntime.
        result.cuda_runtime_ok = True
        result.cudnn_ok = True
        result.steps.append(
            ProbeStep("runtime", Severity.OK, "Non-Windows OS; trusting loader paths.")
        )
        return

    prime_paths()  # idempotent — make sure bundled / venv paths are visible
    missing: List[str] = []
    for entry in REQUIRED_DLLS_WIN:
        if isinstance(entry, tuple):
            if not any(_find_dll(n) for n in entry):
                missing.append(" or ".join(entry))
        else:
            if not _find_dll(entry):
                missing.append(entry)

    if missing:
        result.steps.append(
            ProbeStep(
                "runtime",
                Severity.FAIL,
                "Missing CUDA/cuDNN DLLs: " + ", ".join(missing),
                "Reinstall Phantom-Cast Pro to restore the bundled CUDA runtime, "
                "or install CUDA 12.8 + cuDNN 8.9.7 manually.",
            )
        )
        return
    result.cuda_runtime_ok = True
    result.cudnn_ok = True
    result.steps.append(ProbeStep("runtime", Severity.OK, "CUDA 12 + cuDNN DLLs reachable."))


# ---------- Step 4: onnxruntime provider ----------


def _probe_onnx_provider(result: GpuProbeResult) -> None:
    if not result.cuda_runtime_ok:
        return
    try:
        import onnxruntime as ort  # noqa: WPS433
        providers = ort.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            result.onnx_provider_ok = True
            result.steps.append(
                ProbeStep("onnx_provider", Severity.OK,
                          f"onnxruntime providers: {providers}")
            )
        else:
            result.steps.append(
                ProbeStep(
                    "onnx_provider",
                    Severity.FAIL,
                    f"CUDAExecutionProvider not exposed. Got: {providers}",
                    "Reinstall onnxruntime-gpu==1.21+ matching CUDA 12.x.",
                )
            )
    except ImportError as e:
        result.steps.append(
            ProbeStep("onnx_provider", Severity.FAIL, f"onnxruntime import failed: {e}",
                      "Reinstall the application.")
        )


# ---------- Step 5: actual inference smoke test ----------


_SMOKE_MODEL_BYTES: Optional[bytes] = None


def _build_smoke_model() -> bytes:
    """Build a 1-op ONNX model in-memory: Identity(1x3x4x4 fp32).

    Avoids shipping a binary file, and avoids depending on onnx (which is
    already a project dependency, but importing it here lazily is cheaper).
    """
    global _SMOKE_MODEL_BYTES
    if _SMOKE_MODEL_BYTES is not None:
        return _SMOKE_MODEL_BYTES
    import onnx
    from onnx import helper, TensorProto

    inp = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 4, 4])
    out = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 3, 4, 4])
    node = helper.make_node("Identity", ["x"], ["y"])
    graph = helper.make_graph([node], "smoke", [inp], [out])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    _SMOKE_MODEL_BYTES = model.SerializeToString()
    return _SMOKE_MODEL_BYTES


def _probe_smoke(result: GpuProbeResult) -> None:
    if not result.onnx_provider_ok:
        return
    try:
        import numpy as np
        import onnxruntime as ort

        sess = ort.InferenceSession(
            _build_smoke_model(), providers=["CUDAExecutionProvider"]
        )
        x = np.zeros((1, 3, 4, 4), dtype=np.float32)
        sess.run(None, {"x": x})
        result.smoke_test_ok = True
        result.steps.append(
            ProbeStep("smoke", Severity.OK, "CUDA inference smoke test passed.")
        )
    except Exception as e:  # noqa: BLE001 — any failure means CPU
        result.steps.append(
            ProbeStep(
                "smoke",
                Severity.FAIL,
                f"CUDA smoke inference failed: {e}",
                "Falling back to CPU. Re-run after updating drivers / reinstalling.",
            )
        )


# ---------- macOS / CoreML cascade ----------


def _probe_coreml(result: GpuProbeResult) -> None:
    """Detect Apple Silicon (or Intel Mac) and verify CoreMLExecutionProvider.

    This is the macOS counterpart to the CUDA cascade. There's no NVIDIA
    hardware on macOS, so ``has_nvidia_gpu`` stays False — but ``gpu_name``
    is set to the chip identifier and the same ``smoke_test_ok`` field
    gates the "usable" decision.
    """
    import platform as _plat

    arch = _plat.machine()
    name = ""
    try:
        name = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if not name:
        name = f"Apple Silicon ({arch})" if arch == "arm64" else f"Mac ({arch})"
    result.gpu_name = name
    result.steps.append(
        ProbeStep(
            "hardware", Severity.OK,
            f"Detected: {name} — targeting CoreML on macOS.",
        )
    )

    try:
        import onnxruntime as ort  # noqa: WPS433
        providers = ort.get_available_providers()
    except ImportError as e:
        result.steps.append(
            ProbeStep(
                "onnx_provider", Severity.FAIL,
                f"onnxruntime import failed: {e}",
                "Reinstall the application (pip install onnxruntime).",
            )
        )
        return

    if "CoreMLExecutionProvider" not in providers:
        result.steps.append(
            ProbeStep(
                "onnx_provider", Severity.FAIL,
                f"CoreMLExecutionProvider not exposed. Got: {providers}",
                "Reinstall onnxruntime — the macOS wheel ships CoreML.",
            )
        )
        return
    result.onnx_provider_ok = True
    result.steps.append(
        ProbeStep(
            "onnx_provider", Severity.OK,
            f"onnxruntime providers: {providers}",
        )
    )

    try:
        import numpy as np
        import onnxruntime as ort

        sess = ort.InferenceSession(
            _build_smoke_model(),
            providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        )
        x = np.zeros((1, 3, 4, 4), dtype=np.float32)
        sess.run(None, {"x": x})
        result.smoke_test_ok = True
        result.steps.append(
            ProbeStep("smoke", Severity.OK, "CoreML inference smoke test passed.")
        )
    except Exception as e:  # noqa: BLE001
        result.steps.append(
            ProbeStep(
                "smoke", Severity.FAIL,
                f"CoreML smoke inference failed: {e}",
                "Falling back to CPU. Re-run after reinstalling onnxruntime.",
            )
        )


# ---------- Public API ----------


_LAST_RESULT: Optional[GpuProbeResult] = None


def detect(force: bool = False) -> GpuProbeResult:
    """Run the platform-appropriate cascade. Cached unless ``force=True``.

    macOS → CoreML cascade (no CUDA on Mac).
    Windows / Linux → existing NVIDIA / CUDA cascade.
    """
    global _LAST_RESULT
    if _LAST_RESULT is not None and not force:
        return _LAST_RESULT

    prime_paths()  # before any onnxruntime touch (no-op on non-Windows)
    result = GpuProbeResult()

    if sys.platform == "darwin":
        _probe_coreml(result)
        result.selected = GpuMode.COREML if result.smoke_test_ok else GpuMode.CPU
    else:
        _probe_hardware(result)
        _probe_driver(result)
        _probe_runtime(result)
        _probe_onnx_provider(result)
        _probe_smoke(result)
        result.selected = GpuMode.CUDA if result.smoke_test_ok else GpuMode.CPU

    log.info("gpu detect -> %s", result.selected.value)
    for s in result.steps:
        log.info("  [%s] %s — %s", s.severity.value.upper(), s.name, s.detail)

    _LAST_RESULT = result
    return result


# ---------- User override ----------


def _load_settings() -> dict:
    p = settings_file()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(d: dict) -> None:
    settings_file().write_text(json.dumps(d, indent=2), encoding="utf-8")


def set_user_override(mode: GpuMode) -> None:
    s = _load_settings()
    s["execution_mode"] = mode.value
    _save_settings(s)


def _user_override() -> GpuMode:
    val = _load_settings().get("execution_mode", GpuMode.AUTO.value)
    try:
        return GpuMode(val)
    except ValueError:
        return GpuMode.AUTO


def selected_mode() -> GpuMode:
    """Return the *effective* mode honouring user override + probe result."""
    override = _user_override()
    if override == GpuMode.CPU:
        return GpuMode.CPU
    probe = detect()
    if override == GpuMode.CUDA:
        # User insists on CUDA. Honour even if smoke failed; runtime may
        # have just recovered. Caller can still see probe.usable.
        return GpuMode.CUDA if probe.has_nvidia_gpu else GpuMode.CPU
    if override == GpuMode.COREML:
        return GpuMode.COREML if sys.platform == "darwin" else GpuMode.CPU
    # AUTO — pick whatever the cascade decided
    return probe.selected if probe.usable else GpuMode.CPU


def execution_providers() -> List[str]:
    """Provider list to pass to ``onnxruntime.InferenceSession``."""
    mode = selected_mode()
    if mode == GpuMode.CUDA:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if mode == GpuMode.COREML:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]

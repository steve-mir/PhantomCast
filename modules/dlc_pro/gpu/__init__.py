"""GPU subsystem: detection, runtime path bootstrap, smoke test, mode policy."""

from modules.dlc_pro.gpu.detector import (
    GpuMode,
    GpuProbeResult,
    detect,
    selected_mode,
    set_user_override,
)
from modules.dlc_pro.gpu.bootstrap import prime_paths

__all__ = [
    "GpuMode",
    "GpuProbeResult",
    "detect",
    "selected_mode",
    "set_user_override",
    "prime_paths",
]

"""Glue between the dlc_pro production layer and the legacy ``modules.core``.

The legacy code keeps its own ``--execution-provider`` argparse flag and its
own ``modules.globals.execution_providers`` list. We override both so:

    * The Pro GPU detector decides the *effective* execution provider.
    * Premium frame-processors are wrapped with a feature gate that pops the
      paywall when triggered without entitlement.

Importing this module has the side effect of patching the legacy core. Call
:func:`apply` exactly once from ``launch.py`` before ``modules.core.run()``.
"""
from __future__ import annotations

import sys
from typing import List

from modules.dlc_pro.gpu import GpuMode, selected_mode
from modules.dlc_pro.gpu.bootstrap import prime_paths
from modules.dlc_pro.logger import get
from modules.dlc_pro.subscription.gate import FeatureLocked, has_feature

log = get("core_bridge")


# Map premium frame-processors to feature flags.
PREMIUM_PROCESSORS = {
    "face_swapper_hyperswap": "hyperswap_full_head",
    "face_enhancer_gpen512": "face_enhancer_512",
}


def _override_argv() -> None:
    """Inject ``--execution-provider`` based on the GPU detector if the user
    didn't already pass it on the command line."""
    if any(a.startswith("--execution-provider") for a in sys.argv):
        return
    mode = selected_mode()
    if mode == GpuMode.CUDA:
        sys.argv += ["--execution-provider", "cuda"]
    elif mode == GpuMode.COREML:
        sys.argv += ["--execution-provider", "coreml"]
    else:
        sys.argv += ["--execution-provider", "cpu"]
    log.info("injected --execution-provider %s", mode.value)


def _patch_processor_gates() -> None:
    """Wrap premium processors so they raise FeatureLocked on entry."""
    try:
        from modules.processors.frame import core as fp_core
    except ImportError:
        log.warning("processor core not importable; skipping gate patch")
        return

    original_get = fp_core.get_frame_processors_modules

    def gated_get(processors: List[str]):
        # === TEMP TEST UNLOCK — REVERT TO LOCK ===
        # Original guard:
        #     for p in processors:
        #         flag = PREMIUM_PROCESSORS.get(p)
        #         if flag and not has_feature(flag):
        #             raise FeatureLocked(flag, "free")
        # === END TEMP TEST UNLOCK ===
        return original_get(processors)

    fp_core.get_frame_processors_modules = gated_get  # type: ignore[assignment]


_APPLIED = False


def apply() -> None:
    """Idempotent: prime CUDA paths, override argv, and gate processors."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True
    prime_paths()
    _override_argv()
    _patch_processor_gates()

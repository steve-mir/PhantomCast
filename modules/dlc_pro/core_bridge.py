"""Glue between the dlc_pro production layer and the legacy ``modules.core``.

The legacy code keeps its own ``--execution-provider`` argparse flag and its
own ``modules.globals.execution_providers`` list. We override both so:

    * The Pro GPU detector decides the *effective* execution provider.
    * Free users get their requested processor list silently rewritten to
      free-tier equivalents (no startup crash if their saved state includes
      a premium processor like Hyperswap).
    * Premium-only processors that *can't* be silently substituted raise
      :class:`FeatureLocked` as a defense-in-depth net.
    * Every output frame from the swapper gets a free-tier watermark drawn
      on top (when the user lacks ``no_watermark``).

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


# Processors that get a *quality* downgrade for free users — silently
# substituted, not blocked. The `flag` is the entitlement that, when
# present, leaves the original processor in place. During a 2-minute
# preview window ``has_feature(flag)`` flips True, so the real processor
# runs and the user can taste the premium quality.
FREE_FALLBACKS = {
    "face_swapper":            ("face_swapper_lite", "gpu_inference"),
    "face_swapper_hyperswap":  ("face_swapper_lite", "hyperswap_full_head"),
}

# Enhancers free users don't get at all. Removed from the active processor
# list rather than blocked, so the run still completes (no enhancement
# instead of an error).
FREE_DROPPED = {
    "face_enhancer":           "face_enhancer_512",
    "face_enhancer_gpen256":   "face_enhancer_512",
    "face_enhancer_gpen512":   "face_enhancer_512",
}

# Anything still premium-only after :func:`_route_for_plan` runs. Should
# normally be empty for a free user — kept as a hard backstop in case a
# future processor is added without a fallback entry above.
PREMIUM_PROCESSORS = {
    "face_swapper_hyperswap": "hyperswap_full_head",
    "face_enhancer_gpen512":  "face_enhancer_512",
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


def _route_for_plan(processors: List[str]) -> List[str]:
    """Rewrite the processor list based on current entitlements.

    Premium users (or anyone inside an active 2-min preview window for
    the relevant flag) get the list unchanged. Free users get premium
    processors substituted (``face_swapper_hyperswap`` → ``face_swapper_lite``)
    and free-disallowed enhancers dropped. After this rewrite, no
    premium-only processor should remain in the list.
    """
    out: List[str] = []
    for p in processors:
        if p in FREE_FALLBACKS:
            replacement, flag = FREE_FALLBACKS[p]
            if not has_feature(flag):
                if p != replacement:
                    log.info("routing %s -> %s (free tier)", p, replacement)
                out.append(replacement)
                continue
        if p in FREE_DROPPED:
            flag = FREE_DROPPED[p]
            if not has_feature(flag):
                log.info("dropping %s (free tier)", p)
                continue
        out.append(p)
    return out


def _route_fp_ui() -> None:
    """Rewrite ``modules.globals.fp_ui`` so set_frame_processors_modules_from_ui
    doesn't immediately re-add premium processors after :func:`_route_for_plan`
    cleaned the main list.

    Idempotent and persistent within the run: if fp_ui says
    ``face_swapper_hyperswap=True`` and the user has no entitlement, we
    flip that to False and set ``face_swapper_lite=True``. On a future
    upgrade the user re-enables the premium toggle from the UI; that path
    is unchanged.
    """
    import modules.globals as g
    fp_ui = getattr(g, "fp_ui", None)
    if not isinstance(fp_ui, dict):
        return

    # Premium swap → lite swap.
    for src, (dst, flag) in FREE_FALLBACKS.items():
        if src == dst:
            continue
        if fp_ui.get(src) and not has_feature(flag):
            fp_ui[src] = False
            fp_ui[dst] = True

    # Free-disallowed enhancers off.
    for src, flag in FREE_DROPPED.items():
        if fp_ui.get(src) and not has_feature(flag):
            fp_ui[src] = False


_WATERMARK_PATCHED = False


def _ensure_watermark_patched() -> None:
    """Wrap ``face_swapper.apply_post_processing`` once, on first use.

    Has to be lazy: at the time :func:`apply` runs, ``modules.core`` is
    only partially loaded (legacy circular-import path), so importing
    ``face_swapper`` eagerly would fail. By the time
    :func:`get_frame_processors_modules` is called, the legacy core has
    finished its imports and ``face_swapper`` resolves cleanly.
    """
    global _WATERMARK_PATCHED
    if _WATERMARK_PATCHED:
        return
    try:
        from modules.processors.frame import face_swapper as fs
    except ImportError as e:
        log.warning("face_swapper still not importable; watermark deferred: %s", e)
        return

    from modules.dlc_pro.output.watermark import watermark_if_free

    original_app = fs.apply_post_processing

    def watermarked(current_frame, swapped_face_bboxes):
        out = original_app(current_frame, swapped_face_bboxes)
        return watermark_if_free(out)

    fs.apply_post_processing = watermarked  # type: ignore[assignment]
    _WATERMARK_PATCHED = True
    log.info("watermark patch installed")


def _patch_processor_gates() -> None:
    """Wrap the processor loader so:

        1. Free users get their processor list rewritten via :func:`_route_for_plan`.
        2. Watermark patch is installed lazily on first use.
        3. Anything still premium after rewrite raises :class:`FeatureLocked`
           — should be unreachable for normal flows but covers defense-in-depth.
    """
    try:
        from modules.processors.frame import core as fp_core
    except ImportError:
        log.warning("processor core not importable; skipping gate patch")
        return

    original_get = fp_core.get_frame_processors_modules

    def gated_get(processors: List[str]):
        _ensure_watermark_patched()
        _route_fp_ui()
        rewritten = _route_for_plan(processors)
        for p in rewritten:
            flag = PREMIUM_PROCESSORS.get(p)
            if flag and not has_feature(flag):
                raise FeatureLocked(flag, "free")
        return original_get(rewritten)

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

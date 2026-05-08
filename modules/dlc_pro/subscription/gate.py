"""Feature-gate decorator + helpers.

Resolution order (cheapest first):
    1. Live LicenseManager.snapshot().feature_flags  (in-memory, fresh)
    2. Verified, signed claims cache on disk         (offline grace window)
    3. Empty                                          (free tier — CPU only)

When a gated function is invoked without entitlement, we raise
:class:`FeatureLocked`. The UI's top-level ``call_with_paywall`` wrapper
catches it and pops the upgrade dialog.

Built-in flags:
    core_cpu             — always on, CPU pipeline
    gpu_inference        — Pro+
    export_4k            — Pro+
    face_enhancer_512    — Pro+
    map_faces            — Studio
    hyperswap_full_head  — Studio
    batch_queue          — Studio
    no_watermark         — Studio
"""
from __future__ import annotations

import functools
import time
from typing import Any, Callable, Iterable, List, Optional, TypeVar

from modules.dlc_pro.async_runner import runner
from modules.dlc_pro.logger import get
from modules.dlc_pro.subscription import claims as claims_mod

log = get("subscription.gate")

F = TypeVar("F", bound=Callable[..., Any])

ALWAYS_FREE = frozenset({"core_cpu"})
HEARTBEAT_INTERVAL_SECONDS = 6 * 3600
_LAST_HEARTBEAT = 0.0


class FeatureLocked(Exception):
    """Raised when gated code runs without entitlement."""

    def __init__(self, feature: str, current_plan: str):
        super().__init__(f"feature '{feature}' requires upgrade (current plan: {current_plan})")
        self.feature = feature
        self.current_plan = current_plan


# ---------- entitlement resolution ----------


def _entitlements() -> List[str]:
    """Resolve the union of currently-granted feature flags."""
    # Lazy import: avoid circular dep with license.manager.
    from modules.dlc_pro.license.manager import LicenseStatus, license_manager

    mgr = license_manager()
    snap = mgr.snapshot()

    if snap.status == LicenseStatus.ACTIVE:
        return list(snap.feature_flags) + list(ALWAYS_FREE)

    if snap.status == LicenseStatus.EXPIRED and time.time() < snap.offline_grace_until:
        c = claims_mod.load()
        if c and c.is_valid:
            return list(c.feature_flags) + list(ALWAYS_FREE)

    return list(ALWAYS_FREE)


def current_plan() -> str:
    from modules.dlc_pro.license.manager import license_manager

    return license_manager().snapshot().plan or "free"


def has_feature(name: str) -> bool:
    # === TEMP TEST UNLOCK — REVERT TO LOCK ===
    # Original line:
    #     return name in _entitlements()
    return True
    # === END TEMP TEST UNLOCK ===


def has_any(names: Iterable[str]) -> bool:
    # === TEMP TEST UNLOCK — REVERT TO LOCK ===
    # Original body:
    #     flags = set(_entitlements())
    #     return any(n in flags for n in names)
    return True
    # === END TEMP TEST UNLOCK ===


def require_feature(name: str) -> Callable[[F], F]:
    """Decorator: raise :class:`FeatureLocked` if ``name`` is not entitled.

    Use on backend / processing entry points, *not* on UI callbacks. UI code
    should call :func:`has_feature` to gray-out controls.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # === TEMP TEST UNLOCK — REVERT TO LOCK ===
            # Original guard:
            #     if not has_feature(name):
            #         plan = current_plan()
            #         log.info("feature locked: %s (plan=%s)", name, plan)
            #         raise FeatureLocked(name, plan)
            # === END TEMP TEST UNLOCK ===
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def call_with_paywall(
    fn: Callable[..., Any],
    *args,
    on_locked: Optional[Callable[[FeatureLocked], None]] = None,
    **kwargs,
):
    """Invoke ``fn``; on :class:`FeatureLocked`, run ``on_locked``.

    Convenience for UI buttons whose handler is already gated server-side
    too: lets us catch in one place and pop the paywall.
    """
    try:
        return fn(*args, **kwargs)
    except FeatureLocked as exc:
        if on_locked:
            on_locked(exc)
        return None


# ---------- background heartbeat trigger ----------


def refresh_claims_async() -> None:
    """Trigger a license heartbeat if we haven't done one recently.

    Idempotent and non-blocking — safe to call from UI ``after`` callbacks.
    """
    global _LAST_HEARTBEAT
    now = time.time()
    if now - _LAST_HEARTBEAT < HEARTBEAT_INTERVAL_SECONDS:
        return
    _LAST_HEARTBEAT = now

    from modules.dlc_pro.license.manager import license_manager

    runner().run(license_manager().heartbeat)

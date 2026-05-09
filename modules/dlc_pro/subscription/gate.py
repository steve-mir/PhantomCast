"""Feature-gate decorator + helpers.

Resolution order (cheapest first):
    1. Live LicenseManager.snapshot().feature_flags  (in-memory, fresh)
    2. Verified, signed claims cache on disk         (offline grace window)
    3. Empty                                          (free tier — CPU only)

When a gated function is invoked without entitlement, we raise
:class:`FeatureLocked`. The UI's top-level ``call_with_paywall`` wrapper
catches it and pops the upgrade dialog.

Two-tier plan model (Free vs Premium). Premium grants every flag below
*except* ``core_cpu``, which is free for everyone.

Built-in flags:
    core_cpu             — always on, free CPU pipeline
    gpu_inference        — Premium
    export_4k            — Premium
    face_enhancer_512    — Premium
    map_faces            — Premium
    hyperswap_full_head  — Premium
    batch_queue          — Premium
    no_watermark         — Premium

A free user may *preview* any premium flag for ``TrialBudget.MAX_SECONDS``
total per session via :func:`try_feature` — the same flag is treated as
entitled while the budget is non-zero.
"""
from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from threading import Lock
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, TypeVar

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

    if snap.status == LicenseStatus.ACTIVE and mgr.is_active():
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
    """True when ``name`` is entitled — either by plan or active preview budget."""
    if name in _entitlements():
        return True
    return TrialBudget.is_active(name)


def has_any(names: Iterable[str]) -> bool:
    flags = set(_entitlements())
    if any(n in flags for n in names):
        return True
    return any(TrialBudget.is_active(n) for n in names)


def require_feature(name: str) -> Callable[[F], F]:
    """Decorator: raise :class:`FeatureLocked` if ``name`` is not entitled.

    Use on backend / processing entry points, *not* on UI callbacks. UI code
    should call :func:`has_feature` to gray-out controls.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_feature(name):
                plan = current_plan()
                log.info("feature locked: %s (plan=%s)", name, plan)
                raise FeatureLocked(name, plan)
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


# ---------- 2-minute premium preview ----------


class TrialBudget:
    """Per-feature, per-session preview window.

    A free user can taste any premium flag for up to :data:`MAX_SECONDS`
    of *active* time per session. The budget is tracked here; the
    free-tier processor router (in ``core_bridge``) inspects
    :meth:`is_active` to decide whether to route to the premium model.

    The budget runs as wall-clock between :meth:`start` and either an
    explicit :meth:`stop` or a check that detects exhaustion. It is
    intentionally simple — per-session only, no persistence — to keep
    the surface small and the user experience generous during testing.
    """

    MAX_SECONDS: float = 120.0

    _lock = Lock()
    _spent: Dict[str, float] = {}     # cumulative seconds consumed
    _started_at: Dict[str, float] = {}  # wall-clock start for the live window
    _exhausted: Dict[str, bool] = {}

    @classmethod
    def remaining(cls, name: str) -> float:
        with cls._lock:
            spent = cls._spent.get(name, 0.0)
            if name in cls._started_at:
                spent += time.time() - cls._started_at[name]
            return max(0.0, cls.MAX_SECONDS - spent)

    @classmethod
    def is_active(cls, name: str) -> bool:
        """True while the feature has remaining budget *and* a window is open.

        ``is_active`` returns False once the budget is exhausted even if
        :meth:`start` hasn't been called yet — that way the router never
        accidentally routes premium to a free user who's already used up
        the window.
        """
        with cls._lock:
            if cls._exhausted.get(name, False):
                return False
            started = name in cls._started_at
        if not started:
            return False
        return cls.remaining(name) > 0

    @classmethod
    def start(cls, name: str) -> bool:
        """Open a preview window; returns False if already exhausted.

        Idempotent — calling :meth:`start` twice in a row is safe.
        """
        with cls._lock:
            if cls._exhausted.get(name, False):
                return False
            if name not in cls._started_at:
                cls._started_at[name] = time.time()
            return True

    @classmethod
    def stop(cls, name: str) -> None:
        """Close the preview window and roll the elapsed time into ``_spent``."""
        with cls._lock:
            started = cls._started_at.pop(name, None)
            if started is not None:
                elapsed = time.time() - started
                cls._spent[name] = cls._spent.get(name, 0.0) + elapsed
                if cls._spent[name] >= cls.MAX_SECONDS:
                    cls._exhausted[name] = True

    @classmethod
    def reset(cls, name: Optional[str] = None) -> None:
        """For tests and `Settings → Reset preview budgets` only."""
        with cls._lock:
            if name is None:
                cls._spent.clear()
                cls._started_at.clear()
                cls._exhausted.clear()
            else:
                cls._spent.pop(name, None)
                cls._started_at.pop(name, None)
                cls._exhausted.pop(name, None)

    @classmethod
    def tick(cls, name: str) -> bool:
        """Sample the budget; if exhausted mid-window, close it.

        Call this from hot paths (e.g. each frame of preview). Returns
        True while the preview is still live.
        """
        with cls._lock:
            if cls._exhausted.get(name, False):
                return False
            started = cls._started_at.get(name)
            if started is None:
                return False
            elapsed = time.time() - started
            spent = cls._spent.get(name, 0.0) + elapsed
        if spent >= cls.MAX_SECONDS:
            cls.stop(name)
            log.info("preview budget exhausted: %s", name)
            return False
        return True


@contextmanager
def try_feature(name: str) -> Iterator[bool]:
    """Open a preview budget window for ``name``.

    Yields True if the user already owns the feature OR has remaining
    preview budget. Yields False once the budget is exhausted; the caller
    is expected to fall back to the free experience and surface a paywall.

    Usage::

        with try_feature("face_enhancer_512") as allowed:
            if allowed:
                run_premium_enhancer()
            else:
                run_free_enhancer()

    Owning the feature outright is a no-op for the budget (no time is
    consumed) so we don't accidentally exhaust premium users.
    """
    if name in _entitlements():
        yield True
        return
    started = TrialBudget.start(name)
    try:
        yield started and TrialBudget.is_active(name)
    finally:
        TrialBudget.stop(name)


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

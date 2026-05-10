"""License lifecycle: activate, validate, refresh, deactivate.

State machine:
    UNACTIVATED  --activate(key)-->  VALIDATING  --ok-->  ACTIVE
                                              \\--err-->  ERROR
    ACTIVE  --heartbeat(ok)-->  ACTIVE
    ACTIVE  --offline > grace--> EXPIRED  (features stripped)
    ANY      --deactivate()-->   UNACTIVATED

Plan model (post two-tier collapse):
    "free"    — UNACTIVATED, no entitlements beyond core_cpu
    "premium" — ACTIVE, all PREMIUM_FEATURES granted

Activation includes a 30-day prepaid trial month. After that, the server
heartbeat must return ``subscribed=true`` (via Stripe) or the manager
downgrades the user to free locally.

The manager never blocks the UI. All network calls go via AsyncRunner and
publish state changes through ``on_change`` callbacks.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from modules.phantom_cast.async_runner import runner
from modules.phantom_cast.license import fingerprint, secure_store
from modules.phantom_cast.license.dev_mode import is_dev_mode
from modules.phantom_cast.logger import get

log = get("license.manager")


class LicenseStatus(str, Enum):
    UNACTIVATED = "unactivated"
    VALIDATING = "validating"
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    ERROR = "error"


class LicenseError(Exception):
    """Raised when activation or validation fails with a user-actionable reason."""


# Single Premium tier — all gated capabilities live here.
PREMIUM_FEATURES: List[str] = [
    "gpu_inference",
    "export_4k",
    "face_enhancer_512",
    "map_faces",
    "hyperswap_full_head",
    "batch_queue",
    "no_watermark",
]

TRIAL_MONTH_SECONDS = 30 * 24 * 3600
OFFLINE_GRACE_SECONDS = 72 * 3600
HEARTBEAT_INTERVAL_SECONDS = 6 * 3600


@dataclass
class LicenseState:
    status: LicenseStatus = LicenseStatus.UNACTIVATED
    license_key: str = ""
    license_id: str = ""
    plan: str = "free"
    fingerprint_hash: str = ""
    last_validated_at: float = 0.0
    offline_grace_until: float = 0.0
    claims_jwt: str = ""
    claims_jwt_exp: float = 0.0
    feature_flags: List[str] = field(default_factory=list)
    last_error: str = ""
    # --- Trial-month + subscription tracking ---
    activated_at: float = 0.0
    trial_month_until: float = 0.0      # day-30 deadline; 0 = no trial month
    subscribed: bool = False             # Stripe sub active (set by heartbeat)
    subscription_url: str = ""           # short-lived portal/checkout URL from server
    # --- Dev-mode tag — fake activations carry this so they're rejected
    # if the next launch happens without PHANTOMCAST_DEV_MODE.
    dev_mode: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "license_key": self.license_key,
            "license_id": self.license_id,
            "plan": self.plan,
            "fingerprint_hash": self.fingerprint_hash,
            "last_validated_at": self.last_validated_at,
            "offline_grace_until": self.offline_grace_until,
            "claims_jwt": self.claims_jwt,
            "claims_jwt_exp": self.claims_jwt_exp,
            "feature_flags": list(self.feature_flags),
            "activated_at": self.activated_at,
            "trial_month_until": self.trial_month_until,
            "subscribed": self.subscribed,
            "subscription_url": self.subscription_url,
            "dev_mode": self.dev_mode,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LicenseState":
        return cls(
            status=LicenseStatus(d.get("status", "unactivated")),
            license_key=d.get("license_key", ""),
            license_id=d.get("license_id", ""),
            plan=d.get("plan", "free"),
            fingerprint_hash=d.get("fingerprint_hash", ""),
            last_validated_at=float(d.get("last_validated_at", 0.0)),
            offline_grace_until=float(d.get("offline_grace_until", 0.0)),
            claims_jwt=d.get("claims_jwt", ""),
            claims_jwt_exp=float(d.get("claims_jwt_exp", 0.0)),
            feature_flags=list(d.get("feature_flags", [])),
            activated_at=float(d.get("activated_at", 0.0)),
            trial_month_until=float(d.get("trial_month_until", 0.0)),
            subscribed=bool(d.get("subscribed", False)),
            subscription_url=d.get("subscription_url", ""),
            dev_mode=bool(d.get("dev_mode", False)),
        )


class LicenseManager:
    def __init__(self) -> None:
        self._state = LicenseState()
        self._lock = Lock()
        self._listeners: List[Callable[[LicenseState], None]] = []
        self._tk_root: Any = None
        self._load()

    # ---------- listeners ----------

    def bind_root(self, tk_root: Any) -> None:
        """Bind a Tk root so async callbacks marshal back to the UI thread."""
        self._tk_root = tk_root

    def on_change(self, fn: Callable[[LicenseState], None]) -> None:
        self._listeners.append(fn)

    def _emit(self) -> None:
        snap = self.snapshot()
        for fn in list(self._listeners):
            try:
                fn(snap)
            except Exception:  # noqa: BLE001 — listener errors must not break license
                log.exception("listener failed")

    # ---------- snapshot / queries ----------

    def snapshot(self) -> LicenseState:
        with self._lock:
            return LicenseState.from_dict(self._state.to_dict())

    def is_active(self) -> bool:
        s = self.snapshot()
        if s.status == LicenseStatus.ACTIVE:
            # Active but past the 30-day trial with no Stripe sub → treat
            # as not active. The features() set will be empty.
            if s.trial_month_until and time.time() > s.trial_month_until and not s.subscribed:
                return False
            return True
        if s.status == LicenseStatus.EXPIRED and time.time() < s.offline_grace_until:
            return True
        return False

    def features(self) -> List[str]:
        return self.snapshot().feature_flags if self.is_active() else []

    def trial_days_remaining(self) -> Optional[int]:
        """Whole days left in the prepaid trial month (None if not in one)."""
        s = self.snapshot()
        if not s.trial_month_until or s.subscribed:
            return None
        remaining = s.trial_month_until - time.time()
        if remaining <= 0:
            return 0
        return max(1, int(remaining // 86400))

    def trial_lapsed(self) -> bool:
        """True if the 30-day trial expired and no subscription is on file."""
        s = self.snapshot()
        if not s.trial_month_until:
            return False
        return time.time() > s.trial_month_until and not s.subscribed

    # ---------- persistence ----------

    def _load(self) -> None:
        d = secure_store.load()
        if not d:
            return
        try:
            with self._lock:
                state = LicenseState.from_dict(d)
            # Reject stale dev-mode state when running outside dev mode.
            if state.dev_mode and not is_dev_mode():
                log.warning("dev-mode activation found but PHANTOMCAST_DEV_MODE is off; clearing")
                secure_store.clear()
                return
            with self._lock:
                self._state = state
            log.info(
                "license loaded: status=%s plan=%s dev_mode=%s",
                self._state.status.value, self._state.plan, self._state.dev_mode,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("license state corrupt; ignoring: %s", e)

    def _persist(self) -> None:
        secure_store.save(self._state.to_dict())

    # ---------- activation ----------

    def activate(
        self,
        license_key: str,
        on_done: Optional[Callable[[LicenseState], None]] = None,
        on_error: Optional[Callable[[LicenseError], None]] = None,
    ) -> None:
        license_key = (license_key or "").strip()
        if not license_key:
            err = LicenseError("Please enter a license key.")
            if on_error:
                on_error(err)
            return

        # Dev mode: skip Firebase entirely, grant Premium locally.
        if is_dev_mode():
            log.info("PHANTOMCAST_DEV_MODE active — accepting any key as Premium")
            snap = self._activate_dev(license_key)
            self._emit()
            if on_done:
                on_done(snap)
            return

        with self._lock:
            self._state.status = LicenseStatus.VALIDATING
            self._state.license_key = license_key
            self._state.last_error = ""
        self._emit()

        runner().run(
            lambda: self._do_activate(license_key),
            on_done=lambda s: self._after_activate(s, on_done),
            on_error=lambda e, tb: self._after_error(e, on_error),
            tk_root=self._tk_root,
        )

    def start_free_trial(self) -> LicenseState:
        """Persist an explicit Free state. Idempotent.

        Used by the first-run wizard's "Start Free Trial" button so the
        free state survives restarts (otherwise the wizard would re-prompt
        every launch).
        """
        with self._lock:
            self._state = LicenseState(
                status=LicenseStatus.UNACTIVATED,
                plan="free",
            )
            self._persist()
            snap = LicenseState.from_dict(self._state.to_dict())
        self._emit()
        log.info("free trial started (no activation key)")
        return snap

    def _activate_dev(self, license_key: str) -> LicenseState:
        """Build a fake Premium state without contacting Firebase."""
        now = time.time()
        with self._lock:
            self._state = LicenseState(
                status=LicenseStatus.ACTIVE,
                license_key=license_key,
                license_id=f"dev_{uuid.uuid4().hex[:12]}",
                plan="premium",
                fingerprint_hash=fingerprint.collect().composite(),
                last_validated_at=now,
                offline_grace_until=now + OFFLINE_GRACE_SECONDS,
                claims_jwt="",
                claims_jwt_exp=now + TRIAL_MONTH_SECONDS,
                feature_flags=list(PREMIUM_FEATURES),
                activated_at=now,
                trial_month_until=now + TRIAL_MONTH_SECONDS,
                subscribed=False,
                dev_mode=True,
            )
            self._persist()
            return LicenseState.from_dict(self._state.to_dict())

    def _do_activate(self, license_key: str) -> LicenseState:
        from modules.phantom_cast.firebase.client import FirebaseClient

        fp = fingerprint.collect()
        client = FirebaseClient()
        resp = client.activate(
            license_key=license_key,
            composite_hash=fp.composite(),
            component_hashes=fp.hashed(),
            os_release=fp.os_release,
        )
        # resp = { license_id, plan, claims_jwt, claims_exp, feature_flags,
        #          subscribed?, subscription_url?, trial_month_until? }
        now = time.time()
        with self._lock:
            self._state.status = LicenseStatus.ACTIVE
            self._state.license_id = resp["license_id"]
            # Server may legacy-return "pro" or "studio"; collapse to "premium".
            self._state.plan = _normalize_plan(resp.get("plan", "premium"))
            self._state.fingerprint_hash = fp.composite()
            self._state.last_validated_at = now
            self._state.offline_grace_until = now + OFFLINE_GRACE_SECONDS
            self._state.claims_jwt = resp["claims_jwt"]
            self._state.claims_jwt_exp = float(resp["claims_exp"])
            # Premium == full feature set; trust the server only if it sent more.
            server_flags = list(resp.get("feature_flags", []))
            self._state.feature_flags = (
                server_flags if server_flags else list(PREMIUM_FEATURES)
            )
            self._state.activated_at = now
            self._state.trial_month_until = float(
                resp.get("trial_month_until", now + TRIAL_MONTH_SECONDS)
            )
            self._state.subscribed = bool(resp.get("subscribed", False))
            self._state.subscription_url = resp.get("subscription_url", "")
            self._state.dev_mode = False
            self._persist()
            return LicenseState.from_dict(self._state.to_dict())

    def _after_activate(self, snap: LicenseState, on_done) -> None:
        log.info("activation succeeded: plan=%s flags=%s", snap.plan, snap.feature_flags)
        self._emit()
        if on_done:
            on_done(snap)

    def _after_error(self, exc: BaseException, on_error) -> None:
        msg = str(exc) or exc.__class__.__name__
        with self._lock:
            self._state.status = LicenseStatus.ERROR
            self._state.last_error = msg
            self._persist()
        self._emit()
        log.error("activation failed: %s", msg)
        if on_error:
            on_error(LicenseError(msg))

    # ---------- heartbeat ----------

    def heartbeat(self) -> None:
        snap = self.snapshot()
        if snap.status not in (LicenseStatus.ACTIVE, LicenseStatus.EXPIRED):
            return
        if snap.dev_mode:
            # Don't try to phone home for dev-mode fakes.
            return
        runner().run(
            lambda: self._do_heartbeat(snap),
            on_done=lambda r: (self._emit() if r else None),
            on_error=lambda e, tb: self._mark_offline_grace(e),
            tk_root=self._tk_root,
        )

    def _do_heartbeat(self, snap: LicenseState) -> bool:
        from modules.phantom_cast.firebase.client import FirebaseClient

        client = FirebaseClient()
        resp = client.heartbeat(
            license_id=snap.license_id,
            license_key=snap.license_key,
            composite_hash=snap.fingerprint_hash,
        )
        now = time.time()
        with self._lock:
            self._state.status = LicenseStatus(resp.get("status", "active"))
            self._state.plan = _normalize_plan(resp.get("plan", self._state.plan))
            server_flags = list(resp.get("feature_flags", self._state.feature_flags))
            self._state.feature_flags = server_flags
            self._state.claims_jwt = resp.get("claims_jwt", self._state.claims_jwt)
            self._state.claims_jwt_exp = float(resp.get("claims_exp", self._state.claims_jwt_exp))
            self._state.last_validated_at = now
            self._state.offline_grace_until = now + OFFLINE_GRACE_SECONDS
            self._state.subscribed = bool(resp.get("subscribed", self._state.subscribed))
            if "subscription_url" in resp:
                self._state.subscription_url = resp.get("subscription_url", "")
            if "trial_month_until" in resp:
                self._state.trial_month_until = float(resp["trial_month_until"])
            # If the trial month has lapsed and no subscription is on file,
            # strip entitlements locally — server should agree but be defensive.
            if (
                self._state.trial_month_until
                and now > self._state.trial_month_until
                and not self._state.subscribed
            ):
                self._state.feature_flags = []
                self._state.plan = "free"
            self._persist()
        return True

    def _mark_offline_grace(self, exc: BaseException) -> None:
        log.warning("heartbeat offline: %s", exc)
        with self._lock:
            if time.time() > self._state.offline_grace_until:
                self._state.status = LicenseStatus.EXPIRED
                self._state.feature_flags = []
                self._persist()
        self._emit()

    # ---------- deactivate ----------

    def deactivate(self) -> None:
        snap = self.snapshot()
        # Dev-mode fakes never hit the server.
        if snap.dev_mode:
            self._after_deactivate()
            return
        runner().run(
            lambda: self._do_deactivate(snap),
            on_done=lambda _: self._after_deactivate(),
            on_error=lambda e, tb: self._after_deactivate(),
            tk_root=self._tk_root,
        )

    def _do_deactivate(self, snap: LicenseState) -> None:
        from modules.phantom_cast.firebase.client import FirebaseClient

        if snap.license_id and snap.license_key:
            try:
                FirebaseClient().deactivate(
                    license_id=snap.license_id,
                    license_key=snap.license_key,
                    composite_hash=snap.fingerprint_hash,
                )
            except Exception as e:  # noqa: BLE001 — best-effort
                log.warning("server deactivate failed: %s", e)

    def _after_deactivate(self) -> None:
        with self._lock:
            self._state = LicenseState()
            secure_store.clear()
        self._emit()
        log.info("deactivated")


def _normalize_plan(plan: str) -> str:
    """Collapse legacy server plan names into the two-tier model."""
    p = (plan or "free").strip().lower()
    if p in ("pro", "studio", "premium", "premium_plus"):
        return "premium"
    return "free"


# Module singleton
_singleton: Optional[LicenseManager] = None


def license_manager() -> LicenseManager:
    global _singleton
    if _singleton is None:
        _singleton = LicenseManager()
    return _singleton

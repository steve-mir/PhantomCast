"""License lifecycle: activate, validate, refresh, deactivate.

State machine:
    UNACTIVATED  --activate(key)-->  VALIDATING  --ok-->  ACTIVE
                                              \\--err-->  ERROR
    ACTIVE  --heartbeat(ok)-->  ACTIVE
    ACTIVE  --offline > grace--> EXPIRED  (features stripped)
    ANY      --deactivate()-->   UNACTIVATED

The manager never blocks the UI. All network calls go via AsyncRunner and
publish state changes through ``on_change`` callbacks.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from modules.dlc_pro.async_runner import runner
from modules.dlc_pro.license import fingerprint, secure_store
from modules.dlc_pro.logger import get

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
        )


OFFLINE_GRACE_SECONDS = 72 * 3600
HEARTBEAT_INTERVAL_SECONDS = 6 * 3600


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
            return True
        if s.status == LicenseStatus.EXPIRED and time.time() < s.offline_grace_until:
            return True
        return False

    def features(self) -> List[str]:
        return self.snapshot().feature_flags if self.is_active() else []

    # ---------- persistence ----------

    def _load(self) -> None:
        d = secure_store.load()
        if not d:
            return
        try:
            with self._lock:
                self._state = LicenseState.from_dict(d)
            log.info("license loaded: status=%s plan=%s", self._state.status.value,
                     self._state.plan)
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

    def _do_activate(self, license_key: str) -> LicenseState:
        from modules.dlc_pro.firebase.client import FirebaseClient

        fp = fingerprint.collect()
        client = FirebaseClient()
        resp = client.activate(
            license_key=license_key,
            composite_hash=fp.composite(),
            component_hashes=fp.hashed(),
            os_release=fp.os_release,
        )
        # resp = { license_id, plan, claims_jwt, claims_exp, feature_flags }
        now = time.time()
        with self._lock:
            self._state.status = LicenseStatus.ACTIVE
            self._state.license_id = resp["license_id"]
            self._state.plan = resp["plan"]
            self._state.fingerprint_hash = fp.composite()
            self._state.last_validated_at = now
            self._state.offline_grace_until = now + OFFLINE_GRACE_SECONDS
            self._state.claims_jwt = resp["claims_jwt"]
            self._state.claims_jwt_exp = float(resp["claims_exp"])
            self._state.feature_flags = list(resp.get("feature_flags", []))
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
        runner().run(
            lambda: self._do_heartbeat(snap),
            on_done=lambda r: (self._emit() if r else None),
            on_error=lambda e, tb: self._mark_offline_grace(e),
            tk_root=self._tk_root,
        )

    def _do_heartbeat(self, snap: LicenseState) -> bool:
        from modules.dlc_pro.firebase.client import FirebaseClient

        client = FirebaseClient()
        resp = client.heartbeat(
            license_id=snap.license_id,
            license_key=snap.license_key,
            composite_hash=snap.fingerprint_hash,
        )
        now = time.time()
        with self._lock:
            self._state.status = LicenseStatus(resp.get("status", "active"))
            self._state.plan = resp.get("plan", self._state.plan)
            self._state.feature_flags = list(resp.get("feature_flags", self._state.feature_flags))
            self._state.claims_jwt = resp.get("claims_jwt", self._state.claims_jwt)
            self._state.claims_jwt_exp = float(resp.get("claims_exp", self._state.claims_jwt_exp))
            self._state.last_validated_at = now
            self._state.offline_grace_until = now + OFFLINE_GRACE_SECONDS
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
        runner().run(
            lambda: self._do_deactivate(snap),
            on_done=lambda _: self._after_deactivate(),
            on_error=lambda e, tb: self._after_deactivate(),
            tk_root=self._tk_root,
        )

    def _do_deactivate(self, snap: LicenseState) -> None:
        from modules.dlc_pro.firebase.client import FirebaseClient

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


# Module singleton
_singleton: Optional[LicenseManager] = None


def license_manager() -> LicenseManager:
    global _singleton
    if _singleton is None:
        _singleton = LicenseManager()
    return _singleton

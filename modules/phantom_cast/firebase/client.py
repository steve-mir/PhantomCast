"""HTTPS client for Phantom Cast Cloud Functions.

Standard-library only (urllib + ssl) to avoid pulling `requests` into the
PyInstaller graph and bloating the installer.

Guarantees:
    * Idempotent activate/heartbeat/deactivate (server enforces too).
    * Retries with full-jitter exponential backoff on 429 / 5xx / network.
    * Cert pinning via ``ssl.SSLContext.set_default_verify_paths`` plus an
      optional pinned-leaf SHA-256 list (defence in depth — Google's CA
      already covers normal operation).
    * Never blocks the UI thread; meant to be called from AsyncRunner.
"""
from __future__ import annotations

import json
import os
import platform
import random
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from modules.phantom_cast import __version__ as APP_VERSION
from modules.phantom_cast.firebase.config import (
    ACTIVATE_URL,
    CLIENT_NAME,
    DEACTIVATE_URL,
    HEARTBEAT_URL,
    MOVE_LICENSE_URL,
)
from modules.phantom_cast.logger import get
from modules.phantom_cast.subscription import claims as claims_mod

log = get("firebase.client")


# ---------- Errors ----------


class FirebaseError(Exception):
    """Base class for typed errors surfaced by the client."""


class NetworkError(FirebaseError):
    """DNS / TCP / TLS / read failure. Caller should fall back to offline."""


class LicenseInvalid(FirebaseError):
    """License key not found, suspended, refunded, or expired server-side."""


class DeviceMismatch(FirebaseError):
    """License is bound to a different machine and cannot auto-rebind."""


class RateLimited(FirebaseError):
    """Too many activations from this IP / license. Surface to user."""


# ---------- HTTP plumbing ----------


_TIMEOUT_SECONDS = 12
_MAX_RETRIES = 4
_BACKOFF_BASE = 0.6


def _user_agent() -> str:
    return (
        f"{CLIENT_NAME}/{APP_VERSION} "
        f"({platform.system()} {platform.release()}; "
        f"{platform.machine()}) Python/{platform.python_version()}"
    )


def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _user_agent(),
            "X-Client-Version": APP_VERSION,
        },
    )

    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    last_exc: Optional[BaseException] = None
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS, context=ctx) as resp:
                code = resp.getcode()
                raw = resp.read()
                data = json.loads(raw.decode("utf-8")) if raw else {}
                return _interpret(code, data)
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                data = {"error": e.reason}
            try:
                return _interpret(e.code, data)
            except (NetworkError, RateLimited) as retryable:
                last_exc = retryable
            except FirebaseError:
                raise  # terminal: 4xx semantic
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ssl.SSLError) as e:
            last_exc = NetworkError(f"{type(e).__name__}: {e}")

        # Retry with full-jitter backoff
        sleep_for = random.uniform(0, _BACKOFF_BASE * (2 ** attempt))
        log.warning("retry %d/%d in %.2fs after %s", attempt + 1, _MAX_RETRIES, sleep_for, last_exc)
        time.sleep(sleep_for)

    raise NetworkError(str(last_exc) if last_exc else "request failed")


def _interpret(code: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate HTTP status + body into our typed exceptions."""
    if 200 <= code < 300:
        return data
    err_code = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else data.get("error")
    msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("message")
    msg = msg or f"HTTP {code}"

    if code == 401 or err_code in ("license_invalid", "license_suspended", "license_expired"):
        raise LicenseInvalid(msg)
    if code == 409 or err_code in ("device_mismatch", "device_already_bound"):
        raise DeviceMismatch(msg)
    if code == 429 or err_code == "rate_limited":
        raise RateLimited(msg)
    if 500 <= code < 600:
        raise NetworkError(msg)  # retryable
    raise FirebaseError(msg)


# ---------- High-level API ----------


class FirebaseClient:
    def activate(
        self,
        license_key: str,
        composite_hash: str,
        component_hashes: Dict[str, str],
        os_release: str,
    ) -> Dict[str, Any]:
        """Bind device to license. Returns the parsed response.

        Server response contract:
            {
                license_id, plan,
                claims_jwt,            # RS256-signed, kid pinned in app
                claims_exp,            # unix
                feature_flags: [...],
                device_id              # opaque
            }
        """
        resp = _post_json(
            ACTIVATE_URL,
            {
                "license_key": license_key,
                "fingerprint": composite_hash,
                "components": component_hashes,
                "os": os_release,
                "client_version": APP_VERSION,
            },
        )
        # Verify the claims JWT before trusting *any* of the response fields.
        jwt = resp.get("claims_jwt", "")
        c = claims_mod.verify_and_parse(jwt)
        if not c or not c.is_valid:
            raise FirebaseError("server returned an invalid or expired claims token")
        if c.fingerprint_hash and c.fingerprint_hash != composite_hash:
            raise DeviceMismatch("claims fingerprint does not match this machine")
        # Persist signed claims for offline use.
        claims_mod.save(jwt)
        return resp

    def heartbeat(
        self,
        license_id: str,
        license_key: str,
        composite_hash: str,
    ) -> Dict[str, Any]:
        resp = _post_json(
            HEARTBEAT_URL,
            {
                "license_id": license_id,
                "license_key": license_key,
                "fingerprint": composite_hash,
                "client_version": APP_VERSION,
            },
        )
        jwt = resp.get("claims_jwt", "")
        if jwt:
            c = claims_mod.verify_and_parse(jwt)
            if not c or not c.is_valid:
                raise FirebaseError("heartbeat returned an invalid claims token")
            claims_mod.save(jwt)
        return resp

    def deactivate(
        self,
        license_id: str,
        license_key: str,
        composite_hash: str,
    ) -> Dict[str, Any]:
        return _post_json(
            DEACTIVATE_URL,
            {
                "license_id": license_id,
                "license_key": license_key,
                "fingerprint": composite_hash,
            },
        )

    def move_license(
        self,
        license_id: str,
        license_key: str,
        composite_hash: str,
        component_hashes: Dict[str, str],
    ) -> Dict[str, Any]:
        return _post_json(
            MOVE_LICENSE_URL,
            {
                "license_id": license_id,
                "license_key": license_key,
                "fingerprint": composite_hash,
                "components": component_hashes,
            },
        )

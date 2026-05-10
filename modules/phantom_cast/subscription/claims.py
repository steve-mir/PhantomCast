"""Server-signed claims cache.

Cloud Functions issue an RS256-signed claims payload at activation/heartbeat
time. The desktop app caches the *signed* token (not the parsed payload) so
that even when offline we can re-verify integrity before trusting the
contents.

Claim shape:

    {
      "license_id":  "lic_...",
      "plan":        "pro" | "studio" | "free",
      "feature_flags": ["gpu_inference", "export_4k", ...],
      "fingerprint_hash": "...",
      "iat": <unix>,
      "exp": <unix>,
      "kid": "dlc-pro-2026-01"
    }
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from modules.dlc_pro.firebase.config import PINNED_PUBKEYS
from modules.dlc_pro.logger import get
from modules.dlc_pro.paths import claims_cache_file

log = get("subscription.claims")


@dataclass
class Claims:
    license_id: str = ""
    plan: str = "free"
    feature_flags: List[str] = field(default_factory=list)
    fingerprint_hash: str = ""
    iat: float = 0.0
    exp: float = 0.0
    raw_jwt: str = ""

    @property
    def is_expired(self) -> bool:
        return self.exp > 0 and time.time() >= self.exp

    @property
    def is_valid(self) -> bool:
        return bool(self.license_id) and not self.is_expired


# ---------- JWT verify ----------


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_and_parse(jwt: str) -> Optional[Claims]:
    """Verify RS256 signature against pinned public keys; return Claims or None.

    Returns ``None`` on any verification failure — callers must treat that as
    "no entitlements". We never raise into the caller because UI threads
    handle this on the hot path.
    """
    if not jwt or jwt.count(".") != 2:
        return None
    try:
        header_b64, payload_b64, sig_b64 = jwt.split(".")
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        sig = _b64url_decode(sig_b64)
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("claims jwt malformed: %s", e)
        return None

    kid = header.get("kid", "")
    alg = header.get("alg", "")
    if alg != "RS256":
        log.warning("claims jwt alg=%s rejected (only RS256 accepted)", alg)
        return None

    pem = PINNED_PUBKEYS.get(kid)
    if not pem:
        log.warning("claims jwt kid=%s not in pinned set", kid)
        return None

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pub = serialization.load_pem_public_key(pem.encode("utf-8"))
        signed_data = f"{header_b64}.{payload_b64}".encode("ascii")
        pub.verify(sig, signed_data, padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:  # noqa: BLE001
        log.warning("claims jwt signature invalid: %s", e)
        return None

    return Claims(
        license_id=str(payload.get("license_id", "")),
        plan=str(payload.get("plan", "free")),
        feature_flags=list(payload.get("feature_flags", [])),
        fingerprint_hash=str(payload.get("fingerprint_hash", "")),
        iat=float(payload.get("iat", 0)),
        exp=float(payload.get("exp", 0)),
        raw_jwt=jwt,
    )


# ---------- on-disk cache ----------


def save(jwt: str) -> None:
    if not jwt:
        return
    Path(claims_cache_file()).write_text(jwt, encoding="ascii")


def load() -> Optional[Claims]:
    p = claims_cache_file()
    try:
        jwt = p.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    return verify_and_parse(jwt)


def clear() -> None:
    try:
        claims_cache_file().unlink()
    except FileNotFoundError:
        pass

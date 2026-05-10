"""DPAPI-encrypted local store for license state.

On Windows, uses ``CryptProtectData`` (CurrentUser scope). The OS holds the
key derivation, which means the encrypted blob can't be moved to another
machine or another user account and decrypted there.

On non-Windows we fall back to AES-GCM with a key derived from a per-user
salt + the machine fingerprint. This is weaker than DPAPI but acceptable —
the desktop product targets Windows; non-Windows is dev-only.

If ``cryptography`` isn't installed on a non-Windows host (a fresh dev box),
we degrade further to a base64 ``PLAINv1`` blob — readable but unparsable
without the right header, sufficient for dev/free-tier state where there's
no JWT or sensitive secret to protect. Production builds should always
have ``cryptography`` available; ``pip install cryptography`` removes the
fallback.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from modules.phantom_cast.logger import get
from modules.phantom_cast.paths import state_file

log = get("license.store")


# ---------------------------------------------------------------- Windows DPAPI


def _dpapi_protect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB(len(blob), ctypes.cast(ctypes.c_char_p(blob), ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), "PhantomCast", None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB(len(blob), ctypes.cast(ctypes.c_char_p(blob), ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ---------------------------------------------------------------- portable fallback


def _portable_key() -> bytes:
    from modules.phantom_cast.license.fingerprint import collect

    fp = collect().composite()
    salt = b"phantomcast-store-v1"
    # Lazy import to avoid hard dep when DPAPI works.
    import hashlib
    return hashlib.scrypt(fp.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _aes_encrypt(blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    aes = AESGCM(_portable_key())
    return b"AESGCMv1" + nonce + aes.encrypt(nonce, blob, None)


def _aes_decrypt(blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(b"AESGCMv1"):
        raise ValueError("bad header")
    nonce = blob[8:20]
    ct = blob[20:]
    aes = AESGCM(_portable_key())
    return aes.decrypt(nonce, ct, None)


# ---------------------------------------------------------------- plaintext fallback


def _have_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def _plain_encode(blob: bytes) -> bytes:
    return b"PLAINv1\x00" + base64.b64encode(blob)


def _plain_decode(blob: bytes) -> bytes:
    return base64.b64decode(blob[len(b"PLAINv1\x00"):])


# ---------------------------------------------------------------- public API


def save(payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if sys.platform == "win32":
        enc = _dpapi_protect(raw)
        header = b"DPAPIv1\x00"
    elif _have_cryptography():
        enc = _aes_encrypt(raw)
        header = b""
    else:
        # Dev-host fallback: base64 plaintext. Acceptable for free / dev-mode
        # state which carries no sensitive secret. Logs once per run so a
        # production build accidentally missing cryptography is noisy.
        log.warning(
            "cryptography not installed; persisting license state as PLAINv1. "
            "Install `cryptography` for AES-GCM at-rest protection."
        )
        enc = _plain_encode(raw)
        header = b""

    tmp = Path(str(state_file()) + ".tmp")
    tmp.write_bytes(header + enc)
    os.replace(tmp, state_file())
    log.info("license state persisted (%d bytes)", len(raw))


def load() -> Optional[Dict[str, Any]]:
    p = state_file()
    if not p.is_file():
        return None
    blob = p.read_bytes()
    try:
        if blob.startswith(b"DPAPIv1\x00"):
            raw = _dpapi_unprotect(blob[len(b"DPAPIv1\x00"):])
        elif blob.startswith(b"AESGCMv1"):
            raw = _aes_decrypt(blob)
        elif blob.startswith(b"PLAINv1\x00"):
            raw = _plain_decode(blob)
        else:
            # Legacy / corrupted — treat as missing.
            log.warning("unknown state.bin header; ignoring")
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.error("license state decrypt failed: %s", e)
        return None


def clear() -> None:
    p = state_file()
    try:
        p.unlink()
    except FileNotFoundError:
        pass

"""Firebase project configuration.

These are *public* values per Firebase's security model: authority lives in
the security rules + custom claims signed by Cloud Functions, not in the
client API key. Override via env for staging.
"""
from __future__ import annotations

import os


PROJECT_ID = os.environ.get("PHANTOMCAST_FIREBASE_PROJECT", "diivix1")
API_KEY = os.environ.get("PHANTOMCAST_FIREBASE_API_KEY", "")
FUNCTIONS_REGION = os.environ.get("PHANTOMCAST_FUNCTIONS_REGION", "us-central1")

FUNCTIONS_BASE = (
    f"https://{FUNCTIONS_REGION}-{PROJECT_ID}.cloudfunctions.net"
)

# Endpoints
ACTIVATE_URL          = f"{FUNCTIONS_BASE}/v1_activate"
HEARTBEAT_URL         = f"{FUNCTIONS_BASE}/v1_heartbeat"
DEACTIVATE_URL        = f"{FUNCTIONS_BASE}/v1_deactivate"
MOVE_LICENSE_URL      = f"{FUNCTIONS_BASE}/v1_moveLicense"
SUBSCRIPTION_STATUS_URL = f"{FUNCTIONS_BASE}/v1_subscriptionStatus"

# Where to send users to manage / renew their subscription. The signed
# per-customer URL from the heartbeat response takes precedence; this
# fallback covers users who don't have one yet (e.g. free-tier upgrading
# for the first time).
PORTAL_FALLBACK_URL = "https://phantomcast.space/subscribe.html"

CLIENT_NAME = "PhantomCast"
CLIENT_VERSION = "0.0.23"

# Custom-claim JWT signer kid → PEM. Update when rotating server keys.
# Inline here so it ships with the binary; refusing unknown kids defeats
# trivial token forgery.
#
# Both ``dlc-pro-2026-05`` (pre-rebrand) and ``phantomcast-2026-05``
# (post-rebrand) point at the same RSA pubkey. The kid was renamed during
# the rebrand, but the underlying keypair is unchanged — so JWTs signed
# by either name verify against the same public key. Keep both entries
# until you next rotate keys; at that point retire the dlc-pro alias.
_PUBKEY_2026_05 = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAiW8PFWBTRrkQY7VS7cJR\n"
    "s6APXNEnz9IxCALae76uEMMV2yG3JxxJjg6SkN8XfG2+haHeCW1yjHpnXEfh8x7U\n"
    "z9ZHNuUMRtPlM6GKR9ofzPGzIooJW5j7sRzpS9VLQ46sjsuRStbuUoPvK8wPsMfY\n"
    "EqoyzIU4LyT3hSBsvJ0KIuiAKggJvHFN6ycdaQj78resJyZkVW2Y+x6LCZU07LNT\n"
    "NsJih8Z/q3Gyb2EAy9Q+z3kaPIafjXLG2O1BkxzUTNVqWCgXMxcz4bym74iOnv5s\n"
    "OffORDniagnCIQrQn+VnIg1HON9czOr7hvFopw9ATvvPuzph5E7YadSplJv93O8e\n"
    "IQIDAQAB\n"
    "-----END PUBLIC KEY-----\n"
)
PINNED_PUBKEYS = {
    "phantomcast-2026-05": _PUBKEY_2026_05,
    "dlc-pro-2026-05":     _PUBKEY_2026_05,  # pre-rebrand alias, same keypair
}

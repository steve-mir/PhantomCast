"""Firebase project configuration.

These are *public* values per Firebase's security model: authority lives in
the security rules + custom claims signed by Cloud Functions, not in the
client API key. Override via env for staging.
"""
from __future__ import annotations

import os


PROJECT_ID = os.environ.get("DLCPRO_FIREBASE_PROJECT", "diivix1")
API_KEY = os.environ.get("DLCPRO_FIREBASE_API_KEY", "")
FUNCTIONS_REGION = os.environ.get("DLCPRO_FUNCTIONS_REGION", "us-central1")

FUNCTIONS_BASE = (
    f"https://{FUNCTIONS_REGION}-{PROJECT_ID}.cloudfunctions.net"
)

# Endpoints
ACTIVATE_URL          = f"{FUNCTIONS_BASE}/v1_activate"
HEARTBEAT_URL         = f"{FUNCTIONS_BASE}/v1_heartbeat"
DEACTIVATE_URL        = f"{FUNCTIONS_BASE}/v1_deactivate"
MOVE_LICENSE_URL      = f"{FUNCTIONS_BASE}/v1_moveLicense"
SUBSCRIPTION_STATUS_URL = f"{FUNCTIONS_BASE}/v1_subscriptionStatus"

# Where to send users to manage / renew their subscription. NOWPayments
# doesn't ship a customer portal yet, so for now this points at the public
# pricing page; replace once you have a per-customer dashboard URL.
PORTAL_FALLBACK_URL = "https://us-central1-diivix1.cloudfunctions.net/"  # TODO: real portal

CLIENT_NAME = "DeepLiveCamPro"
CLIENT_VERSION = "1.0.0"

# Custom-claim JWT signer kid → PEM. Update when rotating server keys.
# Inline here so it ships with the binary; refusing unknown kids defeats
# trivial token forgery.
PINNED_PUBKEYS = {
    "dlc-pro-2026-05": (
        "-----BEGIN PUBLIC KEY-----\n"
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAiW8PFWBTRrkQY7VS7cJR\n"
        "s6APXNEnz9IxCALae76uEMMV2yG3JxxJjg6SkN8XfG2+haHeCW1yjHpnXEfh8x7U\n"
        "z9ZHNuUMRtPlM6GKR9ofzPGzIooJW5j7sRzpS9VLQ46sjsuRStbuUoPvK8wPsMfY\n"
        "EqoyzIU4LyT3hSBsvJ0KIuiAKggJvHFN6ycdaQj78resJyZkVW2Y+x6LCZU07LNT\n"
        "NsJih8Z/q3Gyb2EAy9Q+z3kaPIafjXLG2O1BkxzUTNVqWCgXMxcz4bym74iOnv5s\n"
        "OffORDniagnCIQrQn+VnIg1HON9czOr7hvFopw9ATvvPuzph5E7YadSplJv93O8e\n"
        "IQIDAQAB\n"
        "-----END PUBLIC KEY-----\n"
    ),
}

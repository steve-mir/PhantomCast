"""Firebase project configuration.

These are *public* values per Firebase's security model: authority lives in
the security rules + custom claims signed by Cloud Functions, not in the
client API key. Override via env for staging.
"""
from __future__ import annotations

import os


PROJECT_ID = os.environ.get("DLCPRO_FIREBASE_PROJECT", "Phantom-Cast-pro")
API_KEY = os.environ.get("DLCPRO_FIREBASE_API_KEY", "AIzaSy-REPLACE-WITH-YOUR-KEY")
FUNCTIONS_REGION = os.environ.get("DLCPRO_FUNCTIONS_REGION", "us-central1")

FUNCTIONS_BASE = (
    f"https://{FUNCTIONS_REGION}-{PROJECT_ID}.cloudfunctions.net"
)

# Endpoints
ACTIVATE_URL = f"{FUNCTIONS_BASE}/v1_activate"
HEARTBEAT_URL = f"{FUNCTIONS_BASE}/v1_heartbeat"
DEACTIVATE_URL = f"{FUNCTIONS_BASE}/v1_deactivate"
MOVE_LICENSE_URL = f"{FUNCTIONS_BASE}/v1_moveLicense"

# Stripe customer portal — populated server-side and returned in heartbeat
PORTAL_FALLBACK_URL = "https://billing.stripe.com/p/login/REPLACE-WITH-YOURS"

CLIENT_NAME = "DeepLiveCamPro"
CLIENT_VERSION = "1.0.0"

# Custom-claim JWT signer kid → PEM. Update when rotating server keys.
# Inline here so it ships with the binary; refusing unknown kids defeats
# trivial token forgery.
PINNED_PUBKEYS = {
    "dlc-pro-2026-01": (
        "-----BEGIN PUBLIC KEY-----\n"
        "REPLACE_WITH_REAL_RS256_PUBLIC_KEY\n"
        "-----END PUBLIC KEY-----\n"
    ),
}

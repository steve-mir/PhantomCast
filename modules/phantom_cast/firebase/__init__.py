"""Firebase REST/Functions client wrapper used by license + subscription."""

from modules.phantom_cast.firebase.client import (
    DeviceMismatch,
    FirebaseClient,
    FirebaseError,
    LicenseInvalid,
    NetworkError,
    RateLimited,
)

__all__ = [
    "FirebaseClient",
    "FirebaseError",
    "NetworkError",
    "LicenseInvalid",
    "DeviceMismatch",
    "RateLimited",
]

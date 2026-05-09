"""Developer-mode license bypass.

When dev mode is on, the activation flow short-circuits server validation:

    * Any non-empty activation key is accepted and grants a full Premium
      plan locally (no Firebase round-trip, no JWT signature check).
    * "Start Free Trial" persists an unactivated, free-tier state.

Dev mode turns on if EITHER is true:

    1. ``TEST_MODE_ACCEPT_ANY_KEY`` is True (build-time constant below).
       Use this while iterating locally — flip to False once Firebase
       activation is wired up and you want real keys to be required.
    2. ``DLC_DEV_MODE`` env var is set to a truthy value (1/true/yes/on/y).
       Useful for CI runs and packaged builds you want to test without
       editing source.

Dev-activated state is tagged with ``dev_mode=True`` in :class:`LicenseState`,
so if a user later launches with both knobs off the saved fake claims are
rejected at load time and the user is returned to ``UNACTIVATED``.
"""
from __future__ import annotations

import os


# Flip to False (or delete the OR clause in is_dev_mode) when the real
# Firebase/Stripe activation path is live and you want production behavior
# from a default-config build. Leaving this True means *any* string typed
# into the activation dialog grants Premium locally.
TEST_MODE_ACCEPT_ANY_KEY: bool = True

_TRUTHY = {"1", "true", "yes", "on", "y"}


def is_dev_mode() -> bool:
    """Return True when activation should bypass the server."""
    if TEST_MODE_ACCEPT_ANY_KEY:
        return True
    return os.environ.get("DLC_DEV_MODE", "").strip().lower() in _TRUTHY

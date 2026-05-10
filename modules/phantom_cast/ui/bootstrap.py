"""UI integration glue.

Called by ``launch.py`` after the legacy ``modules.ui.init(start, destroy, lang)``
returns its CTk root. We:

    * dock a :class:`StatusBar` at the bottom
    * wire feature-locked exceptions to pop the paywall
    * expose ``open_settings`` and ``open_paywall`` for callers
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Optional

from modules.dlc_pro.license import license_manager
from modules.dlc_pro.logger import get
from modules.dlc_pro.subscription.gate import FeatureLocked, refresh_claims_async
from modules.dlc_pro.ui import toast
from modules.dlc_pro.ui.paywall_dialog import PaywallDialog
from modules.dlc_pro.ui.settings_panel import open_settings_panel
from modules.dlc_pro.ui.status_bar import StatusBar

log = get("ui.bootstrap")


def bootstrap_ui(root: tk.Misc) -> None:
    """Attach Pro UI to an existing CTk root."""
    if root is None:
        return
    license_manager().bind_root(root)

    bar = StatusBar(root)
    bar.pack(side="bottom", fill="x")

    _install_paywall_handler(root)
    root.after(2000, refresh_claims_async)
    root.after(60_000, lambda: _periodic_refresh(root))


def _periodic_refresh(root: tk.Misc) -> None:
    refresh_claims_async()
    root.after(6 * 3600 * 1000, lambda: _periodic_refresh(root))


def _install_paywall_handler(root: tk.Misc) -> None:
    """Wrap Tk's ``report_callback_exception`` to catch :class:`FeatureLocked`."""
    original = root.report_callback_exception  # type: ignore[attr-defined]

    def handler(exc, val, tb):
        if isinstance(val, FeatureLocked):
            log.info("paywall trigger: %s", val.feature)
            try:
                PaywallDialog(root, locked=val)
            except Exception:  # noqa: BLE001
                log.exception("paywall failed to open")
            return
        original(exc, val, tb)

    root.report_callback_exception = handler  # type: ignore[attr-defined]


def open_settings(root: tk.Misc) -> None:
    open_settings_panel(root)


def open_paywall(root: tk.Misc, feature: Optional[str] = None) -> None:
    locked = FeatureLocked(feature, "free") if feature else None
    PaywallDialog(root, locked=locked)

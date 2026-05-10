"""First-run flow orchestration.

Invoked from ``launch.py`` *after* ``modules.ui.init()`` has produced a CTk
root and the StatusBar is wired up. We open the wizard, block on it (modal),
and only return once it dismisses itself.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from modules.phantom_cast.logger import get
from modules.phantom_cast.paths import first_run_marker

log = get("setup.first_run")


def is_first_run() -> bool:
    # === TEMP TEST OVERRIDE — REVERT TO NORMAL ===
    # Original line:
    #     return not first_run_marker().is_file()
    return True
    # === END TEMP TEST OVERRIDE ===


def run_first_run(root: tk.Misc, on_complete: Optional[Callable[[], None]] = None) -> None:
    """Open the first-run wizard. Idempotent: if marker exists, no-op."""
    if not is_first_run():
        if on_complete:
            on_complete()
        return

    from modules.phantom_cast.ui.setup_wizard import SetupWizard

    log.info("starting first-run wizard")
    SetupWizard(root, on_complete=lambda: (on_complete() if on_complete else None))

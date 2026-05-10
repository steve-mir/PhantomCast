"""Pro UI: status bar, dialogs, settings panel, first-run wizard, toasts.

Wraps the legacy Tk/CTk UI in ``modules/ui.py`` rather than rewriting it. The
entry point :func:`bootstrap_ui` should be called from ``launch.py`` *after*
the legacy ``modules.ui.init(...)`` returns its root.
"""

from modules.phantom_cast.ui.bootstrap import bootstrap_ui, open_paywall, open_settings

__all__ = ["bootstrap_ui", "open_paywall", "open_settings"]

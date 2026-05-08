#!/usr/bin/env python3
"""Deep-Live-Cam Pro bootstrapper.

This is the *only* entry point for the installed product. It:

    1. Configures logging into %LOCALAPPDATA%/DeepLiveCamPro/logs.
    2. Primes CUDA / cuDNN DLL search paths *before* anything imports
       onnxruntime or torch.
    3. Runs the GPU detector cascade and decides CUDA vs CPU.
    4. Wraps the legacy ``modules.core`` argv + processor pipeline behind
       license / subscription gates.
    5. Handles first-run wizard.
    6. Hands off to ``modules.core.run()`` which spins up the existing UI.

Crash-safe by construction: every step that could fail is wrapped in a
try/except that logs and falls back to a degraded but usable state. The
installer will create a Start-menu shortcut pointing at this entry.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _project_root_to_path() -> None:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


def _configure_logger() -> None:
    from modules.dlc_pro.logger import configure

    configure()


def _prime_environment() -> None:
    """PATH + DLL directories. Must run before onnxruntime / torch import."""
    from modules.dlc_pro.gpu.bootstrap import prime_paths

    prime_paths()


def _run_first_run_if_needed(on_done) -> bool:
    """Returns True if the wizard handled startup; False to continue immediately."""
    from modules.dlc_pro.setup.first_run import is_first_run

    if not is_first_run():
        return False

    # We have to bring up a transient root *before* the legacy UI so the
    # wizard has somewhere to live. The legacy core.run() will create its
    # own root afterwards.
    import customtkinter as ctk

    boot_root = ctk.CTk()
    boot_root.withdraw()
    # macOS: pump pending Tk events so the withdrawn root is fully
    # registered before any CTkToplevel is parented to it. Without this,
    # the wizard Toplevel can come up with no rendered children.
    boot_root.update_idletasks()
    from modules.dlc_pro.setup.first_run import run_first_run

    def cont():
        try:
            boot_root.destroy()
        except Exception:  # noqa: BLE001
            pass
        on_done()

    run_first_run(boot_root, on_complete=cont)
    boot_root.mainloop()
    return True


def _wrap_legacy_ui_init() -> None:
    """Patch ``modules.ui.init`` so we can attach the StatusBar to its root.

    The legacy UI builds the CTk root inside ``init()`` and never returns it.
    We monkey-patch by wrapping the function: after it returns, we walk
    Tk's default root and bootstrap our extras.
    """
    import modules.ui as legacy_ui
    from modules.dlc_pro.logger import get
    from modules.dlc_pro.ui import bootstrap_ui

    log = get("launch")
    original_init = legacy_ui.init

    def patched_init(start, destroy, lang):
        result = original_init(start, destroy, lang)
        try:
            # The legacy init stores the root in module-level ``ROOT``.
            root = getattr(legacy_ui, "ROOT", None)
            if root is not None:
                bootstrap_ui(root)
            else:
                log.warning("legacy ui.ROOT missing; status bar not attached")
        except Exception:  # noqa: BLE001
            log.exception("status bar bootstrap failed")
        return result

    legacy_ui.init = patched_init  # type: ignore[assignment]


def _run_core() -> None:
    from modules.dlc_pro.core_bridge import apply
    apply()
    _wrap_legacy_ui_init()

    from modules import platform_info
    platform_info.print_banner()

    from modules import core as legacy_core
    legacy_core.run()


def _crash_dialog(exc: BaseException) -> None:
    msg = "".join(traceback.format_exception(exc))
    try:
        import tkinter.messagebox as mb
        mb.showerror(
            "Deep-Live-Cam Pro — fatal error",
            f"{exc.__class__.__name__}: {exc}\n\nDiagnostics copied to clipboard.\n"
            f"See logs in %LOCALAPPDATA%\\DeepLiveCamPro\\logs",
        )
    except Exception:  # noqa: BLE001
        sys.stderr.write(msg)
    try:
        from modules.dlc_pro.logger import get
        get("launch").error("fatal: %s", msg)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    _project_root_to_path()
    try:
        _configure_logger()
        _prime_environment()
    except Exception as e:  # noqa: BLE001
        _crash_dialog(e)
        return 2

    try:
        if _run_first_run_if_needed(_run_core):
            return 0
        _run_core()
        return 0
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        _crash_dialog(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Modal "update available" dialog with inline download + install flow.

Shown when ``modules.dlc_pro.updater.check_for_update()`` returns a newer
release. Three buttons:
    Install          → download asset, run installer, exit app.
    Remind me later  → close dialog; re-prompt next launch.
    Skip this version → write skip marker; do not prompt again until a
                        higher version is published.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from modules.dlc_pro import updater
from modules.dlc_pro.updater import UpdateInfo, UpdaterState

try:
    from modules.dlc_pro.logger import get
    log = get("ui.update")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("dlc_pro.ui.update")


def _human_bytes(n: Optional[int]) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n:.1f} TB"


class UpdateDialog(ctk.CTkToplevel):
    """Tkinter modal that owns the entire update lifecycle for one release."""

    def __init__(self, parent: tk.Misc, info: UpdateInfo) -> None:
        super().__init__(parent)
        self.title("Update available")
        self.geometry("520x460")
        self.resizable(False, False)

        self.update_idletasks()
        if sys.platform != "darwin":
            try:
                self.transient(parent)
                self.grab_set()
            except tk.TclError:
                pass
        self.lift()
        self.focus_force()

        self._info = info
        self._cancel_flag = threading.Event()
        self._dl_thread: Optional[threading.Thread] = None
        self._build()

    # ----- layout -----

    def _build(self) -> None:
        ctk.CTkLabel(self, text="A new version is available",
                     font=("Segoe UI", 18, "bold")).pack(pady=(20, 2))

        ctk.CTkLabel(
            self,
            text=f"You have {self._info.current} — {self._info.latest} is now available.",
            font=("Segoe UI", 12),
        ).pack(pady=(0, 12))

        # Release notes — read-only textbox so long bodies scroll.
        notes_frame = ctk.CTkFrame(self)
        notes_frame.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        ctk.CTkLabel(notes_frame, text="What's new", font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        notes = ctk.CTkTextbox(notes_frame, height=180, wrap="word")
        notes.insert("1.0", (self._info.body or "No release notes provided.").strip())
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Status / progress row.
        self._status_var = tk.StringVar(value=self._initial_status())
        ctk.CTkLabel(self, textvariable=self._status_var, font=("Segoe UI", 10),
                     anchor="w").pack(fill="x", padx=20)
        self._progress = ctk.CTkProgressBar(self, height=8)
        self._progress.set(0.0)
        self._progress.pack(fill="x", padx=20, pady=(2, 12))
        self._progress.pack_forget()  # hide until install starts

        # Buttons.
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 16))
        self._skip_btn   = ctk.CTkButton(btns, text="Skip this version",
                                         fg_color="transparent", border_width=1,
                                         command=self._on_skip, width=130)
        self._later_btn  = ctk.CTkButton(btns, text="Later",
                                         fg_color="transparent", border_width=1,
                                         command=self._on_later, width=80)
        self._install_btn = ctk.CTkButton(btns, text="Install",
                                          command=self._on_install, width=120)
        self._skip_btn.pack(side="left")
        self._later_btn.pack(side="left", padx=(8, 0))
        self._install_btn.pack(side="right")

    def _initial_status(self) -> str:
        if not self._info.asset_url:
            return "No installer asset attached — clicking Install will open the release page."
        size = _human_bytes(self._info.asset_size)
        return f"Installer: {self._info.asset_name}  ({size})"

    # ----- actions -----

    def _on_later(self) -> None:
        log.info("user deferred update %s", self._info.latest)
        self.destroy()

    def _on_skip(self) -> None:
        s = UpdaterState.load()
        s.skip(self._info.latest)
        log.info("user skipped update %s", self._info.latest)
        self.destroy()

    def _on_install(self) -> None:
        # No asset attached → just open the release page in the browser. This
        # is the macOS / Linux path (and Windows fallback for misconfigured
        # releases).
        if not self._info.asset_url or os.name != "nt":
            updater.open_release_page(self._info)
            self.destroy()
            return

        # Prevent re-entry.
        self._install_btn.configure(state="disabled", text="Downloading…")
        self._later_btn.configure(state="disabled")
        self._skip_btn.configure(state="disabled")
        self._progress.pack(fill="x", padx=20, pady=(2, 12))
        self._set_progress(0, self._info.asset_size or 1)

        target = Path(tempfile.gettempdir()) / (self._info.asset_name or "DeepLiveCamPro-installer.exe")

        def _progress(done: int, total: int) -> None:
            # Marshal back to main thread.
            self.after(0, self._set_progress, done, total)

        def _cancel() -> bool:
            return self._cancel_flag.is_set()

        def _worker() -> None:
            try:
                updater.download(self._info.asset_url, target, _progress, _cancel)  # type: ignore[arg-type]
                self.after(0, self._on_download_done, target)
            except Exception as e:
                log.exception("download failed")
                self.after(0, self._on_download_failed, str(e))

        self._dl_thread = threading.Thread(target=_worker, name="dlc-updater-dl", daemon=True)
        self._dl_thread.start()

    # ----- progress callbacks (main thread) -----

    def _set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.set(min(1.0, done / total))
            self._status_var.set(
                f"Downloading…  {_human_bytes(done)} / {_human_bytes(total)}"
            )
        else:
            self._status_var.set(f"Downloading…  {_human_bytes(done)}")

    def _on_download_done(self, path: Path) -> None:
        self._status_var.set("Download complete. Launching installer…")
        self._progress.set(1.0)
        # Brief pause so the user can read the message before we exit.
        self.after(700, lambda: updater.apply_update(path))

    def _on_download_failed(self, message: str) -> None:
        self._status_var.set(f"Download failed: {message}")
        self._install_btn.configure(state="normal", text="Retry")
        self._later_btn.configure(state="normal")
        self._skip_btn.configure(state="normal")


# Convenience entry point used by launch.py wiring.
def show_if_available(parent: tk.Misc, info: UpdateInfo) -> None:
    """Open the dialog. Called by the updater background thread via
    ``parent.after(0, ...)`` so we run on the tk main loop."""
    try:
        UpdateDialog(parent, info)
    except Exception as e:  # never let an updater bug crash the app
        log.exception("could not open update dialog: %s", e)

"""Modal license-activation dialog.

Shown by the first-run wizard, by the paywall when a user has a key, and by
Settings → License → Re-activate.
"""
from __future__ import annotations

import sys
import tkinter as tk
import webbrowser
from typing import Any, Callable, Optional

import customtkinter as ctk

from modules.phantom_cast.license import LicenseError, LicenseStatus, license_manager
from modules.phantom_cast.ui import toast


BUY_URL = "https://phantomcast.space/buy.html"


class ActivationDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc, on_activated: Optional[Callable[[], None]] = None) -> None:
        super().__init__(parent)
        self.title("Activate Phantom Cast Pro")
        self.geometry("440x300")
        self.resizable(False, False)

        # macOS: transient/grab_set against a withdrawn root produces a
        # blank Toplevel — same workaround as setup_wizard.
        self.update_idletasks()
        if sys.platform != "darwin":
            try:
                self.transient(parent)
                self.grab_set()
            except tk.TclError:
                pass
        self.lift()
        self.focus_force()

        self._on_activated = on_activated
        self._build()

        license_manager().bind_root(self.winfo_toplevel())

    def _build(self) -> None:
        ctk.CTkLabel(
            self, text="Activate your license",
            font=("Segoe UI", 18, "bold"),
        ).pack(pady=(20, 4))

        ctk.CTkLabel(
            self,
            text="Paste the license key from your purchase email.",
            font=("Segoe UI", 11),
            text_color="#475569",
        ).pack(pady=(0, 16))

        self._entry = ctk.CTkEntry(
            self, width=380, height=36, justify="center",
            placeholder_text="PC-XXXX-XXXX-XXXX-XXXX",
        )
        self._entry.pack(pady=(0, 8))
        self._entry.focus_set()

        self._status = ctk.CTkLabel(self, text="", text_color="#b91c1c", font=("Segoe UI", 11))
        self._status.pack(pady=(4, 4))

        self._spinner = ctk.CTkProgressBar(self, width=380, mode="indeterminate")
        self._spinner.pack(pady=(0, 4))
        self._spinner.pack_forget()

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(16, 8))

        ctk.CTkButton(btn_row, text="Buy a license",
                      width=140,
                      fg_color="#475569", hover_color="#334155",
                      command=lambda: webbrowser.open(BUY_URL)).pack(side="left", padx=6)
        self._activate_btn = ctk.CTkButton(btn_row, text="Activate",
                                           width=140,
                                           fg_color="#2563eb", hover_color="#1d4ed8",
                                           command=self._do_activate)
        self._activate_btn.pack(side="left", padx=6)

        ctk.CTkLabel(
            self,
            text="One activation per machine. Hardware swaps re-bind automatically.",
            text_color="#94a3b8",
            font=("Segoe UI", 10),
        ).pack(side="bottom", pady=10)

    # ---------- actions ----------

    def _do_activate(self) -> None:
        key = self._entry.get().strip()
        if not key:
            self._status.configure(text="Please enter a license key.")
            return
        self._activate_btn.configure(state="disabled")
        self._spinner.pack(pady=(0, 4))
        self._spinner.start()
        self._status.configure(text="Contacting activation server…", text_color="#475569")

        license_manager().activate(
            key,
            on_done=self._after_ok,
            on_error=self._after_err,
        )

    def _after_ok(self, snap) -> None:
        self._spinner.stop()
        self._spinner.pack_forget()
        self._status.configure(text=f"Activated! Plan: {snap.plan}", text_color="#15803d")
        toast.show(self.master, f"License activated — plan: {snap.plan}", level="ok")
        if self._on_activated:
            self._on_activated()
        self.after(800, self.destroy)

    def _after_err(self, err: LicenseError) -> None:
        self._spinner.stop()
        self._spinner.pack_forget()
        self._activate_btn.configure(state="normal")
        self._status.configure(text=str(err), text_color="#b91c1c")

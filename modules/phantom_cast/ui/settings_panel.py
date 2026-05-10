"""Settings & diagnostics panel.

Sections:
    Execution    — Auto / GPU / CPU radio, with live re-detect button
    Diagnostics  — Probe results table + copy-to-clipboard
    License      — Plan, license id, deactivate, move-license
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from typing import Optional

import customtkinter as ctk

from modules.phantom_cast.gpu import GpuMode, detect, set_user_override
from modules.phantom_cast.license import license_manager
from modules.phantom_cast.paths import logs_dir
from modules.phantom_cast.ui import toast
from modules.phantom_cast.ui.activation_dialog import ActivationDialog


class SettingsPanel(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Settings — Phantom Cast")
        self.geometry("640x520")

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

        self._tabs = ctk.CTkTabview(self, width=620, height=480)
        self._tabs.pack(padx=8, pady=8, fill="both", expand=True)
        self._build_execution(self._tabs.add("Execution"))
        self._build_diagnostics(self._tabs.add("Diagnostics"))
        self._build_license(self._tabs.add("License"))

    # ---------- Execution tab ----------

    def _build_execution(self, parent: tk.Misc) -> None:
        ctk.CTkLabel(parent, text="Execution mode",
                     font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(8, 4), padx=10)
        ctk.CTkLabel(
            parent, justify="left", font=("Segoe UI", 11), text_color="#475569",
            text=("GPU is the default. CPU is a fallback — face-swap will be 5-15× slower.\n"
                  "Auto chooses GPU if the smoke test passes."),
        ).pack(anchor="w", padx=10)

        self._mode_var = tk.StringVar(value=self._current_override())
        accel_option = (
            ("Apple GPU (CoreML)", "coreml")
            if sys.platform == "darwin"
            else ("GPU (CUDA)", "cuda")
        )
        options = (("Auto (recommended)", "auto"), accel_option, ("CPU", "cpu"))
        for label, val in options:
            ctk.CTkRadioButton(parent, text=label, value=val, variable=self._mode_var,
                               command=self._apply_mode).pack(anchor="w", padx=20, pady=4)

        ctk.CTkButton(parent, text="Re-run GPU detection",
                      command=self._redetect, width=200,
                      fg_color="#2563eb", hover_color="#1d4ed8").pack(pady=12, padx=10, anchor="w")
        self._mode_status = ctk.CTkLabel(parent, text="", font=("Segoe UI", 11),
                                         text_color="#15803d")
        self._mode_status.pack(anchor="w", padx=10)

    def _current_override(self) -> str:
        from modules.phantom_cast.gpu.detector import _user_override

        return _user_override().value

    def _apply_mode(self) -> None:
        mode = GpuMode(self._mode_var.get())
        set_user_override(mode)
        toast.show(self.master, f"Execution mode set to {mode.value}", level="info")

    def _redetect(self) -> None:
        result = detect(force=True)
        if result.usable:
            kind = "CoreML" if result.selected == GpuMode.COREML else "CUDA"
            text = f"{kind} available — using GPU."
        else:
            kind = "CoreML" if sys.platform == "darwin" else "CUDA"
            text = f"{kind} not usable — running on CPU."
        self._mode_status.configure(
            text=text,
            text_color=("#15803d" if result.usable else "#b45309"),
        )

    # ---------- Diagnostics tab ----------

    def _build_diagnostics(self, parent: tk.Misc) -> None:
        ctk.CTkLabel(parent, text="GPU detection cascade",
                     font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(8, 4), padx=10)

        self._diag_box = ctk.CTkTextbox(parent, height=320, width=600, font=("Consolas", 11))
        self._diag_box.pack(padx=10, pady=4, fill="both", expand=True)
        self._refresh_diagnostics()

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(row, text="Refresh", command=self._refresh_diagnostics,
                      width=100).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Copy diagnostics", command=self._copy_diag,
                      width=160, fg_color="#475569", hover_color="#334155").pack(side="left", padx=4)
        ctk.CTkButton(row, text="Open logs folder", command=self._open_logs,
                      width=160, fg_color="#475569", hover_color="#334155").pack(side="left", padx=4)

    def _refresh_diagnostics(self) -> None:
        result = detect(force=True)
        self._diag_box.delete("1.0", tk.END)
        self._diag_box.insert(tk.END, result.to_json())

    def _copy_diag(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._diag_box.get("1.0", tk.END))
            toast.show(self.master, "Diagnostics copied to clipboard.", level="ok")
        except tk.TclError:
            pass

    def _open_logs(self) -> None:
        import os
        import sys

        path = str(logs_dir())
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    # ---------- License tab ----------

    def _build_license(self, parent: tk.Misc) -> None:
        snap = license_manager().snapshot()
        ctk.CTkLabel(parent, text="License", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(8, 4), padx=10
        )
        info = (
            f"Status:        {snap.status.value}\n"
            f"Plan:          {snap.plan or '—'}\n"
            f"License ID:    {snap.license_id or '—'}\n"
            f"Last verified: {self._fmt_ts(snap.last_validated_at)}\n"
            f"Offline grace: {self._fmt_ts(snap.offline_grace_until)}\n"
            f"Features:      {', '.join(snap.feature_flags) or '—'}"
        )
        ctk.CTkLabel(parent, text=info, justify="left", font=("Consolas", 11)).pack(
            anchor="w", padx=10, pady=8
        )

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(row, text="Activate / change key",
                      command=self._open_activation, width=180,
                      fg_color="#2563eb", hover_color="#1d4ed8").pack(side="left", padx=4)
        ctk.CTkButton(row, text="Deactivate this device",
                      command=self._deactivate, width=180,
                      fg_color="#b91c1c", hover_color="#7f1d1d").pack(side="left", padx=4)

    def _open_activation(self) -> None:
        ActivationDialog(self.master)

    def _deactivate(self) -> None:
        license_manager().deactivate()
        toast.show(self.master, "Device deactivated.", level="info")

    @staticmethod
    def _fmt_ts(ts: float) -> str:
        if not ts:
            return "—"
        import datetime as dt

        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def open_settings_panel(parent: tk.Misc) -> SettingsPanel:
    return SettingsPanel(parent)

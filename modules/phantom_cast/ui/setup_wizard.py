"""First-run wizard.

Three steps; each step renders a single ``CTkFrame`` and on success calls
``self._next()``.

    1. GPU check        — re-run cascade, show the result, allow continue on CPU
    2. Models           — download/verify ONNX models in background
    3. Activation       — collect license key OR start free trial

Designed to be re-runnable: if any step fails, the user can re-enter from
Settings → Diagnostics → "Re-run setup".
"""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

from modules.phantom_cast.gpu import GpuMode, detect
from modules.phantom_cast.license.manager import LicenseStatus, license_manager
from modules.phantom_cast.paths import first_run_marker
from modules.phantom_cast.setup.model_downloader import ensure_models_async
from modules.phantom_cast.ui import toast
from modules.phantom_cast.ui.activation_dialog import ActivationDialog


class SetupWizard(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc, on_complete: Callable[[], None]) -> None:
        super().__init__(parent)
        self.title("Phantom Cast — First-time setup")
        self.geometry("520x420")
        self.resizable(False, False)

        # On macOS, calling transient()/grab_set() against a withdrawn root
        # leaves the Toplevel with no surface and its children never paint —
        # the user just sees an empty white window. Force an idle pass so
        # the window materialises, skip the modal grab on Darwin, and lift
        # to the front so the wizard is visible without parent focus.
        self.update_idletasks()
        if sys.platform != "darwin":
            try:
                self.transient(parent)
                self.grab_set()
            except tk.TclError:
                pass
        self.lift()
        self.focus_force()

        self._on_complete = on_complete
        self._step = 0
        self._frame: Optional[ctk.CTkFrame] = None
        self._render()

    # ---------- step plumbing ----------

    def _render(self) -> None:
        if self._frame is not None:
            self._frame.destroy()
        # Solid fg_color (not "transparent") — transparent CTk frames render
        # as blank on macOS Tk when the parent Toplevel didn't fully draw.
        self._frame = ctk.CTkFrame(self)
        self._frame.pack(fill="both", expand=True, padx=20, pady=14)
        [self._step_gpu, self._step_models, self._step_activate][self._step]()
        self.update_idletasks()

    def _next(self) -> None:
        self._step += 1
        if self._step >= 3:
            first_run_marker().write_text("ok", encoding="utf-8")
            self.destroy()
            self._on_complete()
            return
        self._render()

    # ---------- step 1: GPU ----------

    def _step_gpu(self) -> None:
        ctk.CTkLabel(self._frame, text="Step 1 of 3 — GPU check",
                     font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 8))

        spinner = ctk.CTkProgressBar(self._frame, mode="indeterminate", width=440)
        spinner.pack(pady=8)
        spinner.start()
        info = ctk.CTkLabel(self._frame, text="Probing your hardware…",
                            text_color="#475569")
        info.pack(pady=4)

        def run_probe():
            result = detect(force=True)
            self.after(0, lambda: present(result))

        def present(result):
            spinner.stop()
            spinner.pack_forget()
            info.pack_forget()

            if result.usable:
                if result.selected == GpuMode.COREML:
                    summary = (
                        f"GPU detected: {result.gpu_name}\n"
                        f"CoreML (Apple Neural Engine + GPU) ready"
                    )
                else:
                    summary = (
                        f"GPU detected: {result.gpu_name}\n"
                        f"Driver: {result.driver_version}  •  CUDA OK"
                    )
                ctk.CTkLabel(
                    self._frame, font=("Segoe UI", 13),
                    text=summary, text_color="#15803d", justify="left",
                ).pack(anchor="w", pady=8)
            else:
                detail = "\n".join(
                    f"• {s.name}: {s.detail}" + (f" — {s.remedy}" if s.remedy else "")
                    for s in result.steps
                )
                ctk.CTkLabel(
                    self._frame, font=("Segoe UI", 12),
                    text="GPU not usable — you can continue on CPU.\n\n" + detail,
                    text_color="#b45309", justify="left", wraplength=440,
                ).pack(anchor="w", pady=8)

            row = ctk.CTkFrame(self._frame, fg_color="transparent")
            row.pack(side="bottom", pady=10, fill="x")
            ctk.CTkButton(row, text="Cancel", width=120, fg_color="#94a3b8",
                          hover_color="#64748b", command=self.destroy).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Continue →", width=140,
                          fg_color="#2563eb", hover_color="#1d4ed8",
                          command=self._next).pack(side="right", padx=4)

        # detect is fast (cached after first run); call inline.
        self.after(50, run_probe)

    # ---------- step 2: models ----------

    def _step_models(self) -> None:
        ctk.CTkLabel(self._frame, text="Step 2 of 3 — Download models",
                     font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 8))

        bar = ctk.CTkProgressBar(self._frame, width=440)
        bar.set(0)
        bar.pack(pady=8)
        status = ctk.CTkLabel(self._frame, text="Preparing…")
        status.pack(pady=4)

        next_btn = ctk.CTkButton(self._frame, text="Continue →", width=140,
                                 state="disabled", command=self._next,
                                 fg_color="#2563eb", hover_color="#1d4ed8")
        next_btn.pack(side="bottom", pady=10)

        def progress(name: str, done: int, total: int) -> None:
            ratio = done / total if total else 0
            self.after(0, lambda: (bar.set(ratio), status.configure(text=f"{name} ({done}/{total})")))

        def finished(success: bool, err: Optional[str]) -> None:
            def apply():
                if success:
                    bar.set(1.0)
                    status.configure(text="All models ready.", text_color="#15803d")
                    next_btn.configure(state="normal")
                else:
                    status.configure(text=f"Model download failed: {err}", text_color="#b91c1c")
                    next_btn.configure(text="Skip", state="normal", fg_color="#94a3b8",
                                       hover_color="#64748b")
            self.after(0, apply)

        ensure_models_async(progress, finished)

    # ---------- step 3: activation ----------

    def _step_activate(self) -> None:
        ctk.CTkLabel(self._frame, text="Step 3 of 3 — Activate",
                     font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 8))
        snap = license_manager().snapshot()
        if snap.status == LicenseStatus.ACTIVE:
            ctk.CTkLabel(
                self._frame,
                text=f"Already activated — plan: {snap.plan}.",
                text_color="#15803d", font=("Segoe UI", 13),
            ).pack(pady=12)
            ctk.CTkButton(self._frame, text="Finish", command=self._next,
                          fg_color="#2563eb", hover_color="#1d4ed8",
                          width=140).pack(side="bottom", pady=10)
            return

        ctk.CTkLabel(
            self._frame, justify="left", wraplength=440,
            text=("Enter the activation key from your $910 purchase. You get "
                  "30 days of full access included; after that your plan "
                  "rolls into a monthly subscription ($19/mo Pro, $49/mo "
                  "Studio). Or continue on the Free tier (lite swap, "
                  "watermarked, 480p, CPU only)."),
            font=("Segoe UI", 12), text_color="#475569",
        ).pack(anchor="w", pady=(2, 12))

        ctk.CTkButton(self._frame, text="Enter activation key",
                      command=self._open_activation, width=200,
                      fg_color="#2563eb", hover_color="#1d4ed8").pack(pady=4)

        ctk.CTkButton(self._frame, text="Continue on Free tier",
                      command=self._start_free, width=200,
                      fg_color="#475569", hover_color="#334155").pack(pady=4)

    def _start_free(self) -> None:
        license_manager().start_free_trial()
        self._next()

    def _open_activation(self) -> None:
        def after():
            if license_manager().is_active():
                self._next()
            else:
                toast.show(self.master, "Activation cancelled — starting free trial.",
                           level="warn")
        ActivationDialog(self.master, on_activated=after)

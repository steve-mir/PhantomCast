"""Friendly post-install reminder when CUDA isn't usable on Windows.

The first-run wizard (modules/dlc_pro/ui/setup_wizard.py) already shows
the full GPU probe report once. This module covers the *subsequent*
launches where the user dismissed without installing CUDA — without it
they'd just silently run on CPU forever and not know why processing is
slow.

Pops a non-blocking modal once per launch (skippable, with a permanent
"don't show again" option). Skipped on:
  - macOS (uses CoreML; doesn't apply)
  - When the GPU detector reports a usable CUDA mode
  - When the user has previously checked "don't show again"
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
import webbrowser

import customtkinter as ctk

from modules.dlc_pro.gpu import GpuMode, detect, selected_mode
from modules.dlc_pro.logger import get
from modules.dlc_pro.paths import settings_file

log = get("ui.cuda_warning")

CUDA_DOWNLOAD_URL = "https://developer.nvidia.com/cuda-downloads"
NVIDIA_DRIVER_URL = "https://www.nvidia.com/Download/index.aspx"
DISMISS_KEY = "cuda_warning_dismissed"


def _load_settings() -> dict:
    p = settings_file()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_settings(d: dict) -> None:
    try:
        settings_file().write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.exception("could not persist dismiss preference")


def should_show() -> bool:
    """Decide whether to show the dialog without touching the UI."""
    if sys.platform == "darwin":
        return False
    if _load_settings().get(DISMISS_KEY):
        return False
    if selected_mode() != GpuMode.CPU:
        return False
    return True


def show(parent: tk.Misc) -> None:
    """Pop the dialog as a CTkToplevel parented to ``parent``."""
    win = ctk.CTkToplevel(parent)
    win.title("GPU acceleration unavailable")
    win.resizable(False, False)
    try:
        win.transient(parent)
    except tk.TclError:
        pass
    try:
        win.grab_set()
    except tk.TclError:
        pass

    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(padx=24, pady=20, fill="both", expand=True)

    ctk.CTkLabel(
        body,
        text="GPU acceleration is not available",
        font=("Segoe UI", 16, "bold"),
        text_color="#b45309",
    ).pack(anchor="w")

    probe = detect()
    detail_lines = [
        f"• {s.name}: {s.detail}" + (f" — {s.remedy}" if s.remedy else "")
        for s in probe.steps
        if s.severity.value in ("warn", "fail")
    ]
    detail_text = (
        "Phantom-Cast Pro will run, but processing falls back to CPU\n"
        "(noticeably slower — minutes per frame instead of real-time).\n\n"
        "To enable GPU acceleration:\n"
        "  1. Install the latest NVIDIA driver (525.60+)\n"
        "  2. Install NVIDIA CUDA 12.x Runtime (~600 MB)"
    )
    if detail_lines:
        detail_text += "\n\nWhat the GPU probe found:\n" + "\n".join(detail_lines)

    ctk.CTkLabel(
        body,
        text=detail_text,
        font=("Segoe UI", 12),
        justify="left",
        wraplength=500,
    ).pack(anchor="w", pady=(8, 14))

    dismiss_var = tk.BooleanVar(value=False)
    ctk.CTkCheckBox(
        body,
        text="Don't show this again",
        variable=dismiss_var,
    ).pack(anchor="w", pady=(0, 12))

    btn_row = ctk.CTkFrame(body, fg_color="transparent")
    btn_row.pack(fill="x")

    def _close() -> None:
        if dismiss_var.get():
            settings = _load_settings()
            settings[DISMISS_KEY] = True
            _save_settings(settings)
        try:
            win.grab_release()
        except tk.TclError:
            pass
        win.destroy()

    def _open_cuda() -> None:
        webbrowser.open(CUDA_DOWNLOAD_URL)

    def _open_driver() -> None:
        webbrowser.open(NVIDIA_DRIVER_URL)

    ctk.CTkButton(
        btn_row,
        text="Get CUDA Runtime",
        width=160,
        fg_color="#2563eb",
        hover_color="#1d4ed8",
        command=_open_cuda,
    ).pack(side="left")

    ctk.CTkButton(
        btn_row,
        text="Get NVIDIA Driver",
        width=160,
        fg_color="#475569",
        hover_color="#334155",
        command=_open_driver,
    ).pack(side="left", padx=(8, 0))

    ctk.CTkButton(
        btn_row,
        text="Continue on CPU",
        width=140,
        fg_color="#94a3b8",
        hover_color="#64748b",
        command=_close,
    ).pack(side="right")

    win.protocol("WM_DELETE_WINDOW", _close)

    win.update_idletasks()
    parent.update_idletasks()
    try:
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        ww = win.winfo_width()
        wh = win.winfo_height()
        win.geometry(f"+{px + (pw - ww) // 2}+{py + (ph - wh) // 2}")
    except tk.TclError:
        pass


def show_if_needed(parent: tk.Misc) -> None:
    """Public entry point: defer the modal a tick so the main UI paints first."""
    if not should_show():
        return

    def _go() -> None:
        try:
            show(parent)
        except Exception:  # noqa: BLE001
            log.exception("CUDA warning dialog failed to render")

    try:
        parent.after(800, _go)
    except Exception:  # noqa: BLE001
        log.exception("could not schedule CUDA warning dialog")

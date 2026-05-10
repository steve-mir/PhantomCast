"""Lightweight non-blocking toast for status messages and errors.

Implemented as a borderless top-level window pinned to the bottom-right of
its parent. Auto-dismisses; click to copy diagnostics.
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional

import customtkinter as ctk


_LEVEL_COLOR = {
    "info": ("#2563eb", "#dbeafe"),
    "warn": ("#b45309", "#fef3c7"),
    "error": ("#b91c1c", "#fee2e2"),
    "ok": ("#15803d", "#dcfce7"),
}


def show(parent: tk.Misc, message: str, level: str = "info", duration_ms: int = 4000,
         diagnostics: Optional[str] = None) -> None:
    fg, bg = _LEVEL_COLOR.get(level, _LEVEL_COLOR["info"])
    win = ctk.CTkToplevel(parent)
    win.overrideredirect(True)
    try:
        win.wm_attributes("-topmost", True)
    except tk.TclError:
        pass

    frame = ctk.CTkFrame(win, fg_color=bg, corner_radius=8, border_width=1, border_color=fg)
    frame.pack(padx=4, pady=4)

    label = ctk.CTkLabel(frame, text=message, text_color=fg, padx=14, pady=10,
                         wraplength=360, justify="left")
    label.pack(anchor="w")

    def copy_and_close(_event=None):
        if diagnostics:
            try:
                parent.clipboard_clear()
                parent.clipboard_append(diagnostics)
            except tk.TclError:
                pass
        try:
            win.destroy()
        except tk.TclError:
            pass

    label.bind("<Button-1>", copy_and_close)
    frame.bind("<Button-1>", copy_and_close)

    parent.update_idletasks()
    try:
        x = parent.winfo_rootx() + parent.winfo_width() - 400
        y = parent.winfo_rooty() + parent.winfo_height() - 120
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    except tk.TclError:
        pass

    parent.after(duration_ms, lambda: win.destroy() if win.winfo_exists() else None)

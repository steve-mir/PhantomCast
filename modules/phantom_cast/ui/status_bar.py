"""Always-visible status bar pinned to the bottom of the root window.

Three pills + a contextual action button:
    - GPU pill    — green "GPU: <name>" or yellow "CPU (fallback)"
    - Plan pill   — "Free", "Premium — N days trial left", "Premium",
                    or red "Subscription expired"
    - Action btn  — "Activate" when on Free, "Subscribe" when sub is
                    at risk or expired, hidden when a paid sub is active
    - Online      — green dot if last heartbeat OK
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from typing import Any, Optional

import customtkinter as ctk

from modules.phantom_cast.firebase.config import PORTAL_FALLBACK_URL
from modules.phantom_cast.gpu import GpuMode, detect, selected_mode
from modules.phantom_cast.license.manager import LicenseStatus, license_manager
from modules.phantom_cast.subscription.gate import current_plan


GREEN = "#15803d"
GREEN_BG = "#dcfce7"
YELLOW = "#b45309"
YELLOW_BG = "#fef3c7"
RED = "#b91c1c"
RED_BG = "#fee2e2"
SLATE = "#334155"
SLATE_BG = "#f1f5f9"


class StatusBar(ctk.CTkFrame):
    def __init__(self, parent: tk.Misc, **kw: Any) -> None:
        super().__init__(parent, height=36, corner_radius=0, **kw)
        self.pack_propagate(False)

        self._gpu_pill = self._make_pill("GPU: detecting…", SLATE, SLATE_BG)
        self._gpu_pill.pack(side="left", padx=(10, 6), pady=4)

        self._plan_pill = self._make_pill("Plan: —", SLATE, SLATE_BG)
        self._plan_pill.pack(side="left", padx=6, pady=4)

        # Contextual CTA: Activate (Free) / Subscribe (expired or sub-at-risk).
        # Packed eagerly so refresh() can show/hide via a single configure().
        # Hidden by default — refresh() decides.
        self._action_button = ctk.CTkButton(
            self, text="", width=104, height=24,
            command=self._do_action, fg_color="#2563eb", hover_color="#1d4ed8",
        )
        self._action_mode: Optional[str] = None  # "activate" | "subscribe" | None

        self._online_pill = self._make_pill("● offline", YELLOW, YELLOW_BG)
        self._online_pill.pack(side="right", padx=(6, 10), pady=4)

        self._diag_button = ctk.CTkButton(
            self, text="Diagnostics", width=92, height=24,
            command=self._show_diagnostics, fg_color=SLATE, hover_color="#475569",
        )
        self._diag_button.pack(side="right", padx=4, pady=4)

        # initial paint + reactive
        self.refresh()
        license_manager().on_change(lambda _s: self.after(0, self.refresh))

    # ---------- helpers ----------

    def _make_pill(self, text: str, fg: str, bg: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            self,
            text=text,
            fg_color=bg,
            text_color=fg,
            corner_radius=12,
            padx=10, pady=2,
            font=("Segoe UI", 11, "bold"),
        )

    def _set_pill(self, pill: ctk.CTkLabel, text: str, fg: str, bg: str) -> None:
        pill.configure(text=text, text_color=fg, fg_color=bg)

    # ---------- public API ----------

    def refresh(self) -> None:
        self._refresh_gpu()
        self._refresh_plan()

    def _refresh_gpu(self) -> None:
        probe = detect()
        mode = selected_mode()
        short = (probe.gpu_name or "GPU")[:34]
        if mode == GpuMode.CPU:
            self._set_pill(self._gpu_pill, "CPU (fallback) — slower", YELLOW, YELLOW_BG)
        elif probe.usable:
            label = "GPU" if mode == GpuMode.CUDA else "CoreML"
            self._set_pill(self._gpu_pill, f"{label}: {short}", GREEN, GREEN_BG)
        else:
            kind = mode.value.upper() if mode != GpuMode.AUTO else "GPU"
            self._set_pill(self._gpu_pill, f"{kind}: forced (unverified)", YELLOW, YELLOW_BG)

    def _refresh_plan(self) -> None:
        mgr = license_manager()
        snap = mgr.snapshot()
        plan = (snap.plan or "free").capitalize()
        action: Optional[str] = None  # default: hide CTA
        if snap.status == LicenseStatus.ACTIVE:
            days = mgr.trial_days_remaining()
            if days is not None and days > 0:
                # Inside the prepaid first month, no Stripe sub yet.
                label = f"{plan} — {days}d trial left"
                fg, bg = (YELLOW, YELLOW_BG) if days <= 5 else (GREEN, GREEN_BG)
                self._set_pill(self._plan_pill, label, fg, bg)
                # Soft nudge in the last week of the trial; nothing earlier.
                if days <= 7:
                    action = "subscribe"
            elif mgr.trial_lapsed():
                # Trial month over and no subscription on file.
                self._set_pill(self._plan_pill, "Subscription required", RED, RED_BG)
                action = "subscribe"
            else:
                self._set_pill(self._plan_pill, f"Plan: {plan}", GREEN, GREEN_BG)
            self._set_pill(self._online_pill, "● online", GREEN, GREEN_BG)
        elif snap.status == LicenseStatus.EXPIRED:
            self._set_pill(self._plan_pill, "Subscription expired", RED, RED_BG)
            self._set_pill(self._online_pill, "● offline (grace)", YELLOW, YELLOW_BG)
            action = "subscribe"
        elif snap.status == LicenseStatus.UNACTIVATED:
            self._set_pill(self._plan_pill, "Free", SLATE, SLATE_BG)
            self._set_pill(self._online_pill, "● activate", YELLOW, YELLOW_BG)
            action = "activate"
        else:
            self._set_pill(self._plan_pill, current_plan().capitalize(), SLATE, SLATE_BG)
            # ERROR / VALIDATING / SUSPENDED: offer activate as recovery path.
            action = "activate"

        self._apply_action(action)

    def _apply_action(self, mode: Optional[str]) -> None:
        """Show/hide the contextual CTA based on plan state."""
        if mode == self._action_mode:
            return
        self._action_mode = mode
        # Pack between plan pill (left side) and diagnostics button (right
        # side). Re-pack so the position remains stable across refreshes.
        try:
            self._action_button.pack_forget()
        except tk.TclError:
            pass
        if mode == "activate":
            self._action_button.configure(
                text="Activate", fg_color="#2563eb", hover_color="#1d4ed8",
            )
            self._action_button.pack(side="left", padx=4, pady=4)
        elif mode == "subscribe":
            self._action_button.configure(
                text="Subscribe", fg_color="#b91c1c", hover_color="#7f1d1d",
            )
            self._action_button.pack(side="left", padx=4, pady=4)
        # else: keep it unpacked (hidden) when fully active + subscribed.

    # ---------- actions ----------

    def _do_action(self) -> None:
        if self._action_mode == "activate":
            self._open_activation()
        elif self._action_mode == "subscribe":
            self._open_subscribe()

    def _open_activation(self) -> None:
        from modules.phantom_cast.ui.activation_dialog import ActivationDialog

        ActivationDialog(self.winfo_toplevel())

    def _open_subscribe(self) -> None:
        # Prefer the per-customer signed URL the heartbeat returns; fall
        # back to the public marketing page when none is set yet.
        snap = license_manager().snapshot()
        url = snap.subscription_url or PORTAL_FALLBACK_URL
        webbrowser.open(url)

    def _show_diagnostics(self) -> None:
        from modules.phantom_cast.ui.settings_panel import open_settings_panel

        open_settings_panel(self.winfo_toplevel())

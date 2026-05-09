"""Paywall: shown when a gated feature is invoked without entitlement.

Displays a plan comparison table and an "Upgrade" CTA that opens Stripe's
hosted checkout / customer portal in the default browser.
"""
from __future__ import annotations

import sys
import tkinter as tk
import webbrowser
from typing import Optional

import customtkinter as ctk

from modules.dlc_pro.firebase.config import PORTAL_FALLBACK_URL
from modules.dlc_pro.subscription.gate import FeatureLocked, current_plan


PLANS = [
    {
        "name": "Free",
        "price": "$0",
        "tag": "Always free",
        "highlights": [
            "Lite face swap (low res)",
            "CPU only",
            "480p export cap",
            "Watermark on every output",
            "2-minute preview of any Premium feature",
        ],
    },
    {
        "name": "Premium",
        "price": "$29/mo",
        "tag": "First month free with activation key",
        "highlights": [
            "GPU acceleration",
            "Hyperswap full-head + GPEN-512 enhancer",
            "Map source → target faces",
            "Batch queue",
            "4K export, no watermark",
        ],
    },
]


class PaywallDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc, locked: Optional[FeatureLocked] = None) -> None:
        super().__init__(parent)
        self.title("Upgrade to unlock")
        self.geometry("620x460")
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

        feature = locked.feature if locked else None
        self._build(feature)

    def _build(self, feature: Optional[str]) -> None:
        title = "Unlock more with Phantom-Cast Pro"
        if feature:
            human = feature.replace("_", " ").title()
            title = f"{human} requires an upgrade"
        ctk.CTkLabel(self, text=title, font=("Segoe UI", 20, "bold")).pack(pady=(18, 4))
        ctk.CTkLabel(
            self, text=f"You're on: {current_plan().capitalize()}",
            font=("Segoe UI", 12), text_color="#475569",
        ).pack(pady=(0, 14))

        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.pack(padx=18, pady=(0, 14), fill="x")
        for i, plan in enumerate(PLANS):
            self._make_card(cards, plan, recommended=(plan["name"] == "Premium")) \
                .grid(row=0, column=i, padx=8, sticky="nsew")
            cards.grid_columnconfigure(i, weight=1)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(pady=(6, 14))
        ctk.CTkButton(actions, text="Maybe later", width=140,
                      fg_color="#94a3b8", hover_color="#64748b",
                      command=self.destroy).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="Upgrade →", width=160,
                      fg_color="#2563eb", hover_color="#1d4ed8",
                      command=self._open_portal).pack(side="left", padx=8)

    def _make_card(self, parent: tk.Misc, plan: dict, recommended: bool) -> ctk.CTkFrame:
        border = "#2563eb" if recommended else "#e2e8f0"
        f = ctk.CTkFrame(parent, corner_radius=10, border_width=2, border_color=border)
        ctk.CTkLabel(f, text=plan["name"], font=("Segoe UI", 16, "bold")).pack(pady=(12, 0))
        ctk.CTkLabel(f, text=plan["price"], font=("Segoe UI", 22, "bold"),
                     text_color="#2563eb" if recommended else "#0f172a").pack(pady=(2, 0))
        ctk.CTkLabel(f, text=plan["tag"], text_color="#64748b",
                     font=("Segoe UI", 10)).pack(pady=(0, 8))
        for h in plan["highlights"]:
            ctk.CTkLabel(f, text=f"✓ {h}", anchor="w", font=("Segoe UI", 11),
                         text_color="#0f172a").pack(fill="x", padx=14, pady=1)
        ctk.CTkLabel(f, text="").pack(pady=(8, 6))
        return f

    def _open_portal(self) -> None:
        # In production, the heartbeat response can carry a fresh signed URL.
        webbrowser.open(PORTAL_FALLBACK_URL)

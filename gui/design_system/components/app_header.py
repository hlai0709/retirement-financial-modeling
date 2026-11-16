"""Floating glass header — brand, mode toggle, build label."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.components.mode_toggle import ModeToggle
from gui.design_system.tokens import COLORS, FONTS, SPACING
from gui.design_system.typography import ui_family
from gui.version import APP_BUILD_LABEL


class AppHeader(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        mode: str = "guided",
        on_mode_change: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, bg=COLORS.bg_base, highlightthickness=0, **kwargs)

        self._glass = GlassPanel(self, padding=SPACING.md)
        self._glass.pack(fill="x")
        bar = self._glass.body
        bar.columnconfigure(1, weight=1)

        left = tk.Frame(bar, bg=COLORS.glass, highlightthickness=0)
        left.grid(row=0, column=0, sticky="w")
        tk.Label(
            left,
            text="Retirement Planner",
            bg=COLORS.glass,
            fg=COLORS.text_primary,
            font=(ui_family(), 13, "bold"),
        ).pack(side="left")
        self._toggle = ModeToggle(left, mode=mode, on_change=on_mode_change, bg=COLORS.glass)
        self._toggle.pack(side="left", padx=(SPACING.md, 0))

        tk.Label(
            bar,
            text=APP_BUILD_LABEL,
            bg=COLORS.glass,
            fg=COLORS.text_muted,
            font=(ui_family(), 11),
        ).grid(row=0, column=2, sticky="e")

    def set_mode(self, mode: str) -> None:
        self._toggle.set_mode(mode)
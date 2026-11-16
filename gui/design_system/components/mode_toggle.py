"""Guided / Expert pill toggle — custom tk buttons."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from gui.design_system.components.pill_controls import PillButton
from gui.design_system.tokens import COLORS, SPACING


class ModeToggle(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        mode: str = "guided",
        on_change: Callable[[str], None] | None = None,
        bg: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, bg=bg or COLORS.glass, highlightthickness=0, **kwargs)
        self._on_change = on_change
        self._mode = mode
        host = tk.Frame(
            self,
            bg=COLORS.bg_elevated,
            highlightthickness=1,
            highlightbackground=COLORS.glass_border_subtle,
        )
        host.pack()
        inner = tk.Frame(host, bg=COLORS.bg_elevated, highlightthickness=0)
        inner.pack(padx=3, pady=3)
        self._guided = tk.Button(
            inner,
            text="Guided",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=6,
            font=("SF Pro Text", 12),
            cursor="hand2",
            command=lambda: self._select("guided"),
        )
        self._guided.pack(side="left", padx=(0, 2))
        self._expert = tk.Button(
            inner,
            text="Expert",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=6,
            font=("SF Pro Text", 12),
            cursor="hand2",
            command=lambda: self._select("expert"),
        )
        self._expert.pack(side="left")
        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        on = {"bg": COLORS.pill_on, "fg": COLORS.text_primary, "activebackground": COLORS.pill_on}
        off = {"bg": COLORS.bg_elevated, "fg": COLORS.text_muted, "activebackground": COLORS.pill_off_hover}
        if mode == "guided":
            self._guided.configure(**on)
            self._expert.configure(**off)
        else:
            self._expert.configure(**on)
            self._guided.configure(**off)

    def _select(self, mode: str) -> None:
        if mode == self._mode:
            return
        self.set_mode(mode)
        if self._on_change is not None:
            self._on_change(mode)
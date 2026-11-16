"""Guided footer progress — step count, dots, journey label."""

from __future__ import annotations

import tkinter as tk

from gui.design_system.tokens import COLORS, SPACING


class ProgressStrip(tk.Frame):
    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, bg=COLORS.glass, highlightthickness=0, **kwargs)
        tk.Frame(self, bg=COLORS.glass_border_subtle, height=1).pack(fill="x", pady=(SPACING.sm, SPACING.sm))
        row = tk.Frame(self, bg=COLORS.glass, highlightthickness=0)
        row.pack(fill="x", padx=SPACING.sm)
        self._left = tk.Label(row, text="Step 1 of 16", bg=COLORS.glass, fg=COLORS.text_muted, font=("SF Pro Text", 11))
        self._left.pack(side="left")
        self._dots_host = tk.Frame(row, bg=COLORS.glass, highlightthickness=0)
        self._dots_host.pack(side="left", expand=True, fill="x", padx=SPACING.md)
        self._right = tk.Label(row, text="Start", bg=COLORS.glass, fg=COLORS.text_muted, font=("SF Pro Text", 11))
        self._right.pack(side="right")

    def set_progress(self, *, step: int, total: int, journey: str) -> None:
        self._left.configure(text=f"Step {step} of {total}")
        self._right.configure(text=journey)
        for child in self._dots_host.winfo_children():
            child.destroy()
        if total <= 0:
            return
        inner = tk.Frame(self._dots_host, bg=COLORS.glass, highlightthickness=0)
        inner.pack(anchor="center")
        width = max(8, min(14, 400 // total))
        for i in range(total):
            color = COLORS.accent if i < step else COLORS.glass_border_subtle
            tk.Frame(inner, bg=color, width=width, height=3).pack(side="left", padx=1)
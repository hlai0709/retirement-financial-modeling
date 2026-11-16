"""Liquid Glass panel — layered border + specular highlight."""

from __future__ import annotations

import tkinter as tk

from gui.design_system.tokens import COLORS, SPACING


class GlassPanel(tk.Frame):
    """Frosted panel simulation for Tk (no OS blur)."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        strong: bool = False,
        padding: int = SPACING.md,
        **kwargs,
    ) -> None:
        bg = COLORS.glass_strong if strong else COLORS.glass
        super().__init__(
            parent,
            bg=COLORS.glass_border_subtle,
            highlightthickness=0,
            **kwargs,
        )
        self._inner = tk.Frame(self, bg=bg, highlightthickness=1, highlightbackground=COLORS.glass_border)
        self._inner.pack(fill="both", expand=True, padx=1, pady=1)

        # Specular top edge
        tk.Frame(self._inner, bg=COLORS.specular, height=1).pack(fill="x", side="top")

        self.body = tk.Frame(self._inner, bg=bg, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=padding, pady=padding)
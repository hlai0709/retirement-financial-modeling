"""Content glass wrapper — matches mockup .content-glass.liquid.liquid-strong."""

from __future__ import annotations

import tkinter as tk

from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.tokens import COLORS, SPACING


class ContentGlass(GlassPanel):
    """Raised glass card for guided question blocks (max ~640px)."""

    def __init__(self, parent: tk.Misc, *, wide: bool = False, **kwargs) -> None:
        super().__init__(parent, strong=True, padding=SPACING.xs, **kwargs)
        self._max_width = 640 if wide else 580
        self.body = tk.Frame(self.body, bg=COLORS.glass_strong, highlightthickness=0)
        self.body.pack(fill="both", expand=True)

    def grid(self, *args, **kwargs):
        kwargs.setdefault("sticky", "nw")
        return super().grid(*args, **kwargs)
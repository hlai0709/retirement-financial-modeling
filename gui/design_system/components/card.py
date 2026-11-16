"""Glass content card — groups guided questions."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.tokens import COLORS, SPACING


class Card(GlassPanel):
    """Raised glass surface with optional title."""

    def __init__(self, parent: tk.Misc, *, title: str | None = None, padding: int = SPACING.lg, strong: bool = True, **kwargs) -> None:
        super().__init__(parent, strong=strong, padding=padding, **kwargs)
        if title:
            ttk.Label(self.body, text=title, style="Caption.TLabel").pack(anchor="w", pady=(0, SPACING.sm))
        self.content = tk.Frame(self.body, bg=COLORS.glass_strong if strong else COLORS.glass, highlightthickness=0)
        self.content.pack(fill="both", expand=True)
        self.body = self.content
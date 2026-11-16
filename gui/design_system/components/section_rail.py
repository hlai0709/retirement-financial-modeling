"""Expert mode left section navigation."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.tokens import COLORS, SPACING


class SectionRail(GlassPanel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        sections: Sequence[tuple[str, str]],
        active_id: str,
        on_select: Callable[[str], None],
        width: int = 168,
        **kwargs,
    ) -> None:
        super().__init__(parent, padding=SPACING.sm, **kwargs)
        self._on_select = on_select
        self._active_id = active_id
        self._buttons: dict[str, ttk.Button] = {}
        self.configure(width=width)
        self.pack_propagate(False)
        ttk.Label(self.body, text="SECTIONS", style="RailCaption.TLabel").pack(anchor="w", padx=SPACING.sm, pady=(SPACING.xs, SPACING.sm))
        for section_id, label in sections:
            btn = ttk.Button(
                self.body,
                text=label,
                style="Rail.TButton",
                command=lambda sid=section_id: self._select(sid),
            )
            btn.pack(fill="x", padx=SPACING.xs, pady=2)
            self._buttons[section_id] = btn
        self.set_active(active_id)

    def _select(self, section_id: str) -> None:
        self.set_active(section_id)
        self._on_select(section_id)

    def set_active(self, section_id: str) -> None:
        self._active_id = section_id
        for sid, btn in self._buttons.items():
            btn.configure(style="RailActive.TButton" if sid == section_id else "Rail.TButton")
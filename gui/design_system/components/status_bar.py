"""Expert status dock — headline + Run plan."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.components.primary_button import RunPlanButton
from gui.design_system.tokens import COLORS, SPACING
from gui.design_system.typography import ui_family


class StatusBar(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_run_plan: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, bg=COLORS.bg_base, highlightthickness=0, **kwargs)
        self._on_run_plan = on_run_plan
        self.status_var = tk.StringVar(value="Expert editor")
        self.detail_var = tk.StringVar(value="")

        dock = GlassPanel(self, padding=SPACING.md)
        dock.pack(fill="x")
        inner = dock.body
        inner.columnconfigure(1, weight=1)

        left = tk.Frame(inner, bg=COLORS.glass, highlightthickness=0)
        left.grid(row=0, column=0, sticky="w")
        tk.Label(
            left,
            textvariable=self.status_var,
            bg=COLORS.glass,
            fg=COLORS.text_primary,
            font=(ui_family(), 14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            left,
            textvariable=self.detail_var,
            bg=COLORS.glass,
            fg=COLORS.text_muted,
            font=(ui_family(), 12),
        ).pack(anchor="w")

        self.run_btn = RunPlanButton(inner, text="Run plan", command=self._run_plan)
        self.run_btn.grid(row=0, column=2, sticky="e")

    def set_status(self, *, headline: str, detail: str = "") -> None:
        self.status_var.set(headline)
        self.detail_var.set(detail)

    def set_run_enabled(self, enabled: bool) -> None:
        self.run_btn.set_disabled(not enabled)

    def _run_plan(self) -> None:
        if self._on_run_plan is not None:
            self._on_run_plan()
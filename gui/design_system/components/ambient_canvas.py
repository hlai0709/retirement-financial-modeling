"""Colorful mesh wallpaper — approximates HTML mockup radial gradients."""

from __future__ import annotations

import tkinter as tk

from gui.design_system.tokens import COLORS, SPACING


def paint_ambient(canvas: tk.Canvas) -> None:
    """Draw multi-bloom mesh gradient on a canvas."""
    canvas.delete("ambient")
    w = max(canvas.winfo_width(), 1)
    h = max(canvas.winfo_height(), 1)
    canvas.create_rectangle(0, 0, w, h, fill=COLORS.bg_base, outline="", tags="ambient")
    blooms = (
        ("#5856d6", 0.15, 0.20, 0.70, 0.55, "gray25"),
        ("#5ac8fa", 0.85, 0.15, 0.60, 0.50, "gray25"),
        ("#ff9500", 0.70, 0.85, 0.55, 0.45, "gray12"),
        ("#af52de", 0.25, 0.75, 0.50, 0.40, "gray12"),
    )
    for color, rx, ry, rw, rh, stipple in blooms:
        x0 = int((rx - rw * 0.5) * w)
        y0 = int((ry - rh * 0.5) * h)
        x1 = int((rx + rw * 0.5) * w)
        y1 = int((ry + rh * 0.5) * h)
        canvas.create_oval(x0, y0, x1, y1, fill=color, outline="", stipple=stipple, tags="ambient")
    # Diagonal wash — lighter than before so blooms read through
    canvas.create_rectangle(0, 0, w, h, fill=COLORS.bg_wash, outline="", stipple="gray50", tags="ambient")
    canvas.tag_lower("ambient")


class AmbientCanvas(tk.Canvas):
    """Full-window gradient blooms behind glass chrome."""

    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, highlightthickness=0, bd=0, bg=COLORS.bg_base, **kwargs)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None) -> None:
        paint_ambient(self)


class GradientMount(tk.Canvas):
    """Canvas with mesh gradient; embeds a child frame inset so blooms show in margins."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        margin_x: int = SPACING.xl + SPACING.lg,
        margin_y: int = SPACING.sm,
        **kwargs,
    ) -> None:
        super().__init__(parent, highlightthickness=0, bd=0, bg=COLORS.bg_base, **kwargs)
        self._margin_x = margin_x
        self._margin_y = margin_y
        self.body = tk.Frame(self, bg=COLORS.bg_base, highlightthickness=0)
        self._win_id = self.create_window(self._margin_x, self._margin_y, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body_configure)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event=None) -> None:
        paint_ambient(self)
        w = max(self.winfo_width(), 1)
        inner_w = max(w - 2 * self._margin_x, 200)
        self.itemconfigure(self._win_id, width=inner_w)

    def _on_body_configure(self, _event=None) -> None:
        self.configure(scrollregion=self.bbox("all"))
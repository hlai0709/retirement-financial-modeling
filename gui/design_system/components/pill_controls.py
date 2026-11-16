"""Custom pill buttons and segmented controls (not native ttk)."""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Sequence

from gui.design_system.tokens import COLORS, FONTS
from gui.design_system.typography import ui_family


def _font(size: int = 13, bold: bool = False) -> tuple[str, int, str]:
    weight = "bold" if bold else "normal"
    return (ui_family(), size, weight)


class PillButton(tk.Button):
    """Glass capsule button matching HTML mockup pills."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        variant: str = "secondary",
        command: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        style = self._style_for(variant)
        super().__init__(
            parent,
            text=text,
            command=command,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            takefocus=0,
            **style,
            **kwargs,
        )
        self._variant = variant

    @staticmethod
    def _style_for(variant: str) -> dict:
        border = COLORS.glass_border
        if variant == "primary":
            return {
                "bg": COLORS.primary_fill,
                "fg": COLORS.text_inverse,
                "activebackground": COLORS.primary_fill_hover,
                "activeforeground": COLORS.text_inverse,
                "font": _font(13, bold=True),
                "padx": 22,
                "pady": 10,
                "highlightthickness": 0,
            }
        if variant == "run":
            return {
                "bg": COLORS.primary_fill,
                "fg": COLORS.text_inverse,
                "activebackground": COLORS.primary_fill_hover,
                "activeforeground": COLORS.text_inverse,
                "font": _font(14, bold=True),
                "padx": 28,
                "pady": 11,
                "highlightthickness": 0,
            }
        if variant == "help":
            return {
                "bg": COLORS.glass_btn,
                "fg": COLORS.text_primary,
                "activebackground": COLORS.glass_btn_hover,
                "activeforeground": COLORS.text_primary,
                "font": _font(13),
                "padx": 18,
                "pady": 8,
                "highlightthickness": 1,
                "highlightbackground": COLORS.glass_border,
                "highlightcolor": COLORS.glass_border,
            }
        if variant == "disabled":
            return {
                "bg": COLORS.glass_disabled,
                "fg": COLORS.text_disabled,
                "activebackground": COLORS.glass_disabled,
                "activeforeground": COLORS.text_disabled,
                "font": _font(13, bold=True),
                "padx": 22,
                "pady": 10,
                "highlightthickness": 1,
                "highlightbackground": COLORS.glass_border_subtle,
                "highlightcolor": COLORS.glass_border_subtle,
                "state": "disabled",
            }
        return {
            "bg": COLORS.glass_btn,
            "fg": COLORS.text_primary,
            "activebackground": COLORS.glass_btn_hover,
            "activeforeground": COLORS.text_primary,
            "font": _font(13, bold=True),
            "padx": 22,
            "pady": 10,
            "highlightthickness": 1,
            "highlightbackground": COLORS.glass_border,
            "highlightcolor": COLORS.glass_border,
        }

    def set_disabled(self, disabled: bool) -> None:
        if disabled:
            s = self._style_for("disabled")
            self.configure(state="disabled", **{k: v for k, v in s.items() if k != "state"})
        else:
            s = self._style_for(self._variant)
            self.configure(state="normal", **{k: v for k, v in s.items() if k != "state"})


class PillRadioGroup(tk.Frame):
    """Segmented pill control — replaces ttk.Radiobutton rows."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        options: Sequence[tuple[str, str]],
        variable: tk.StringVar,
        on_change: Callable[[], None] | None = None,
        bg: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, bg=bg or COLORS.glass_strong, highlightthickness=0, **kwargs)
        self._variable = variable
        self._on_change = on_change
        self._pills: list[tk.Button] = []
        self._values = [v for v, _ in options]
        for value, label in options:
            btn = tk.Button(
                self,
                text=label,
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                takefocus=0,
                padx=20,
                pady=11,
                font=_font(13),
                command=lambda val=value: self._select(val),
            )
            btn.pack(side="left", padx=(0, 10))
            self._pills.append(btn)
        variable.trace_add("write", lambda *_: self._sync_styles())
        self._sync_styles()

    def _select(self, value: str) -> None:
        self._variable.set(value)
        if self._on_change is not None:
            self._on_change()

    def _sync_styles(self) -> None:
        current = self._variable.get()
        for btn, value in zip(self._pills, self._values, strict=True):
            if value == current:
                btn.configure(
                    bg=COLORS.pill_on,
                    fg=COLORS.text_primary,
                    activebackground=COLORS.pill_on,
                    activeforeground=COLORS.text_primary,
                    highlightthickness=1,
                    highlightbackground=COLORS.glass_border,
                )
            else:
                btn.configure(
                    bg=COLORS.pill_off,
                    fg=COLORS.text_secondary,
                    activebackground=COLORS.pill_off_hover,
                    activeforeground=COLORS.text_primary,
                    highlightthickness=1,
                    highlightbackground=COLORS.glass_border_subtle,
                )
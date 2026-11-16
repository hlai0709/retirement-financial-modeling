"""Capsule inputs — glass-bordered entry/combobox shells."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.design_system.tokens import COLORS, FONTS
from gui.design_system.typography import ui_family
from gui.legacy import FieldWidget


def wrap_glass_input(parent: tk.Misc, widget: FieldWidget) -> tk.Frame:
    """Wrap a ttk Entry/Combobox in a glass capsule."""
    shell = tk.Frame(
        parent,
        bg=COLORS.input_fill,
        highlightthickness=1,
        highlightbackground=COLORS.input_border,
        highlightcolor=COLORS.input_border,
    )
    widget.pack(in_=shell, padx=18, pady=10)
    return shell


def glass_entry(parent: tk.Misc, *, width: int = 12, text: str = "") -> tk.Entry:
    shell = tk.Frame(
        parent,
        bg=COLORS.input_fill,
        highlightthickness=1,
        highlightbackground=COLORS.input_border,
        highlightcolor=COLORS.input_border,
    )
    entry = tk.Entry(
        shell,
        width=width,
        relief="flat",
        borderwidth=0,
        bg=COLORS.input_fill,
        fg=COLORS.text_primary,
        insertbackground=COLORS.text_primary,
        font=(ui_family(), FONTS.size_body),
        highlightthickness=0,
    )
    if text:
        entry.insert(0, text)
    entry.pack(padx=18, pady=10)
    return entry


def glass_combobox(parent: tk.Misc, *, values: list[str], width: int = 10) -> ttk.Combobox:
    shell = tk.Frame(
        parent,
        bg=COLORS.input_fill,
        highlightthickness=1,
        highlightbackground=COLORS.input_border,
        highlightcolor=COLORS.input_border,
    )
    combo = ttk.Combobox(shell, values=values, state="readonly", width=width, style="Glass.TCombobox")
    combo.pack(padx=16, pady=9)
    return combo
"""Apply Liquid Glass theme to Tkinter / ttk globally."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.design_system.tokens import COLORS, FONTS, SPACING
from gui.design_system.typography import font_for, ui_family


def install_theme(root: tk.Tk) -> ttk.Style:
    """Configure root window and ttk styles for dark Liquid Glass mode."""
    root.configure(bg=COLORS.bg_base)
    try:
        root.tk_setPalette(
            background=COLORS.bg_base,
            foreground=COLORS.text_primary,
            activeBackground=COLORS.surface_hover,
            activeForeground=COLORS.text_primary,
            highlightBackground=COLORS.glass_border,
            highlightColor=COLORS.accent,
            selectBackground=COLORS.accent_muted,
            selectForeground=COLORS.text_primary,
        )
    except tk.TclError:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    body_font = font_for("body")
    label_font = font_for("label")

    style.configure(".", background=COLORS.bg, foreground=COLORS.text_primary, font=body_font)
    style.configure("TFrame", background=COLORS.bg)
    style.configure("Surface.TFrame", background=COLORS.surface)
    style.configure("Glass.TFrame", background=COLORS.glass)
    style.configure("Card.TFrame", background=COLORS.glass, relief="flat")

    style.configure("TLabel", background=COLORS.bg, foreground=COLORS.text_primary, font=body_font)
    style.configure("Secondary.TLabel", foreground=COLORS.text_secondary, font=font_for("caption"))
    style.configure("Question.TLabel", font=font_for("question"), foreground=COLORS.text_primary)
    style.configure("Title.TLabel", font=font_for("title"), foreground=COLORS.text_primary)
    style.configure("StepTitle.TLabel", font=font_for("step_title"), foreground=COLORS.text_primary)
    style.configure("Caption.TLabel", font=font_for("caption"), foreground=COLORS.text_muted)
    style.configure("RailCaption.TLabel", font=(ui_family(), 10, "bold"), foreground=COLORS.text_muted)

    for name, bg in (("TLabel", COLORS.bg), ("Secondary.TLabel", COLORS.bg), ("Glass.TFrame", COLORS.glass)):
        if name.startswith("T"):
            continue
        try:
            style.configure(name, background=COLORS.glass if "Glass" in name or name == "Secondary.TLabel" else COLORS.bg)
        except tk.TclError:
            pass
    style.configure("Secondary.TLabel", background=COLORS.bg)
    style.configure("Title.TLabel", background=COLORS.glass)
    style.configure("Caption.TLabel", background=COLORS.glass)
    style.configure("RailCaption.TLabel", background=COLORS.glass)

    style.configure(
        "TButton",
        background=COLORS.surface_raised,
        foreground=COLORS.text_primary,
        borderwidth=0,
        focusthickness=0,
        padding=(SPACING.md, SPACING.sm),
        font=body_font,
    )
    style.map(
        "TButton",
        background=[("active", COLORS.surface_hover), ("disabled", COLORS.glass)],
        foreground=[("disabled", COLORS.text_muted)],
    )

    style.configure(
        "Primary.TButton",
        background=COLORS.primary_fill,
        foreground=COLORS.text_inverse,
        font=(ui_family(), FONTS.size_body, "bold"),
        padding=(SPACING.lg, SPACING.sm),
    )
    style.map(
        "Primary.TButton",
        background=[("active", COLORS.primary_fill_hover), ("disabled", COLORS.glass)],
        foreground=[("disabled", COLORS.text_muted)],
    )

    style.configure(
        "RunPlan.TButton",
        background=COLORS.primary_fill,
        foreground=COLORS.text_inverse,
        font=(ui_family(), FONTS.size_body + 1, "bold"),
        padding=(SPACING.xl, SPACING.md),
    )
    style.map(
        "RunPlan.TButton",
        background=[("active", COLORS.primary_fill_hover), ("disabled", COLORS.glass)],
        foreground=[("disabled", COLORS.text_muted)],
    )

    style.configure(
        "Ghost.TButton",
        background=COLORS.glass,
        foreground=COLORS.text_primary,
        padding=(SPACING.md, SPACING.sm),
    )
    style.map("Ghost.TButton", background=[("active", COLORS.surface_hover)])

    style.configure(
        "Disabled.TButton",
        background=COLORS.bg_elevated,
        foreground=COLORS.text_muted,
        padding=(SPACING.md, SPACING.sm),
    )

    style.configure(
        "Pill.TButton",
        background=COLORS.bg_elevated,
        foreground=COLORS.text_muted,
        padding=(SPACING.sm, SPACING.md),
        font=font_for("caption"),
    )
    style.configure(
        "PillActive.TButton",
        background=COLORS.surface_raised,
        foreground=COLORS.text_primary,
        padding=(SPACING.sm, SPACING.md),
        font=(ui_family(), FONTS.size_caption, "bold"),
    )
    style.map("Pill.TButton", background=[("active", COLORS.surface_hover)], foreground=[("active", COLORS.text_primary)])

    style.configure(
        "Rail.TButton",
        background=COLORS.glass,
        foreground=COLORS.text_muted,
        anchor="w",
        padding=(SPACING.md, SPACING.sm),
        font=font_for("caption"),
    )
    style.configure(
        "RailActive.TButton",
        background=COLORS.accent_muted,
        foreground=COLORS.accent,
        anchor="w",
        padding=(SPACING.md, SPACING.sm),
        font=(ui_family(), FONTS.size_caption, "bold"),
    )
    style.map("Rail.TButton", background=[("active", COLORS.surface_hover)])

    style.configure(
        "TEntry",
        fieldbackground=COLORS.input_fill,
        foreground=COLORS.text_primary,
        insertcolor=COLORS.text_primary,
        bordercolor=COLORS.input_fill,
        lightcolor=COLORS.input_fill,
        darkcolor=COLORS.input_fill,
        padding=SPACING.sm,
    )
    style.configure("Glass.TEntry", fieldbackground=COLORS.input_fill, borderwidth=0, padding=(4, 6))

    style.configure(
        "TCombobox",
        fieldbackground=COLORS.input_fill,
        background=COLORS.input_fill,
        foreground=COLORS.text_primary,
        arrowcolor=COLORS.text_secondary,
        bordercolor=COLORS.input_fill,
        padding=SPACING.sm,
    )
    style.configure("Glass.TCombobox", fieldbackground=COLORS.input_fill, background=COLORS.input_fill, borderwidth=0)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS.input_fill)],
        foreground=[("readonly", COLORS.text_primary)],
    )
    style.map("Glass.TCombobox", fieldbackground=[("readonly", COLORS.input_fill)])

    style.configure("TRadiobutton", background=COLORS.bg, foreground=COLORS.text_primary, font=body_font)
    style.map("TRadiobutton", background=[("active", COLORS.bg)], foreground=[("disabled", COLORS.text_muted)])

    style.configure(
        "Card.TLabelframe",
        background=COLORS.glass,
        borderwidth=1,
        relief="solid",
        bordercolor=COLORS.glass_border_subtle,
    )
    style.configure(
        "Card.TLabelframe.Label",
        background=COLORS.glass,
        foreground=COLORS.text_secondary,
        font=label_font,
    )

    style.configure("TNotebook", background=COLORS.bg, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=COLORS.surface,
        foreground=COLORS.text_secondary,
        padding=(SPACING.md, SPACING.sm),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS.surface_raised)],
        foreground=[("selected", COLORS.text_primary)],
    )

    style.configure("Horizontal.TProgressbar", troughcolor=COLORS.surface_raised, background=COLORS.accent, borderwidth=0)
    style.configure(
        "Vertical.TScrollbar",
        background=COLORS.surface,
        troughcolor=COLORS.bg,
        borderwidth=0,
        arrowcolor=COLORS.text_muted,
    )

    return style
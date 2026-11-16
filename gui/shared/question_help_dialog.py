"""Double-click help popup for guided questions."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.design_system.components.primary_button import PrimaryButton
from gui.design_system.tokens import COLORS, SPACING


def show_question_help(parent: tk.Misc, *, title: str, body: str) -> None:
    dialog = tk.Toplevel(parent)
    dialog.title("About this question")
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    dialog.configure(bg=COLORS.surface)
    dialog.resizable(False, False)

    frame = ttk.Frame(dialog, style="Card.TFrame", padding=SPACING.lg)
    frame.pack(fill="both", expand=True, padx=SPACING.md, pady=SPACING.md)

    ttk.Label(frame, text=title, style="Title.TLabel", wraplength=420).pack(anchor="w")
    ttk.Label(frame, text=body, wraplength=420, justify="left").pack(anchor="w", pady=(SPACING.md, SPACING.lg))
    PrimaryButton(frame, text="Got it", command=dialog.destroy).pack(anchor="e")

    dialog.update_idletasks()
    px = parent.winfo_rootx() + 40
    py = parent.winfo_rooty() + 40
    dialog.geometry(f"+{px}+{py}")
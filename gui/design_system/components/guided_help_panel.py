"""Slide-out help panel for guided steps."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.tokens import COLORS, SPACING
from gui.legacy import _create_scrollable_frame


class GuidedHelpPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, bg=COLORS.bg_base, highlightthickness=0, **kwargs)
        self.place(relx=1.0, rely=0, relheight=1.0, anchor="ne", width=0)
        self._open = False

        self._backdrop = tk.Frame(self, bg="#000000", highlightthickness=0)
        self._backdrop.bind("<Button-1>", lambda _e: self.close())

        self._panel = GlassPanel(self, strong=True, padding=0)
        self._panel.place(relx=1.0, rely=0, relheight=1.0, anchor="ne", width=340)

        head = tk.Frame(self._panel.body, bg=COLORS.glass_strong, highlightthickness=0)
        head.pack(fill="x", padx=SPACING.lg, pady=SPACING.md)
        self._title = ttk.Label(head, text="Help", style="Title.TLabel")
        self._title.pack(side="left")
        ttk.Button(head, text="✕", style="Ghost.TButton", command=self.close).pack(side="right")

        scroll_host = tk.Frame(self._panel.body, bg=COLORS.glass_strong, highlightthickness=0)
        scroll_host.pack(fill="both", expand=True)
        _canvas, self._body = _create_scrollable_frame(scroll_host)
        self._body.columnconfigure(0, weight=1)

    def open(self, *, title: str, items: list[tuple[str, str]]) -> None:
        self._title.configure(text=title)
        for child in self._body.winfo_children():
            child.destroy()
        for idx, (heading, body) in enumerate(items):
            if idx > 0:
                tk.Frame(self._body, bg=COLORS.glass_border_subtle, height=1).grid(
                    row=idx * 2, column=0, sticky="ew", pady=SPACING.md
                )
            block = ttk.Frame(self._body, style="Glass.TFrame")
            block.grid(row=idx * 2 + 1, column=0, sticky="ew", padx=SPACING.md)
            ttk.Label(block, text=heading, style="Title.TLabel", wraplength=300).pack(anchor="w")
            ttk.Label(block, text=body, style="Secondary.TLabel", wraplength=300, justify="left").pack(
                anchor="w", pady=(SPACING.xs, 0)
            )
        self._open = True
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self._backdrop.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._panel.place(relx=1.0, rely=0, relheight=1.0, anchor="ne", width=340)

    def close(self) -> None:
        self._open = False
        self.place_forget()
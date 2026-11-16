"""Render catalog fields into a Tk frame (no per-field Research buttons)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.core.guided_catalog import guided_help_for_field, guided_question_for_field
from config.model_fields import ModelField
from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.tokens import COLORS, SPACING
from gui.shared.field_binder import FieldBinder, resolve_field_person
from gui.shared.question_help_dialog import show_question_help


def _panel_bg(parent: tk.Misc) -> str:
    try:
        return str(parent.cget("bg"))
    except tk.TclError:
        return COLORS.glass_strong


def render_field_rows(
    parent: ttk.Frame,
    fields: tuple[ModelField, ...],
    binder: FieldBinder,
    *,
    person: str | None = None,
    start_row: int = 0,
) -> int:
    row = start_row
    bg = _panel_bg(parent)
    parent.columnconfigure(0, weight=0)
    parent.columnconfigure(1, weight=0)
    for field in fields:
        field_person = resolve_field_person(field, person)
        tk.Label(
            parent,
            text=field.label + ":",
            anchor="w",
            bg=bg,
            fg=COLORS.text_primary,
            font=("SF Pro Text", 13),
        ).grid(row=row, column=0, sticky="nw", pady=4, padx=(0, SPACING.md))
        widget = binder.make_widget(parent, field, person=field_person)
        widget.grid(row=row, column=1, sticky="w", pady=4)
        binder.register(field, widget, person=field_person)
        row += 1
    return row


def _bind_help_popup(widget: tk.Misc, *, title: str, body: str) -> None:
    if not body:
        return

    def _open(_event=None) -> None:
        show_question_help(widget, title=title, body=body)

    widget.bind("<Double-Button-1>", _open)


def render_guided_field_rows(
    parent: ttk.Frame,
    fields: tuple[ModelField, ...],
    binder: FieldBinder,
    *,
    person: str | None = None,
    start_row: int = 0,
    question_start: int = 1,
) -> int:
    """Numbered questions in glass sections; double-click question for field help."""
    row = start_row
    parent.columnconfigure(0, weight=0)
    glass_bg = COLORS.glass_strong

    for offset, field in enumerate(fields):
        field_person = resolve_field_person(field, person)
        question = guided_question_for_field(field, person=field_person)
        help_text = guided_help_for_field(field)
        number = question_start + offset

        section = tk.Frame(parent, bg=glass_bg, highlightthickness=0)
        section.grid(row=row, column=0, sticky="ew", pady=(SPACING.md if offset else 0, 0))
        if offset > 0:
            tk.Frame(section, bg=COLORS.glass_border_subtle, height=1).pack(fill="x", pady=(SPACING.md, SPACING.md))

        inner = tk.Frame(section, bg=glass_bg, highlightthickness=0, cursor="hand2")
        inner.pack(fill="x", anchor="w", padx=SPACING.xl, pady=(SPACING.lg, SPACING.sm))

        qrow = tk.Frame(inner, bg=glass_bg, highlightthickness=0)
        qrow.pack(anchor="w", fill="x")
        tk.Label(
            qrow,
            text=f"{number}.",
            bg=glass_bg,
            fg=COLORS.accent,
            font=("SF Pro Text", 18, "bold"),
        ).pack(side="left", anchor="nw")
        tk.Label(
            qrow,
            text=question,
            bg=glass_bg,
            fg=COLORS.text_primary,
            font=("SF Pro Text", 18),
            wraplength=500,
            justify="left",
        ).pack(side="left", anchor="w", padx=(SPACING.xs, 0))

        if help_text:
            _bind_help_popup(section, title=question, body=help_text)
            _bind_help_popup(inner, title=question, body=help_text)
            _bind_help_popup(qrow, title=question, body=help_text)

        widget = binder.make_widget(section, field, person=field_person)
        shell = widget.nametowidget(widget.winfo_parent())
        shell.pack(anchor="w", padx=SPACING.xl, pady=(0, SPACING.lg))
        binder.register(field, widget, person=field_person)
        row += 1

    return row


def render_grouped_sections(
    parent: ttk.Frame,
    sections: list[tuple[str, str, tuple[ModelField, ...]]] | list[tuple[str, tuple[ModelField, ...]]],
    binder: FieldBinder,
    *,
    person: str | None = None,
) -> None:
    row = 0
    parent.columnconfigure(0, weight=0)
    for item in sections:
        if len(item) == 3:
            title, _step_id, fields = item
        else:
            title, fields = item  # type: ignore[misc]
        if not fields:
            continue
        frame = GlassPanel(parent, strong=True, padding=SPACING.md)
        frame.grid(row=row, column=0, sticky="nw", padx=0, pady=SPACING.sm)
        tk.Label(
            frame.body,
            text=title,
            bg=COLORS.glass_strong,
            fg=COLORS.text_secondary,
            font=("SF Pro Text", 12, "bold"),
        ).pack(anchor="w", pady=(0, SPACING.sm))
        inner = tk.Frame(frame.body, bg=COLORS.glass_strong, highlightthickness=0)
        inner.pack(anchor="w")
        render_field_rows(inner, fields, binder, person=person)
        row += 1
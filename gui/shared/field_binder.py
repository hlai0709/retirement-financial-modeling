"""Bind PlanningSnapshot values to Tk field widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.core.planning_snapshot import PlanningSnapshot
from config.model_fields import ModelField, default_display_value, field_choice_values
from gui.legacy import FieldWidget, _widget_get_text, _widget_set_text
from gui.design_system.tokens import COLORS
from gui.shared.field_widths import combobox_width_for, entry_width_for

PERSONS = ("self", "wife")


def resolve_field_person(field: ModelField, person: str | None) -> str | None:
    """Person-scoped fields need self/wife; shared-scope wizard steps default to self."""
    if field.scope == "shared":
        return None
    return person or "self"


class FieldBinder:
    """Owns widget map and syncs with a PlanningSnapshot."""

    def __init__(self, snapshot: PlanningSnapshot) -> None:
        self.snapshot = snapshot
        self.shared_widgets: dict[str, FieldWidget] = {}
        self.person_widgets: dict[str, dict[str, FieldWidget]] = {p: {} for p in PERSONS}
        self.source_vars: dict[tuple[str | None, str], tk.StringVar] = {}

    def make_widget(self, parent, field: ModelField, *, person: str | None) -> FieldWidget:
        display = self.snapshot.get_raw(field, person=person) or default_display_value(field, person=person)
        choices = field_choice_values(field)
        shell = tk.Frame(
            parent,
            bg=COLORS.input_fill,
            highlightthickness=1,
            highlightbackground=COLORS.input_border,
            highlightcolor=COLORS.input_border,
        )
        if choices:
            widget = ttk.Combobox(
                shell,
                values=list(choices),
                state="readonly",
                width=combobox_width_for(field),
                style="Glass.TCombobox",
            )
            widget.set(str(display))
            widget.pack(padx=12, pady=8)
            return widget
        entry = ttk.Entry(shell, width=entry_width_for(field), style="Glass.TEntry")
        entry.insert(0, str(display))
        entry.pack(padx=14, pady=9)
        return entry

    def register(
        self,
        field: ModelField,
        widget: FieldWidget,
        *,
        person: str | None,
    ) -> tk.StringVar:
        if field.scope == "shared":
            self.shared_widgets[field.key] = widget
        else:
            if person is None:
                raise ValueError(f"person required for {field.key}")
            self.person_widgets[person][field.key] = widget

        source_var = tk.StringVar(value=self.snapshot.get_source(person, field.key))
        self.source_vars[(person, field.key)] = source_var
        return source_var

    def _widget_alive(self, widget: FieldWidget) -> bool:
        try:
            return bool(widget.winfo_exists())
        except tk.TclError:
            return False

    def prune_destroyed_widgets(self) -> None:
        """Drop stale widget refs after a panel rebuild (e.g. assumption tab switch)."""
        self.shared_widgets = {
            key: widget for key, widget in self.shared_widgets.items() if self._widget_alive(widget)
        }
        for person in PERSONS:
            self.person_widgets[person] = {
                key: widget
                for key, widget in self.person_widgets[person].items()
                if self._widget_alive(widget)
            }

    def pull_widget(self, field: ModelField, *, person: str | None = None) -> None:
        widget = self._widget_for(field, person=person)
        if widget is None or not self._widget_alive(widget):
            return
        self.snapshot.set_raw(field, _widget_get_text(widget), person=person)

    def push_widget(self, field: ModelField, *, person: str | None = None) -> None:
        widget = self._widget_for(field, person=person)
        if widget is None or not self._widget_alive(widget):
            return
        _widget_set_text(widget, self.snapshot.get_raw(field, person=person))

    def pull_all(self) -> None:
        from config.model_fields import PERSON_FIELDS, SHARED_FIELDS

        self.prune_destroyed_widgets()
        for field in SHARED_FIELDS:
            self.pull_widget(field)
        for person in PERSONS:
            for field in PERSON_FIELDS:
                self.pull_widget(field, person=person)

    def push_all(self) -> None:
        from config.model_fields import PERSON_FIELDS, SHARED_FIELDS

        for field in SHARED_FIELDS:
            self.push_widget(field)
        for person in PERSONS:
            for field in PERSON_FIELDS:
                self.push_widget(field, person=person)

    def set_value(self, field: ModelField, value, *, person: str | None = None, source: str = "Manual") -> None:
        from config.field_values import format_field_value

        self.snapshot.set_parsed(field, value, person=person)
        self.push_widget(field, person=person)
        self.set_source(person, field.key, source)

    def set_source(self, person: str | None, field_key: str, label: str) -> None:
        self.snapshot.set_source(person, field_key, label)
        var = self.source_vars.get((person, field_key))
        if var is not None:
            var.set(label if label else "Manual")

    def _widget_for(self, field: ModelField, *, person: str | None) -> FieldWidget | None:
        if field.scope == "shared":
            return self.shared_widgets.get(field.key)
        if person is None:
            return None
        return self.person_widgets.get(person, {}).get(field.key)
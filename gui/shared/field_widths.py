"""Appropriate input widths by field type — avoid full-width age boxes."""

from __future__ import annotations

from config.model_fields import ModelField


def entry_width_for(field: ModelField) -> int:
    match field.field_type:
        case "age":
            return 5
        case "year":
            return 7
        case "int":
            return 8
        case "rate_pct":
            return 10
        case "money":
            return 14
        case "bool":
            return 10
        case _:
            return 18


def combobox_width_for(field: ModelField) -> int:
    choices = field.field_type
    if field.key == "state_of_residence":
        return 8
    if choices == "bool":
        return 12
    return min(24, max(12, entry_width_for(field) + 4))
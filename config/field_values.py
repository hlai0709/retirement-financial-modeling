"""Parse and serialize GUI field values based on model field metadata."""

from __future__ import annotations

from config.model_fields import FieldType, ModelField


def parse_field_value(field: ModelField, raw: str):
    value = raw.strip()
    if field.field_type == "money":
        return float(value) if value else 0.0
    if field.field_type == "rate_pct":
        return float(value) / 100 if value else 0.0
    if field.field_type == "age":
        return int(value) if value else 0
    if field.field_type == "year":
        if not value:
            return None
        # Strip currency noise — local AI sometimes maps principal balances here
        cleaned = value.replace(",", "").replace("$", "").strip()
        year = int(float(cleaned)) if cleaned else 0
        # Treat 0 / tiny placeholders / balance-sized junk as blank
        # (e.g. mortgage principal 250000 wrongly stored as payoff "year")
        if year < 1900 or year > 2100:
            return None
        return year
    if field.field_type == "int":
        return int(value) if value else 0
    if field.field_type == "bool":
        return value.lower() == "true"
    return value


def format_field_value(field: ModelField, value) -> str:
    if value is None:
        return ""
    if field.field_type == "rate_pct":
        return str(float(value) * 100)
    if field.field_type == "money" and isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def combine_person_values(field: ModelField, values: list) -> float | int | str | bool | None:
    if not values:
        return parse_field_value(field, "")
    if field.combine_rule == "sum":
        return sum(values)
    if field.combine_rule == "min":
        return min(values)
    if field.combine_rule == "max":
        return max(values)
    if field.combine_rule == "avg":
        return sum(values) / len(values)
    return values[0]
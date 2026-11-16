"""Live field search for expert assumptions — UI-agnostic."""

from __future__ import annotations

from config.model_fields import ModelField


def normalize_query(query: str) -> str:
    return query.strip().lower()


def field_model_search_text(field: ModelField) -> str:
    return " ".join(
        part
        for part in (field.key, field.label, field.description or "")
        if part
    ).lower()


def field_spec_search_text(spec: dict) -> str:
    return " ".join(
        str(spec.get(key, "") or "")
        for key in ("key", "shortLabel", "label", "caption", "searchText")
        if spec.get(key)
    ).lower()


def matches_field_model(field: ModelField, query: str) -> bool:
    q = normalize_query(query)
    if not q:
        return True
    return q in field_model_search_text(field)


def matches_field_spec(spec: dict, query: str) -> bool:
    q = normalize_query(query)
    if not q:
        return True
    return q in field_spec_search_text(spec)


def filter_assumption_sections(
    sections: list[tuple[str, str, tuple[ModelField, ...]]],
    query: str,
) -> list[tuple[str, str, tuple[ModelField, ...]]]:
    q = normalize_query(query)
    if not q:
        return sections
    filtered: list[tuple[str, str, tuple[ModelField, ...]]] = []
    for title, step_id, fields in sections:
        matched = tuple(field for field in fields if matches_field_model(field, q))
        if matched:
            filtered.append((title, step_id, matched))
    return filtered


def filter_assumption_groups(groups: list[dict], query: str) -> list[dict]:
    q = normalize_query(query)
    if not q:
        return groups
    filtered: list[dict] = []
    for group in groups:
        fields = [spec for spec in group.get("fields", []) if matches_field_spec(spec, q)]
        if fields:
            copy = dict(group)
            copy["fields"] = fields
            copy["matchCount"] = len(fields)
            filtered.append(copy)
    return filtered
"""Extract planning fields from structured Form 1040 JSON snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from app.core.presentation.field_editor import resolve_field
from config.field_values import format_field_value
from config.model_fields import ModelField


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_field(field: ModelField, value: Any, *, person: str | None) -> tuple[str, str]:
    parsed = value
    if field.field_type == "money":
        parsed = _money(value) or 0
    elif field.field_type == "bool":
        parsed = bool(value)
    elif field.field_type == "rate_pct" and isinstance(value, (int, float)):
        parsed = float(value)
    else:
        parsed = value
    raw = format_field_value(field, parsed)
    display = raw
    if field.field_type == "money" and _money(value) is not None:
        display = f"${_money(value):,.0f}"
    elif field.field_type == "rate_pct" and isinstance(value, (int, float)):
        display = f"{float(value) * 100:g}%"
    return raw, display


def _suggestion(
    field_key: str,
    *,
    person: str | None,
    value: Any,
    source_quote: str,
    confidence: str = "high",
) -> ExtractedFieldSuggestion | None:
    if value is None or value == "":
        return None
    field = resolve_field(field_key, person=person)
    if field is None:
        return None
    field_person = person if field.scope == "person" else None
    raw, display = _format_field(field, value, person=field_person)
    if not raw.strip():
        return None
    return ExtractedFieldSuggestion(
        field_key=field_key,
        person=field_person,
        label=field.label,
        raw_value=raw,
        display_value=display,
        confidence=confidence,
        source_quote=source_quote,
        document_type=DocumentType.TAX_RETURN_JSON.value,
    )


def _effective_tax_rate(payload: dict[str, Any]) -> float | None:
    form = payload.get("form_1040") or {}
    agi = _money(form.get("agi"))
    tax = _money(form.get("income_tax"))
    if agi and agi > 0 and tax is not None:
        return round(tax / agi, 4)
    return None


def extract_tax_return_suggestions(payload: dict[str, Any]) -> list[ExtractedFieldSuggestion]:
    income = payload.get("income") or {}
    itemized = payload.get("itemized") or {}
    label = str(payload.get("label") or "tax return")
    suggestions: list[ExtractedFieldSuggestion | None] = []

    def add(
        field_key: str,
        value: Any,
        *,
        person: str | None = None,
        quote: str,
        confidence: str = "high",
    ) -> None:
        suggestions.append(
            _suggestion(field_key, person=person, value=value, source_quote=quote, confidence=confidence)
        )

    state = payload.get("state_of_residence")
    if state:
        add("state_of_residence", str(state).upper(), quote=f"{label}: state_of_residence")

    filing = payload.get("filing_status")
    if filing:
        add("filing_status_after_marriage", str(filing), quote=f"{label}: filing_status")

    wages = _money(income.get("wages"))
    if wages:
        add("annual_earned_income", wages, person="self", quote="Form 1040 line 1z (wages)")

    rental = _money(income.get("rental_income"))
    if rental:
        add("rental_income_start", rental, person="self", quote="Schedule E rental income")

    pension = _money(income.get("pension"))
    if pension:
        add("pension_amount", pension, person="self", quote="Form 1040 pension")

    ss_gross = _money(income.get("social_security_gross"))
    if ss_gross:
        add("ss_amount", ss_gross, person="self", quote="Form 1040 Social Security (gross)")

    for key, field_key, quote in (
        ("salt", "itemized_salt_annual", "Schedule A SALT"),
        ("mortgage_interest", "itemized_mortgage_interest", "Schedule A mortgage interest"),
        ("charitable", "itemized_charitable", "Schedule A charitable"),
        ("medical", "itemized_medical", "Schedule A medical"),
    ):
        amount = _money(itemized.get(key))
        if amount:
            add(field_key, amount, quote=quote)

    real_estate = _money(itemized.get("real_estate_taxes"))
    if real_estate:
        add(
            "property_tax_annual",
            real_estate,
            person="self",
            quote="Schedule A line 5b — real estate taxes (cash-flow; SALT may be capped separately)",
        )

    if payload.get("state_tax_enabled") is not None:
        add("state_tax_enabled", bool(payload.get("state_tax_enabled")), quote="Return header state_tax_enabled")

    override = payload.get("state_tax_override_pct")
    if override is not None and override != "":
        add("state_tax_override_pct", float(override), quote="Return header state_tax_override_pct")

    rate = _effective_tax_rate(payload)
    if rate is None and payload.get("effective_tax_rate_pct") is not None:
        try:
            rate = round(float(payload["effective_tax_rate_pct"]) / 100.0, 4)
        except (TypeError, ValueError):
            rate = None
    if rate is not None:
        add("pre_retire_tax_rate", rate, quote="Derived: income_tax ÷ AGI / effective rate", confidence="medium")

    return [row for row in suggestions if row is not None]


class TaxReturnJsonExtractor:
    document_type = DocumentType.TAX_RETURN_JSON

    def can_extract(self, path: str, payload: Any | None = None) -> bool:
        if payload is not None:
            return isinstance(payload, dict) and "schema_version" in payload and "tax_year" in payload
        return Path(path).suffix.lower() == ".json"

    def extract(self, path: str, payload: Any | None = None) -> list[ExtractedFieldSuggestion]:
        data = payload
        if data is None:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        return extract_tax_return_suggestions(data)
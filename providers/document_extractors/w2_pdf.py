"""Extract planning fields from IRS Form W-2 PDF exports (text layer)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from app.core.presentation.field_editor import resolve_field
from config.field_values import format_field_value
from config.model_fields import ModelField
from providers.document_extractors.pdf_text import extract_pdf_text
from settings import PROJECT_ROOT

_AMOUNT = r"(\$?\s*-?\d[\d,]*\.?\d*)"
_BOX_LINE = re.compile(
    rf"(?:^|\n)\s*(?P<box>\d{{1,2}}[a-d]?)\s+[^\n]{{0,120}}?{_AMOUNT}\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BOX_INLINE = re.compile(
    rf"(?:box\s+)?(?P<box>\d{{1,2}}[a-d]?)\b[^\n]{{0,80}}?{_AMOUNT}",
    re.IGNORECASE,
)
_W2_MARKER = re.compile(r"form\s+w-?2|wage\s+and\s+tax\s+statement", re.IGNORECASE)
_TAX_YEAR = re.compile(r"(?:form\s+w-?2|tax\s+year)\s+(\d{4})", re.IGNORECASE)


def _parse_amount(raw: str) -> float | None:
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned in {"-", "—"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _load_parser_config() -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "lazy_mode" / "parsers" / "w2_2024_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _detect_tax_year(text: str) -> int | None:
    match = _TAX_YEAR.search(text)
    if not match:
        return None
    year = int(match.group(1))
    return year if 2018 <= year <= 2035 else None


def _extract_box_values(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for pattern in (_BOX_LINE, _BOX_INLINE):
        for match in pattern.finditer(text):
            box = match.group("box").lower()
            amount = _parse_amount(match.group(1))
            if amount is None:
                continue
            values.setdefault(box, amount)
    return values


def _format_money(field: ModelField, value: float, *, person: str | None) -> tuple[str, str]:
    raw = format_field_value(field, value)
    display = f"${value:,.0f}"
    return raw, display


def extract_w2_suggestions(
    text: str,
    *,
    label: str = "W-2",
    person: str | None = "self",
) -> list[ExtractedFieldSuggestion]:
    config = _load_parser_config()
    boxes = _extract_box_values(text)
    if not boxes:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label="W-2",
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=(
                    "Could not find W-2 box amounts in this PDF. "
                    "Use a digital PDF from your employer portal, or update "
                    "config/lazy_mode/parsers/w2_2024_v1.json for your layout."
                ),
            )
        ]

    wages_box = str((config.get("field_map") or {}).get("annual_earned_income", "1"))
    wages = boxes.get(wages_box.lower())
    if wages is None:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label="W-2",
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=f"Found W-2 text but not box {wages_box} wages. Update parser config for your form layout.",
            )
        ]

    field = resolve_field("annual_earned_income", person=person)
    if field is None:
        return []
    field_person = person if field.scope == "person" else None
    raw, display = _format_money(field, wages, person=field_person)
    year = _detect_tax_year(text)
    quote = f"W-2 box {wages_box} wages"
    if year:
        quote = f"{quote} ({year})"
    return [
        ExtractedFieldSuggestion(
            field_key="annual_earned_income",
            person=field_person,
            label=field.label,
            raw_value=raw,
            display_value=display,
            confidence="high",
            source_quote=quote,
            document_type=DocumentType.PDF.value,
        )
    ]


def parse_w2_pdf(path: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
    file_path = Path(path)
    if not file_path.is_file():
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=file_path.name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=f"File not found: {path}",
            )
        ]
    try:
        text = extract_pdf_text(path)
    except Exception as exc:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=file_path.name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=f"Could not read PDF: {exc}",
            )
        ]
    if not text.strip():
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=Path(path).name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes="No readable text in this PDF. Use a digital W-2 export (not a scan).",
            )
        ]
    if not _W2_MARKER.search(text):
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=Path(path).name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes="This file does not look like a Form W-2. Upload a W-2 PDF from your employer.",
            )
        ]
    return extract_w2_suggestions(text, label=Path(path).name, person=person)
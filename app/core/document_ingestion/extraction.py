"""Extracted field suggestions from uploaded documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractedFieldSuggestion:
    field_key: str | None
    person: str | None
    label: str
    raw_value: str
    display_value: str
    confidence: str
    source_quote: str
    document_type: str
    ok: bool = True
    notes: str = ""

    def to_review_row(self, *, source_label: str, apply_default: bool) -> dict[str, Any]:
        return {
            "field_key": self.field_key,
            "person": self.person,
            "label": self.label,
            "suggested": self.display_value,
            "raw_value": self.raw_value,
            "confidence": self.confidence,
            "notes": self.notes or self.source_quote,
            "source_quote": self.source_quote,
            "apply_default": apply_default,
            "apply": apply_default,
            "ok": self.ok and bool(self.field_key) and bool(self.raw_value),
            "source": source_label,
        }
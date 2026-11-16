"""Stub extractor for PDF/image — future LLM structured extraction."""

from __future__ import annotations

from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType


class StubLlmExtractor:
    document_type = DocumentType.PDF

    def can_extract(self, path: str, payload: Any | None = None) -> bool:
        lower = path.lower()
        return lower.endswith((".png", ".jpg", ".jpeg"))

    def extract(self, path: str, payload: Any | None = None) -> list[ExtractedFieldSuggestion]:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label="Document import",
                raw_value="",
                display_value="",
                confidence="low",
                source_quote=path,
                document_type=DocumentType.PDF.value,
                ok=False,
                notes="PDF and image extraction is not available yet. Use tax return JSON for now.",
            )
        ]
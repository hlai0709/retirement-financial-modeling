"""Document extractor protocol — one implementation per document type."""

from __future__ import annotations

from typing import Any, Protocol

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType


class DocumentExtractor(Protocol):
    document_type: DocumentType

    def can_extract(self, path: str, payload: Any | None = None) -> bool:
        ...

    def extract(self, path: str, payload: Any | None = None) -> list[ExtractedFieldSuggestion]:
        ...
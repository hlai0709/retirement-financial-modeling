"""Lazy Mode data types — parser results and failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion


@dataclass
class ParserFailure:
    parser_id: str
    config_version: str
    document_label: str
    user_message: str
    developer_message: str
    config_path: str
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_id": self.parser_id,
            "config_version": self.config_version,
            "document_label": self.document_label,
            "user_message": self.user_message,
            "developer_message": self.developer_message,
            "config_path": self.config_path,
            "excerpt": self.excerpt,
        }


@dataclass
class ParserResult:
    ok: bool
    parser_id: str
    config_version: str
    fields: list[ExtractedFieldSuggestion] = field(default_factory=list)
    failure: ParserFailure | None = None
    document_id: str = ""
    document_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "parser_id": self.parser_id,
            "config_version": self.config_version,
            "document_id": self.document_id,
            "document_label": self.document_label,
            "field_count": len(self.fields),
            "failure": self.failure.to_dict() if self.failure else None,
        }


@dataclass
class LazyIngestResult:
    ok: bool
    message: str
    suggestions: list[ExtractedFieldSuggestion] = field(default_factory=list)
    review_rows: list[dict[str, Any]] = field(default_factory=list)
    failures: list[ParserFailure] = field(default_factory=list)
    parser_results: list[ParserResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "review_rows": self.review_rows,
            "failures": [item.to_dict() for item in self.failures],
            "parser_results": [item.to_dict() for item in self.parser_results],
            "suggestion_count": len(self.suggestions),
        }
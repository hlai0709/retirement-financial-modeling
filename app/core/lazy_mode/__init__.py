"""Lazy Mode — document-driven auto-fill."""

from app.core.lazy_mode.orchestrator import build_lazy_checklist, ingest_lazy_upload
from app.core.lazy_mode.types import LazyIngestResult, ParserFailure, ParserResult

__all__ = [
    "LazyIngestResult",
    "ParserFailure",
    "ParserResult",
    "build_lazy_checklist",
    "ingest_lazy_upload",
]
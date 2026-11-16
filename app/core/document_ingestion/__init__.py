"""Document ingestion — plug-in extractors map uploads to planning fields."""

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.ingestion_controller import apply_document_import, ingest_document
from app.core.document_ingestion.types import DocumentType, supported_types_payload

__all__ = [
    "DocumentType",
    "ExtractedFieldSuggestion",
    "apply_document_import",
    "ingest_document",
    "supported_types_payload",
]
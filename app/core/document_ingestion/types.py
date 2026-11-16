"""Document types for plug-in ingestion — extensible without UI coupling."""

from __future__ import annotations

from enum import Enum


class DocumentType(str, Enum):
    TAX_RETURN_JSON = "tax_return_json"
    PDF = "pdf"
    IMAGE = "image"
    UNKNOWN = "unknown"

    @property
    def mime_hints(self) -> tuple[str, ...]:
        return _MIME_HINTS.get(self, ())

    @property
    def label(self) -> str:
        return _LABELS.get(self, self.value.replace("_", " ").title())


_MIME_HINTS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.TAX_RETURN_JSON: ("application/json", ".json"),
    DocumentType.PDF: ("application/pdf", ".pdf"),
    DocumentType.IMAGE: ("image/png", "image/jpeg", ".png", ".jpg", ".jpeg"),
}

_LABELS: dict[DocumentType, str] = {
    DocumentType.TAX_RETURN_JSON: "Tax return (JSON)",
    DocumentType.PDF: "PDF document",
    DocumentType.IMAGE: "Image scan",
    DocumentType.UNKNOWN: "Unknown format",
}


def supported_types_payload() -> list[dict[str, object]]:
    return [
        {
            "id": DocumentType.TAX_RETURN_JSON.value,
            "label": DocumentType.TAX_RETURN_JSON.label,
            "ready": True,
            "detail": "Structured Form 1040 JSON (see validation/tax_return_template.json)",
        },
        {
            "id": DocumentType.PDF.value,
            "label": "Tax return (PDF)",
            "ready": True,
            "detail": "Form 1040 PDF export (TurboTax, H&R Block, IRS). Scanned PDFs need LLM Settings.",
        },
        {
            "id": DocumentType.IMAGE.value,
            "label": DocumentType.IMAGE.label,
            "ready": False,
            "detail": "Coming soon — photo/scan extraction",
        },
    ]
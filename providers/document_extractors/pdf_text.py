"""Extract plain text from PDF files for document ingestion."""

from __future__ import annotations

from pathlib import Path


def extract_pdf_text(path: str | Path) -> str:
    """Return concatenated text from all pages. Empty string if unreadable."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n".join(parts)
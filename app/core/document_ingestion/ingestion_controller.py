"""Ingest documents and apply extracted field suggestions to PlanningSnapshot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.controllers.llm_research_controller import field_display_label, is_field_empty
from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.field_editor import resolve_field
from app.core.presentation.llm_panel import apply_llm_suggestions
from config.model_fields import FIELDS_BY_KEY
from providers.document_extractors.stub_llm import StubLlmExtractor
from providers.document_extractors.tax_return_json import TaxReturnJsonExtractor
from providers.document_extractors.tax_return_pdf import TaxReturnPdfExtractor


_EXTRACTORS = (TaxReturnJsonExtractor(), TaxReturnPdfExtractor(), StubLlmExtractor())

# Form 1040 / tax-return imports replace any previous tax-return apply (not stacked).
_TAX_RETURN_DOCUMENT_TYPES = frozenset(
    {
        DocumentType.TAX_RETURN_JSON.value,
        DocumentType.PDF.value,
        "tax_return",
        "tax_return_pdf",
        "form_1040",
    }
)


def _is_tax_return_import(*, document_type: str = "", document_label: str = "") -> bool:
    dtype = str(document_type or "").strip().lower()
    if dtype in _TAX_RETURN_DOCUMENT_TYPES:
        return True
    label = str(document_label or "").lower()
    return "1040" in label or "tax return" in label or "tax_return" in label


def _history_entry_is_tax_return(entry: dict[str, Any]) -> bool:
    return _is_tax_return_import(
        document_type=str(entry.get("document_type") or ""),
        document_label=str(entry.get("label") or ""),
    )


def _clear_prior_tax_return_fields(snapshot: PlanningSnapshot, prior_entries: list[dict[str, Any]]) -> int:
    """Clear plan fields that were written by a previous tax-return import."""
    cleared = 0
    for entry in prior_entries:
        for field_info in entry.get("fields") or []:
            field_key = str(field_info.get("field_key") or "").strip()
            if not field_key:
                continue
            person = field_info.get("person")
            field = resolve_field(field_key, person=person) or FIELDS_BY_KEY.get(field_key)
            if field is None:
                continue
            person_scope = person if field.scope == "person" else None
            source = snapshot.get_source(person_scope, field.key)
            # Only wipe values that still look like document imports
            if "Imported" not in str(source) and "import" not in str(source).lower():
                continue
            snapshot.set_raw(field, "", person=person_scope)
            snapshot.set_source(person_scope, field.key, "Manual")
            cleared += 1
    return cleared


def _replace_prior_tax_return_imports(snapshot: PlanningSnapshot) -> tuple[int, int]:
    """Drop prior tax-return history entries and clear their imported field values.

    Returns ``(entries_removed, fields_cleared)``.
    """
    prior = [entry for entry in snapshot.import_history if _history_entry_is_tax_return(entry)]
    if not prior:
        return 0, 0
    fields_cleared = _clear_prior_tax_return_fields(snapshot, prior)
    snapshot.import_history = [
        entry for entry in snapshot.import_history if not _history_entry_is_tax_return(entry)
    ]
    return len(prior), fields_cleared


def _detect_document_type(path: Path, payload: Any | None) -> DocumentType:
    for extractor in _EXTRACTORS:
        if extractor.can_extract(str(path), payload):
            return extractor.document_type
    suffix = path.suffix.lower()
    if suffix == ".json":
        return DocumentType.TAX_RETURN_JSON
    if suffix == ".pdf":
        return DocumentType.PDF
    if suffix in {".png", ".jpg", ".jpeg"}:
        return DocumentType.IMAGE
    return DocumentType.UNKNOWN


def _load_payload(path: Path) -> Any | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _extract(path: Path, payload: Any | None) -> tuple[DocumentType, list[ExtractedFieldSuggestion]]:
    for extractor in _EXTRACTORS:
        if extractor.can_extract(str(path), payload):
            rows = extractor.extract(str(path), payload)
            return extractor.document_type, rows
    return DocumentType.UNKNOWN, []


def _apply_default(snapshot: PlanningSnapshot, suggestion: ExtractedFieldSuggestion) -> bool:
    if not suggestion.field_key or not suggestion.ok:
        return False
    field = resolve_field(suggestion.field_key, person=suggestion.person)
    if field is None:
        field = FIELDS_BY_KEY.get(suggestion.field_key)
    if field is None:
        return False
    person = suggestion.person if field.scope == "person" else None
    # High-confidence document extracts default on (user can uncheck) so SSA PIA mode
    # overwrites a prior "earnings" mode when importing a statement.
    if (
        suggestion.field_key == "itemized_mortgage_interest"
        and str(suggestion.confidence or "").lower() != "high"
    ):
        # Year-to-date statement interest is not a full-year deduction.
        return False
    if str(suggestion.confidence or "").lower() == "high":
        return True
    return is_field_empty(snapshot, field, person=person)


def build_review_rows(
    snapshot: PlanningSnapshot,
    suggestions: list[ExtractedFieldSuggestion],
    *,
    document_label: str,
) -> list[dict[str, Any]]:
    from app.core.presentation.field_validation import validate_field_raw

    source_label = f"Imported from {document_label}"
    rows: list[dict[str, Any]] = []
    for suggestion in suggestions:
        if not suggestion.field_key:
            rows.append(suggestion.to_review_row(source_label=source_label, apply_default=False))
            continue
        field = resolve_field(suggestion.field_key, person=suggestion.person)
        if field is None:
            continue
        person = suggestion.person if field.scope == "person" else None
        raw = str(suggestion.raw_value or "").strip()
        if raw and validate_field_raw(field, raw, person=person, required=False):
            # Drop junk mappings (e.g. principal balance → mortgage_payoff_year)
            continue
        row = suggestion.to_review_row(
            source_label=source_label,
            apply_default=_apply_default(snapshot, suggestion),
        )
        row["label"] = field_display_label(field, person=person)
        rows.append(row)
    return rows


def ingest_document(snapshot: PlanningSnapshot, path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        return {"ok": False, "message": f"File not found: {path}"}

    payload = _load_payload(file_path)
    doc_type, suggestions = _extract(file_path, payload)

    if doc_type == DocumentType.UNKNOWN:
        return {
            "ok": False,
            "message": "Unsupported document type. Use tax return JSON for now.",
            "document_type": doc_type.value,
        }

    if doc_type == DocumentType.IMAGE:
        stub = suggestions[0] if suggestions else None
        return {
            "ok": False,
            "message": stub.notes if stub else "Image import coming soon.",
            "document_type": doc_type.value,
            "stub": True,
        }

    if suggestions and not suggestions[0].ok:
        return {
            "ok": False,
            "message": suggestions[0].notes or "Could not extract fields from this document.",
            "document_type": doc_type.value,
        }

    if not suggestions:
        return {
            "ok": False,
            "message": "No mappable fields found in this document.",
            "document_type": doc_type.value,
        }

    if doc_type == DocumentType.PDF:
        doc_label = file_path.name
    else:
        doc_label = str((payload or {}).get("label") or file_path.name)
    rows = build_review_rows(snapshot, suggestions, document_label=doc_label)
    if not any(row.get("ok") for row in rows):
        return {
            "ok": False,
            "message": "Document parsed but no fields could be mapped to your plan.",
            "document_type": doc_type.value,
        }

    return {
        "ok": True,
        "message": f"Found {len(rows)} field(s) to review from {doc_label}.",
        "document_type": doc_type.value,
        "document_label": doc_label,
        "rows": rows,
    }


def _remember_property_tax_bills(snapshot: PlanningSnapshot, rows: list[dict[str, Any]]) -> None:
    """Keep each bill amount so a later refresh can replace one bill instead of the sum."""
    for row in rows:
        if not row.get("apply", True):
            continue
        if str(row.get("field_key") or "") != "property_tax_annual":
            continue
        quote = str(row.get("source_quote") or "")
        marker = "bills="
        if marker not in quote:
            continue
        encoded = quote.split(marker, 1)[1].split(";", 1)[0].strip()
        if not encoded:
            continue
        person = row.get("person") if row.get("person") in {"self", "wife"} else "self"
        snapshot.persons.setdefault(person, {})["_property_tax_bills"] = encoded


def apply_document_import(
    snapshot: PlanningSnapshot,
    rows: list[dict[str, Any]],
    *,
    document_type: str = "",
    document_label: str = "",
) -> dict[str, Any]:
    replaced_entries = 0
    cleared_fields = 0
    if _is_tax_return_import(document_type=document_type, document_label=document_label):
        replaced_entries, cleared_fields = _replace_prior_tax_return_imports(snapshot)

    from app.core.lazy_mode.orchestrator import retarget_stacked_import_rows

    rows = retarget_stacked_import_rows(
        snapshot,
        rows,
        document_id=document_type,
    )
    _remember_property_tax_bills(snapshot, rows)
    result = apply_llm_suggestions(snapshot, rows)
    applied = int(result.get("applied", 0))
    field_details: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("apply"):
            continue
        field_key = str(row.get("field_key") or "").strip()
        if not field_key:
            continue
        raw = str(row.get("raw_value", "")).strip()
        if not raw:
            continue
        field_details.append(
            {
                "field_key": field_key,
                "label": str(row.get("label") or field_key),
                "value": str(row.get("suggested") or row.get("display_value") or raw),
                "person": row.get("person"),
                "confidence": str(row.get("confidence") or ""),
                "source_quote": str(row.get("source_quote") or row.get("notes") or ""),
            }
        )
    if document_label or document_type or field_details:
        snapshot.import_history.append(
            {
                "document_type": document_type or "unknown",
                "label": document_label or "document",
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "fields_applied": applied,
                "fields": field_details,
            }
        )
    if applied and snapshot.shared.get("state_of_residence"):
        snapshot.assumption_state = snapshot.shared["state_of_residence"]
    if replaced_entries:
        message = (
            f"Replaced previous tax return ({replaced_entries} prior import"
            f"{'s' if replaced_entries != 1 else ''}). Updated {applied} field(s)."
        )
    else:
        message = f"Updated {applied} field(s) from imported document."
    return {
        "ok": True,
        "applied": applied,
        "message": message,
        "fields": field_details,
        "replaced_prior_tax_returns": replaced_entries,
        "cleared_prior_fields": cleared_fields,
        "import_history": list(snapshot.import_history),
        "changes": list(result.get("changes") or []),
    }

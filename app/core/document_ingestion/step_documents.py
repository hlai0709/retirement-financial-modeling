"""Per-guided-step document recommendations (Lazy capability merged into Guided)."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.lazy_mode.manifest import document_by_id, manifest_documents
from app.core.lazy_mode.orchestrator import build_lazy_checklist
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.field_editor import fields_for_wizard_step, person_for_step
from app.core.ui_groups import FIELD_UI_GROUP, fields_for_ui_group, ui_group_for_field
from config.model_fields import FIELDS_BY_KEY, ModelField
from settings import PROJECT_ROOT

_FIELD_MAP_PATH = PROJECT_ROOT / "config" / "lazy_mode" / "field_document_map.json"

# Guided steps that need fixed document shortcuts even when field maps are sparse
_STEP_DOCUMENT_HINTS: dict[str, list[str]] = {
    "setup": ["form_1040"],
    "timeline": ["form_1040"],
    "income_self": ["form_1040", "pay_stub"],
    "income_wife": ["form_1040", "pay_stub"],
    "assets_self": ["plaid_accounts", "brokerage_statement", "401k_statement"],
    "assets_wife": ["plaid_accounts", "brokerage_statement", "401k_statement"],
    "spending_self": ["plaid_spending"],
    "spending_wife": ["plaid_spending"],
    "healthcare_self": ["form_1040"],
    "healthcare_wife": ["form_1040"],
    "tax_benefits": ["form_1040", "ssa_1099"],
    "tax_wife": ["form_1040", "ssa_1099"],
    "market": ["market_data"],
    "live_data": ["plaid_accounts", "plaid_spending"],
    "import_documents": ["form_1040"],
    "review": ["form_1040", "plaid_accounts", "plaid_spending", "market_data"],
}


@lru_cache(maxsize=1)
def _load_field_document_map() -> dict[str, Any]:
    if not _FIELD_MAP_PATH.is_file():
        return {"mappings": []}
    return json.loads(_FIELD_MAP_PATH.read_text(encoding="utf-8"))


def _field_keys_for_step(step_id: str) -> list[str]:
    fields = fields_for_wizard_step(step_id)
    keys = [field.key for field in fields]
    if step_id == "setup":
        keys = list(dict.fromkeys(["state_of_residence", *keys]))
    return keys


def _document_ids_for_field_key(field_key: str) -> list[str]:
    ids: list[str] = []
    for mapping in _load_field_document_map().get("mappings") or []:
        if mapping.get("field_key") != field_key:
            continue
        for source in mapping.get("sources") or []:
            doc_id = str(source.get("document_id") or "")
            if doc_id and doc_id != "manual_gaps" and doc_id not in ids:
                if source.get("ready") is False:
                    continue
                ids.append(doc_id)
    return ids


def _all_fields_filled_by_document(document_id: str) -> list[dict[str, Any]]:
    """Every catalog field this document can populate (any guided step)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mapping in _load_field_document_map().get("mappings") or []:
        field_key = str(mapping.get("field_key") or "")
        if not field_key or field_key in seen:
            continue
        sources = mapping.get("sources") or []
        if not any(str(src.get("document_id")) == document_id for src in sources):
            continue
        field = FIELDS_BY_KEY.get(field_key)
        if field is None:
            continue
        seen.add(field_key)
        step = FIELD_UI_GROUP.get(field_key) or ui_group_for_field(field)
        rows.append(
            {
                "field_key": field_key,
                "label": field.label,
                "step_id": step,
                "on_current_step": False,
            }
        )
    # Manifest-declared fields as fallback
    doc = document_by_id(document_id) or {}
    for field_key in doc.get("fields") or []:
        if field_key in seen:
            continue
        field = FIELDS_BY_KEY.get(str(field_key))
        if field is None:
            continue
        seen.add(str(field_key))
        step = FIELD_UI_GROUP.get(str(field_key)) or ui_group_for_field(field)
        rows.append(
            {
                "field_key": str(field_key),
                "label": field.label,
                "step_id": step,
                "on_current_step": False,
            }
        )
    return rows


def _status_for_document(snapshot: PlanningSnapshot, doc: dict[str, Any]) -> dict[str, Any]:
    checklist = build_lazy_checklist(snapshot)
    for row in checklist.get("documents") or []:
        if row.get("id") == doc.get("id"):
            return {
                "status": row.get("status", "missing"),
                "status_detail": row.get("status_detail", ""),
            }
    return {"status": "missing", "status_detail": ""}


def build_documents_panel(
    snapshot: PlanningSnapshot,
    *,
    step_id: str,
) -> dict[str, Any]:
    """Documents that help fill the current guided step (and later steps)."""
    step_field_keys = set(_field_keys_for_step(step_id))
    doc_ids: list[str] = []
    for key in step_field_keys:
        for doc_id in _document_ids_for_field_key(key):
            if doc_id not in doc_ids:
                doc_ids.append(doc_id)
    for doc_id in _STEP_DOCUMENT_HINTS.get(step_id, []):
        if doc_id not in doc_ids:
            # Only include if ready / exists in manifest
            manifest_doc = document_by_id(doc_id)
            if manifest_doc is None:
                continue
            if manifest_doc.get("ready") is False and doc_id not in {
                "plaid_accounts",
                "plaid_spending",
                "form_1040",
                "market_data",
            }:
                continue
            doc_ids.append(doc_id)

    # Prefer ready / core docs first
    def sort_key(doc_id: str) -> tuple[int, int, str]:
        doc = document_by_id(doc_id) or {}
        ready = 0 if doc.get("ready", True) is not False else 1
        core = 0 if doc.get("required_for_core") else 1
        return (ready, core, doc_id)

    doc_ids = sorted(doc_ids, key=sort_key)

    documents: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        doc = document_by_id(doc_id)
        if doc is None:
            continue
        if doc.get("ready") is False and doc.get("acquisition") == "upload":
            # Hide not-ready parsers from the guided Documents panel
            continue
        status = _status_for_document(snapshot, doc)
        all_fields = _all_fields_filled_by_document(doc_id)
        for row in all_fields:
            row["on_current_step"] = row["field_key"] in step_field_keys
        this_step = [row for row in all_fields if row["on_current_step"]]
        later = [row for row in all_fields if not row["on_current_step"]]
        documents.append(
            {
                "id": doc_id,
                "label": doc.get("label", doc_id),
                "accountant_prompt": doc.get("accountant_prompt", ""),
                "acquisition": doc.get("acquisition", "upload"),
                "formats": doc.get("formats", []),
                "parser_id": doc.get("parser_id", ""),
                "ready": doc.get("ready", True) is not False,
                "required_for_core": bool(doc.get("required_for_core")),
                "status": status["status"],
                "status_detail": status["status_detail"],
                "fields_this_step": this_step,
                "fields_other_steps": later[:12],
                "other_step_count": max(0, len(later)),
            }
        )

    has_actionable = any(
        doc.get("acquisition") in {"upload", "plaid_link", "plaid_refresh_expenses", "refresh_market"}
        and doc.get("ready", True)
        for doc in documents
    )
    return {
        "step_id": step_id,
        "title": "Documents for this step",
        "intro": (
            "Upload or connect a source below to fill fields on this page. "
            "Values that belong on later steps are applied too — you will see them when you get there."
        ),
        "documents": documents,
        "has_documents": bool(documents),
        "has_actionable": has_actionable,
        "empty_message": (
            "No automatic documents for this step — enter values manually or use AI estimates later."
            if not documents
            else ""
        ),
    }

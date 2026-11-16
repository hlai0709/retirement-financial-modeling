"""Lazy Mode ingest orchestration — documents, Plaid status, auto-fill suggestions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.document_ingestion.ingestion_controller import build_review_rows
from app.core.lazy_mode.manifest import accountant_intro, document_by_id, manifest_documents
from app.core.lazy_mode.parser_registry import parse_uploaded_document
from app.core.lazy_mode.types import LazyIngestResult, ParserFailure
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.live_data_ops import live_status_text
from security.keychain import list_item_ids
from settings import get_settings


def _lazy_document_status(snapshot: PlanningSnapshot, doc: dict[str, Any]) -> dict[str, Any]:
    doc_id = str(doc.get("id", ""))
    acquisition = str(doc.get("acquisition", ""))
    status = "missing"
    detail = ""

    if acquisition.startswith("plaid"):
        items = list_item_ids()
        if not items:
            status = "missing"
            detail = "Not connected"
        elif doc_id == "plaid_accounts":
            from app.core.presentation.live_data_ops import build_plaid_connections_payload

            connections = build_plaid_connections_payload()
            has_assets = any("Plaid" in str(v) for v in snapshot.field_sources.values())
            status = "parsed" if has_assets else "provided"
            detail = connections.get("label") or (live_status_text() if items else "")
        elif doc_id == "plaid_spending":
            has_exp = any(
                "Plaid" in str(v) and "expense" in k.lower()
                for k, v in snapshot.field_sources.items()
            )
            status = "parsed" if has_exp else ("provided" if items else "missing")
            detail = "Refresh expenses after connecting"
        else:
            status = "provided" if items else "missing"
    elif acquisition == "refresh_market":
        market_src = str(snapshot.field_sources.get("shared:inflation_rate", ""))
        has_market = bool(snapshot.shared.get("inflation_rate")) and (
            market_src.startswith("FRED") or "market" in market_src.lower()
        )
        status = "parsed" if has_market else "missing"
        detail = "Click Refresh market"
    elif acquisition == "upload":
        from app.core.lazy_mode.document_library import library_summary_for_document

        lib = library_summary_for_document(snapshot, doc_id)
        if lib["count"]:
            status = lib["status"]
            detail = lib["status_detail"]
        else:
            # Fall back to import history for plans saved before the library existed
            parser_id = str(doc.get("parser_id", ""))
            history = [
                entry
                for entry in snapshot.import_history
                if doc_id in str(entry.get("label", "")).lower()
                or parser_id in str(entry.get("document_type", ""))
                or (
                    doc_id == "form_1040"
                    and str(entry.get("document_type", ""))
                    in {"pdf", "tax_return_json", "tax_return"}
                )
            ]
            if history:
                status = "parsed"
                latest_label = str(history[-1].get("label") or "import")
                if doc_id == "form_1040" or str(doc.get("parser_id", "")) in {
                    "tax_return",
                    "tax_return_json",
                }:
                    detail = f"On file: {latest_label}"
                else:
                    detail = f"{len(history)} import(s) on file"
            else:
                status = "missing"
    elif acquisition == "guided_fallback":
        status = "n/a"
        detail = "Use Guided for remaining fields"

    payload: dict[str, Any] = {
        "id": doc_id,
        "tier": doc.get("tier", "C"),
        "label": doc.get("label", doc_id),
        "accountant_prompt": doc.get("accountant_prompt", ""),
        "acquisition": acquisition,
        "formats": doc.get("formats", []),
        "parser_id": doc.get("parser_id", ""),
        "parser_config_version": doc.get("parser_config_version", ""),
        "config_path": doc.get("config_path", ""),
        "required_for_core": bool(doc.get("required_for_core")),
        "ready": doc.get("ready", True),
        "person_scope": doc.get("person_scope", "household"),
        "status": status,
        "status_detail": detail,
        "fields": list(doc.get("fields") or []),
        "allows_multiple": acquisition == "upload",
    }
    if acquisition == "upload":
        from app.core.lazy_mode.document_library import (
            library_summary_for_document,
            person_label,
        )

        lib = library_summary_for_document(snapshot, doc_id)
        entries = []
        for entry in lib["entries"]:
            entries.append(
                {
                    **entry,
                    "person_label": person_label(entry.get("person")),
                }
            )
        payload["library"] = entries
        payload["library_count"] = lib["count"]
        payload["pending_extract"] = lib["pending_extract"]
        payload["joint_person_picker"] = (
            snapshot.household_mode == "joint"
            and str(doc.get("person_scope") or "") == "person"
        )
    if acquisition.startswith("plaid"):
        from app.core.presentation.live_data_ops import build_plaid_connections_payload

        connections = build_plaid_connections_payload()
        payload["institutions"] = connections.get("institutions") or []
        payload["institutions_label"] = connections.get("label") or ""
    return payload


def build_lazy_checklist(snapshot: PlanningSnapshot) -> dict[str, Any]:
    joint = snapshot.household_mode == "joint"
    documents = [_lazy_document_status(snapshot, doc) for doc in manifest_documents()]
    tier_a = [doc for doc in documents if doc["tier"] == "A"]
    core_required = [doc for doc in tier_a if doc["required_for_core"]]
    core_done = sum(1 for doc in core_required if doc["status"] in {"parsed", "provided", "n/a"})

    return {
        "accountant_intro": accountant_intro(),
        "plaid_ready": get_settings().plaid_configured,
        "documents": documents,
        "tier_a": tier_a,
        "core_progress": {
            "provided": core_done,
            "required": len(core_required),
            "label": f"{core_done}/{len(core_required)} core sources",
        },
        "household_mode": snapshot.household_mode,
        "joint": joint,
    }


def ingest_lazy_upload(
    snapshot: PlanningSnapshot,
    *,
    document_id: str,
    path: str,
    use_local_ai: bool = True,
    person: str | None = None,
    library_entry_id: str | None = None,
) -> dict[str, Any]:
    """Parse upload with deterministic extractors, then optionally local Ollama AI."""
    result = parse_uploaded_document(
        snapshot,
        document_id=document_id,
        path=path,
        person=person,
    )
    fields = list(result.fields or [])
    ai_used = False
    ai_note = ""

    if use_local_ai and Path(path).suffix.lower() == ".pdf":
        try:
            from providers.document_extractors.pdf_text import extract_pdf_text
            from providers.local_doc_ai import extract_fields_with_local_ai, local_ai_available

            if local_ai_available():
                text = extract_pdf_text(path)
                if text.strip():
                    # Always try local AI when the rule parser found nothing (or to fill gaps)
                    ai_fields = extract_fields_with_local_ai(
                        text,
                        document_id=document_id,
                        person=person,
                    )
                    if ai_fields:
                        fields = _merge_field_suggestions(fields, ai_fields)
                        ai_used = True
                        ai_note = (
                            f" Local AI extracted {len(ai_fields)} additional value(s) from the document."
                        )
                    elif not fields:
                        ai_note = " Local AI found no extractable values on this document."
                elif not fields:
                    ai_note = " Local AI skipped (no extractable text in PDF)."
            elif not fields:
                ai_note = " Local AI unavailable (start Ollama on 127.0.0.1:11434)."
        except Exception as exc:
            ai_note = f" Local AI skipped ({exc})."

    if person and fields:
        fields = _apply_person_override(fields, person)

    fields = _sum_additive_income_if_needed(
        snapshot,
        fields,
        document_id=document_id,
        person=person,
    )
    fields = _stack_mortgage_if_needed(
        snapshot,
        fields,
        document_id=document_id,
        person=person,
    )
    fields = _stack_property_tax_if_needed(
        snapshot,
        fields,
        document_id=document_id,
        person=person,
    )

    payload: dict[str, Any] = result.to_dict()
    payload["local_ai_used"] = ai_used
    payload["library_entry_id"] = library_entry_id
    payload["person"] = person
    if fields:
        doc = document_by_id(document_id) or {}
        rows = build_review_rows(
            snapshot,
            fields,
            document_label=result.document_label or path,
        )
        payload["ok"] = True
        # Successful extract (parser and/or local AI) — do not surface parser failure modal
        payload["failure"] = None
        payload["message"] = f"Found {len(rows)} field(s) to review.{ai_note}"
        payload["review_rows"] = rows
        payload["document_type"] = str(doc.get("parser_id", "upload"))
        payload["document_label"] = result.document_label or Path(path).name
        payload["field_count"] = len(fields)
        if library_entry_id:
            from app.core.lazy_mode.document_library import mark_entry_status

            mark_entry_status(
                snapshot,
                library_entry_id,
                status="extracted",
                message=payload["message"],
                field_count=len(fields),
            )
    elif result.failure:
        payload["ok"] = False
        payload["message"] = result.failure.user_message + (ai_note or "")
        payload["failure"] = result.failure.to_dict()
        if library_entry_id:
            from app.core.lazy_mode.document_library import mark_entry_status

            mark_entry_status(
                snapshot,
                library_entry_id,
                status="failed",
                message=payload["message"],
                field_count=0,
            )
    else:
        payload["ok"] = False
        payload["message"] = "No fields extracted." + (ai_note or "")
        payload["failure"] = None
        if library_entry_id:
            from app.core.lazy_mode.document_library import mark_entry_status

            mark_entry_status(
                snapshot,
                library_entry_id,
                status="failed",
                message=payload["message"],
                field_count=0,
            )
    return payload


def extract_library_entry(
    snapshot: PlanningSnapshot,
    entry_id: str,
    *,
    use_local_ai: bool = True,
) -> dict[str, Any]:
    """Extract fields from one library entry (any status — used for refresh too)."""
    from app.core.lazy_mode.document_library import get_entry

    entry = get_entry(snapshot, entry_id)
    if entry is None:
        return {"ok": False, "message": "Document not found in library."}
    path = str(entry.get("stored_path") or "")
    if not path or not Path(path).is_file():
        return {
            "ok": False,
            "message": "Stored file is missing. Use Replace file… or remove and re-add.",
            "library_entry_id": entry_id,
        }
    return ingest_lazy_upload(
        snapshot,
        document_id=str(entry.get("document_id") or ""),
        path=path,
        use_local_ai=use_local_ai,
        person=entry.get("person"),
        library_entry_id=entry_id,
    )


def refresh_library_entry(
    snapshot: PlanningSnapshot,
    entry_id: str,
    *,
    use_local_ai: bool = True,
) -> dict[str, Any]:
    """Re-queue and re-extract an already imported file with current parsers."""
    from app.core.lazy_mode.document_library import queue_for_refresh

    queued = queue_for_refresh(snapshot, entry_id)
    if not queued.get("ok"):
        return queued
    result = extract_library_entry(
        snapshot,
        entry_id,
        use_local_ai=use_local_ai,
    )
    if result.get("ok"):
        result["message"] = (
            "Refreshed — review fields below. "
            + str(result.get("message") or "")
        ).strip()
    return result


def extract_library_pending(
    snapshot: PlanningSnapshot,
    *,
    document_id: str | None = None,
    use_local_ai: bool = True,
    limit: int = 20,
    include_applied: bool = False,
) -> dict[str, Any]:
    """Extract queued/failed library files (optionally refresh applied/extracted too)."""
    from app.core.lazy_mode.document_library import list_library, prune_duplicate_entries

    prune_duplicate_entries(snapshot, document_id=document_id)

    allowed = {"queued", "failed"}
    if include_applied:
        allowed |= {"extracted", "applied"}
    pending = [
        e
        for e in list_library(snapshot, document_id=document_id)
        if str(e.get("status")) in allowed
    ][: max(1, limit)]
    results: list[dict[str, Any]] = []
    for entry in pending:
        if include_applied and str(entry.get("status")) in {"extracted", "applied"}:
            results.append(
                refresh_library_entry(
                    snapshot,
                    str(entry["id"]),
                    use_local_ai=use_local_ai,
                )
            )
        else:
            results.append(
                extract_library_entry(
                    snapshot,
                    str(entry["id"]),
                    use_local_ai=use_local_ai,
                )
            )
    ok_count = sum(1 for r in results if r.get("ok"))
    review_batches = [r for r in results if r.get("ok") and r.get("review_rows")]
    verb = "Refreshed" if include_applied else "Extracted"
    return {
        "ok": ok_count > 0 or not results,
        "message": (
            f"{verb} {ok_count} of {len(results)} file(s)."
            if results
            else ("Nothing on file to refresh." if include_applied else "Nothing queued to extract.")
        ),
        "results": results,
        "review_batches": review_batches,
        "processed": len(results),
    }


def _apply_person_override(fields: list, person: str | None) -> list:
    if not person or person not in {"self", "wife"}:
        return fields
    from dataclasses import replace

    from config.model_fields import FIELDS_BY_KEY

    out = []
    for item in fields:
        key = str(getattr(item, "field_key", "") or "")
        if not key:
            out.append(item)
            continue
        field = FIELDS_BY_KEY.get(key)
        if field is not None and field.scope != "person":
            out.append(item)
            continue
        out.append(replace(item, person=person))
    return out


def _sum_additive_income_if_needed(
    snapshot: PlanningSnapshot,
    fields: list,
    *,
    document_id: str,
    person: str | None,
) -> list:
    """When stacking multiple W-2s, suggest income = existing imported + new box 1."""
    if document_id not in {"w2"} or not fields:
        return fields
    from dataclasses import replace

    from config.field_values import format_field_value, parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    field_def = FIELDS_BY_KEY.get("annual_earned_income")
    if field_def is None:
        return fields
    person_scope = person if person in {"self", "wife"} else "self"
    existing_raw = snapshot.get_raw(field_def, person=person_scope)
    source = snapshot.get_source(person_scope, "annual_earned_income")
    try:
        existing = float(parse_field_value(field_def, existing_raw) or 0)
    except Exception:
        existing = 0.0
    if existing <= 0:
        return fields
    # Only stack when prior value looks imported (another W-2 / pay stub)
    src = str(source or "").lower()
    if "import" not in src and "w-2" not in src and "w2" not in src:
        return fields

    out = []
    for item in fields:
        if getattr(item, "field_key", None) != "annual_earned_income":
            out.append(item)
            continue
        try:
            new_val = float(parse_field_value(field_def, str(getattr(item, "raw_value", "") or "0")))
        except Exception:
            out.append(item)
            continue
        total = existing + new_val
        quote = str(getattr(item, "source_quote", "") or "")
        out.append(
            replace(
                item,
                raw_value=format_field_value(field_def, total),
                display_value=f"${total:,.0f}",
                source_quote=(
                    f"{quote}; summed with existing ${existing:,.0f} from prior import"
                ).strip("; "),
            )
        )
    return out


def _stack_property_tax_if_needed(
    snapshot: PlanningSnapshot,
    fields: list,
    *,
    document_id: str,
    person: str | None,
) -> list:
    """Stack another property-tax bill: sum into Property Tax Annual."""
    if document_id not in {"property_tax_bill", "form_1040"} or not fields:
        return fields
    from dataclasses import replace

    from config.field_values import format_field_value, parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    person_scope = person if person in {"self", "wife"} else "self"
    field_def = FIELDS_BY_KEY.get("property_tax_annual")
    if field_def is None:
        return fields
    try:
        existing = float(
            parse_field_value(
                field_def,
                snapshot.get_raw(field_def, person=person_scope),
            )
            or 0
        )
    except Exception:
        existing = 0.0
    src = str(snapshot.get_source(person_scope, "property_tax_annual") or "").lower()
    # Only stack bill-on-bill; a fresh 1040 can replace when nothing imported yet
    stack = existing > 0 and document_id == "property_tax_bill" and (
        "import" in src or "property" in src or "tax" in src or "bill" in src
    )
    if not stack:
        return fields
    out = []
    for item in fields:
        if getattr(item, "field_key", None) != "property_tax_annual":
            out.append(item)
            continue
        try:
            new_val = float(
                parse_field_value(field_def, str(getattr(item, "raw_value", "") or "0"))
            )
        except Exception:
            out.append(item)
            continue
        total = existing + new_val
        quote = str(getattr(item, "source_quote", "") or "")
        out.append(
            replace(
                item,
                raw_value=format_field_value(field_def, total),
                display_value=f"${total:,.0f}",
                source_quote=(
                    f"{quote}; summed with existing ${existing:,.0f} (multiple properties)"
                ).strip("; "),
            )
        )
    return out


def _stack_mortgage_if_needed(
    snapshot: PlanningSnapshot,
    fields: list,
    *,
    document_id: str,
    person: str | None,
) -> list:
    """Stack a second mortgage import: sum annual payments, keep later payoff year."""
    if document_id not in {"form_1098", "mortgage_statement"} or not fields:
        return fields
    from dataclasses import replace

    from config.field_values import format_field_value, parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    person_scope = person if person in {"self", "wife"} else "self"
    annual_field = FIELDS_BY_KEY.get("mortgage_annual")
    payoff_field = FIELDS_BY_KEY.get("mortgage_payoff_year")
    if annual_field is None:
        return fields

    existing_annual = 0.0
    try:
        existing_annual = float(
            parse_field_value(
                annual_field,
                snapshot.get_raw(annual_field, person=person_scope),
            )
            or 0
        )
    except Exception:
        existing_annual = 0.0
    src = str(snapshot.get_source(person_scope, "mortgage_annual") or "").lower()
    stack_annual = existing_annual > 0 and (
        "import" in src or "mortgage" in src or "1098" in src or "statement" in src
    )

    existing_payoff = None
    if payoff_field is not None:
        try:
            existing_payoff = parse_field_value(
                payoff_field,
                snapshot.get_raw(payoff_field, person=person_scope),
            )
        except Exception:
            existing_payoff = None

    out = []
    for item in fields:
        key = getattr(item, "field_key", None)
        if key == "mortgage_annual" and stack_annual and annual_field is not None:
            try:
                new_val = float(
                    parse_field_value(
                        annual_field,
                        str(getattr(item, "raw_value", "") or "0"),
                    )
                )
            except Exception:
                out.append(item)
                continue
            total = existing_annual + new_val
            quote = str(getattr(item, "source_quote", "") or "")
            out.append(
                replace(
                    item,
                    raw_value=format_field_value(annual_field, total),
                    display_value=f"${total:,.0f}",
                    source_quote=(
                        f"{quote}; summed with existing ${existing_annual:,.0f} "
                        f"(multiple mortgages)"
                    ).strip("; "),
                )
            )
            continue
        if key == "mortgage_payoff_year" and payoff_field is not None and existing_payoff:
            try:
                new_year = int(
                    parse_field_value(
                        payoff_field,
                        str(getattr(item, "raw_value", "") or "0"),
                    )
                    or 0
                )
            except Exception:
                out.append(item)
                continue
            later = max(int(existing_payoff), new_year)
            quote = str(getattr(item, "source_quote", "") or "")
            out.append(
                replace(
                    item,
                    raw_value=format_field_value(payoff_field, later),
                    display_value=str(later),
                    source_quote=(
                        f"{quote}; using later payoff {later} across mortgages"
                    ).strip("; "),
                )
            )
            continue
        out.append(item)
    return out


def _merge_field_suggestions(
    primary: list,
    secondary: list,
) -> list:
    """Prefer deterministic primary when same field+person; append unique AI hits."""
    seen: set[tuple[str, str | None]] = set()
    out: list = []
    for item in primary:
        key = (str(getattr(item, "field_key", "") or ""), getattr(item, "person", None))
        if not key[0]:
            continue
        seen.add(key)
        out.append(item)
    for item in secondary:
        key = (str(getattr(item, "field_key", "") or ""), getattr(item, "person", None))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def collect_parser_failure_help(failure: dict[str, Any]) -> str:
    lines = [
        str(failure.get("user_message", "Parser failed.")),
        "",
        "To update the parser when forms change:",
        f"  {failure.get('config_path', 'config/lazy_mode/parsers/')}",
        "",
        str(failure.get("developer_message", "")),
    ]
    excerpt = failure.get("excerpt")
    if excerpt:
        lines.extend(["", "Extracted text preview:", excerpt[:300]])
    return "\n".join(lines)

def build_import_summary(snapshot: PlanningSnapshot) -> dict[str, Any]:
    """Human-readable summary of applied imports for Lazy Mode review UI."""
    entries: list[dict[str, Any]] = []
    total_fields = 0
    for entry in snapshot.import_history or []:
        fields = list(entry.get("fields") or [])
        count = int(entry.get("fields_applied") or len(fields) or 0)
        total_fields += count
        imported_at = str(entry.get("imported_at") or "")
        when = imported_at[:16].replace("T", " ") if imported_at else ""
        entries.append(
            {
                "label": str(entry.get("label") or "document"),
                "document_type": str(entry.get("document_type") or ""),
                "imported_at": imported_at,
                "when": when,
                "fields_applied": count,
                "fields": fields,
            }
        )

    live_fields = _live_imported_fields(snapshot)
    return {
        "entries": entries,
        "entry_count": len(entries),
        "total_fields": total_fields,
        "live_fields": live_fields,
        "has_imports": bool(entries) or bool(live_fields),
    }


def _live_imported_fields(snapshot: PlanningSnapshot) -> list[dict[str, Any]]:
    """Current plan values whose source is an import (survives old history without field lists)."""
    from app.core.controllers.llm_research_controller import field_display_label
    from app.core.presentation.field_editor import resolve_field
    from config.model_fields import FIELDS_BY_KEY

    items: list[dict[str, Any]] = []
    for source_key, source in (snapshot.field_sources or {}).items():
        src = str(source or "")
        if "Imported from" not in src and not src.lower().startswith("import"):
            continue
        person: str | None = None
        field_key = str(source_key)
        if ":" in field_key:
            left, right = field_key.split(":", 1)
            if left in {"self", "wife"}:
                person, field_key = left, right
            elif left == "shared":
                person, field_key = None, right
        field = resolve_field(field_key, person=person) or FIELDS_BY_KEY.get(field_key)
        if field is None:
            continue
        person_scope = person if field.scope == "person" else None
        raw = snapshot.get_raw(field, person=person_scope)
        if raw is None or str(raw).strip() == "":
            continue
        items.append(
            {
                "field_key": field.key,
                "label": field_display_label(field, person=person_scope),
                "value": str(raw),
                "person": person_scope,
                "source": src,
            }
        )
    items.sort(key=lambda row: str(row.get("label") or ""))
    return items

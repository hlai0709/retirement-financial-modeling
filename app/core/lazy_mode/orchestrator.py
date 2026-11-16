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


def _same_loan(
    existing_payment: float,
    existing_year: int,
    new_payment: float,
    new_year: int,
) -> bool:
    """A refinance keeps the maturity. A similar payment with a different maturity does not.

    Two loans that mature within one year share a slot, even when the payment
    changed by more than 12%. A piggyback opened with the same payoff year
    therefore replaces the first loan. A two-year gap uses the payment test.
    """
    if existing_payment <= 0 or new_payment <= 0:
        return False
    if existing_year > 0 and new_year > 0:
        gap = abs(existing_year - new_year)
        if gap <= 1:
            return True
        if gap >= 3:
            return False
    return _amounts_within(existing_payment, new_payment)


def _snapshot_money(snapshot: PlanningSnapshot, key: str, person: str) -> float:
    from config.field_values import parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return 0.0
    try:
        return float(parse_field_value(field, snapshot.get_raw(field, person=person)) or 0)
    except Exception:
        return 0.0


def _snapshot_year(snapshot: PlanningSnapshot, key: str, person: str) -> int:
    from config.field_values import parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return 0
    try:
        parsed = parse_field_value(field, snapshot.get_raw(field, person=person))
    except Exception:
        return 0
    try:
        year = int(float(parsed or 0))
    except (TypeError, ValueError):
        return 0
    return year if 1900 <= year <= 2100 else 0


def _row_amount(fields: list, key: str) -> float:
    from config.field_values import parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return 0.0
    for item in fields:
        if getattr(item, "field_key", None) != key:
            continue
        try:
            return float(parse_field_value(field, str(getattr(item, "raw_value", "") or "0")) or 0)
        except Exception:
            return 0.0
    return 0.0


def _row_year(fields: list, key: str) -> int:
    from config.field_values import parse_field_value
    from config.model_fields import FIELDS_BY_KEY

    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return 0
    for item in fields:
        if getattr(item, "field_key", None) != key:
            continue
        try:
            parsed = parse_field_value(field, str(getattr(item, "raw_value", "") or ""))
            year = int(float(parsed or 0))
        except Exception:
            return 0
        return year if 1900 <= year <= 2100 else 0
    return 0


def _bill_components(snapshot: PlanningSnapshot, person: str, existing_total: float) -> list[float]:
    raw = ""
    persons = getattr(snapshot, "persons", {}) or {}
    bucket = persons.get(person) or {}
    raw = str(bucket.get("_property_tax_bills") or "")
    bills: list[float] = []
    for part in raw.split(","):
        try:
            value = float(part)
        except ValueError:
            continue
        if value > 0:
            bills.append(value)
    if not bills and existing_total > 0:
        bills = [existing_total]
    return bills


def _bills_for_total(
    snapshot: PlanningSnapshot,
    person: str,
    existing_total: float,
) -> list[float]:
    """Components that still add up to the stored property-tax total.

    A manual edit or the spending builder can change the total and leave the
    old split behind. That split is ignored until imports record a new one.
    A total with no list is one bill, so a very different bill is added to it.
    """
    bills = _bill_components(snapshot, person, existing_total)
    if existing_total <= 0:
        return []
    if abs(sum(bills) - existing_total) > 1.0:
        return [existing_total]
    return bills


def _store_property_tax_bills(
    snapshot: PlanningSnapshot,
    person: str,
    bills: list[float],
) -> None:
    if person not in {"self", "wife"}:
        return
    bucket = snapshot.persons.setdefault(person, {})
    if bills:
        bucket["_property_tax_bills"] = _bill_list(bills)
    else:
        bucket.pop("_property_tax_bills", None)


def drop_property_tax_component(
    snapshot: PlanningSnapshot,
    person: str,
    component_text: str,
    applied_text: str = "",
) -> bool:
    """Remove one stored bill and set the total to what remains.

    A single stored bill normally falls through to the ordinary restore, which
    puts back the value from before that import. The exception is the second
    delete: the other bill is already gone, so the field shows this bill alone
    and no longer equals the sum this import wrote. That last bill is cleared.
    """
    from config.field_values import format_field_value
    from config.model_fields import FIELDS_BY_KEY

    field = FIELDS_BY_KEY.get("property_tax_annual")
    if field is None or person not in {"self", "wife"}:
        return False
    try:
        component = float(str(component_text).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return False
    if component <= 0:
        return False
    total = _snapshot_money(snapshot, "property_tax_annual", person)
    bills = _bill_components(snapshot, person, total)
    if abs(sum(bills) - total) > 1.0:
        return False
    matched = next((index for index, bill in enumerate(bills) if abs(bill - component) <= 1.0), None)
    if matched is None:
        return False
    if len(bills) == 1:
        try:
            applied = float(str(applied_text).replace(",", "").replace("$", "").strip() or 0)
        except ValueError:
            return False
        # The field still shows exactly what this import wrote, so the
        # ordinary restore can put back the earlier value.
        if applied <= 0 or abs(total - applied) <= 1.0:
            return False
        snapshot.set_raw(field, format_field_value(field, 0.0), person=person)
        snapshot.set_source(person, field.key, "Manual")
        _store_property_tax_bills(snapshot, person, [])
        return True
    del bills[matched]
    snapshot.set_raw(field, format_field_value(field, sum(bills)), person=person)
    _store_property_tax_bills(snapshot, person, bills)
    return True


def replace_property_tax_bill_list(
    snapshot: PlanningSnapshot,
    person: str,
    total: float,
) -> None:
    """After a full restore, the old split no longer describes the total."""
    if total > 1.0:
        _store_property_tax_bills(snapshot, person, [total])
    else:
        _store_property_tax_bills(snapshot, person, [])


def _bill_list(bills: list[float]) -> str:
    parts = []
    for value in bills:
        if abs(value - round(value)) < 1e-6:
            parts.append(str(int(round(value))))
        else:
            parts.append(f"{value:.2f}")
    return ",".join(parts)


def _amounts_within(existing: float, new: float, tolerance: float = 0.12) -> bool:
    """True when a new import looks like a refresh of the same bill, not a second one."""
    if existing <= 0 or new <= 0:
        return False
    return abs(existing - new) / max(existing, new) <= tolerance


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
    from config.field_values import parse_field_value
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
        return _tag_single_property_tax_bill(fields, field_def)
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
        bills = _bills_for_total(snapshot, person_scope, existing)
        matched = None
        closest = None
        for index, bill in enumerate(bills):
            if not _amounts_within(bill, new_val):
                continue
            gap = abs(bill - new_val) / max(bill, new_val)
            if closest is None or gap < closest:
                closest = gap
                matched = index
        if matched is not None:
            bills[matched] = new_val
            note = "refreshed one property-tax bill"
        else:
            bills.append(new_val)
            note = f"summed with existing ${existing:,.0f} (multiple properties)"
        out.append(
            _property_tax_row(
                item,
                field_def,
                new_val=new_val,
                bills=bills,
                note=note,
                include_list=True,
            )
        )
    return out


def _tag_single_property_tax_bill(fields: list, field_def) -> list:
    """Name the one bill. Do not mark it summed, so a later apply can still stack."""
    from config.field_values import parse_field_value

    out = []
    for item in fields:
        if getattr(item, "field_key", None) != "property_tax_annual":
            out.append(item)
            continue
        try:
            new_val = float(parse_field_value(field_def, str(getattr(item, "raw_value", "") or "0")))
        except Exception:
            out.append(item)
            continue
        if new_val <= 0:
            out.append(item)
            continue
        out.append(
            _property_tax_row(
                item,
                field_def,
                new_val=new_val,
                bills=[new_val],
                note="",
                include_list=False,
            )
        )
    return out


def _property_tax_row(
    item,
    field_def,
    *,
    new_val: float,
    bills: list[float],
    note: str,
    include_list: bool,
):
    from dataclasses import replace

    from config.field_values import format_field_value

    quote = str(getattr(item, "source_quote", "") or "")
    total = sum(bills)
    parts = [quote, note, f"component={_bill_list([new_val])}"]
    if include_list:
        parts.append(f"bills={_bill_list(bills)}")
    return replace(
        item,
        raw_value=format_field_value(field_def, total),
        display_value=f"${total:,.0f}",
        source_quote="; ".join(part for part in parts if part),
    )


def _retarget_loan_fields(item, *, slot: str, quote_note: str):
    from dataclasses import replace

    key = getattr(item, "field_key", None)
    mapping = {
        "mortgage_annual": "mortgage_second_annual",
        "mortgage_payoff_year": "mortgage_second_payoff_year",
        "mortgage_payoff_month": "mortgage_second_payoff_month",
        "itemized_mortgage_interest": "itemized_mortgage_interest_second",
    }
    if slot != "second" or key not in mapping:
        return item
    quote = str(getattr(item, "source_quote", "") or "")
    return replace(
        item,
        field_key=mapping[key],
        source_quote=f"{quote}; {quote_note}".strip("; "),
    )


def _stack_mortgage_if_needed(
    snapshot: PlanningSnapshot,
    fields: list,
    *,
    document_id: str,
    person: str | None,
) -> list:
    """Keep a different mortgage on its own payment and payoff, apart from the first loan.

    A statement is the same loan when the maturity matches, or when the payment
    is within about 12% and the maturity is unknown. A refresh of the second
    slot replaces that slot. It is not added on as a third loan.
    """
    if document_id not in {"form_1098", "mortgage_statement"} or not fields:
        return fields
    from dataclasses import replace

    from config.field_values import format_field_value
    from config.model_fields import FIELDS_BY_KEY

    person_scope = person if person in {"self", "wife"} else "self"
    first_payment = _snapshot_money(snapshot, "mortgage_annual", person_scope)
    first_year = _snapshot_year(snapshot, "mortgage_payoff_year", person_scope)
    second_payment = _snapshot_money(snapshot, "mortgage_second_annual", person_scope)
    second_year = _snapshot_year(snapshot, "mortgage_second_payoff_year", person_scope)
    src = str(snapshot.get_source(person_scope, "mortgage_annual") or "").lower()
    second_src = str(snapshot.get_source(person_scope, "mortgage_second_annual") or "").lower()
    looks_imported = any(
        token in source
        for source in (src, second_src)
        for token in ("import", "mortgage", "1098", "statement")
    )
    proposed = _row_amount(fields, "mortgage_annual")
    proposed_year = _row_year(fields, "mortgage_payoff_year")
    # refresh_first, refresh_second, add_second, form_first, form_second, form_drop_payoff
    action = "keep"
    if document_id == "mortgage_statement" and looks_imported and proposed > 0:
        if first_payment > 0 and _same_loan(first_payment, first_year, proposed, proposed_year):
            action = "refresh_first"
        elif second_payment > 0 and _same_loan(
            second_payment, second_year, proposed, proposed_year
        ):
            action = "refresh_second"
        elif first_payment > 0:
            action = "add_second"
    elif document_id == "form_1098" and (first_payment > 0 or second_payment > 0):
        matches_first = first_year > 0 and proposed_year > 0 and abs(first_year - proposed_year) <= 1
        matches_second = (
            second_year > 0 and proposed_year > 0 and abs(second_year - proposed_year) <= 1
        )
        new_interest = _row_amount(fields, "itemized_mortgage_interest")
        first_interest = _snapshot_money(snapshot, "itemized_mortgage_interest", person_scope)
        second_interest = _snapshot_money(
            snapshot, "itemized_mortgage_interest_second", person_scope
        )
        if matches_second and not matches_first:
            action = "form_second"
        elif matches_first:
            action = "form_first"
        elif proposed_year > 0:
            # One loan or two, a maturity that matches neither must not rewrite
            # the payoff already on the plan. Interest fills an empty second
            # figure or refreshes a close one.
            if (
                new_interest > 0
                and first_interest > 0
                and _amounts_within(first_interest, new_interest)
            ):
                action = "form_first_interest"
            elif second_interest <= 0 or _amounts_within(second_interest, new_interest):
                action = "form_drop_payoff"
            else:
                action = "form_skip_interest"
        elif (
            new_interest > 0
            and first_interest > 0
            and _amounts_within(first_interest, new_interest)
        ):
            # No payoff on this row. An amount close to both loans stays on
            # the first, which is the field the row already names.
            action = "form_first"
        elif (
            new_interest > 0
            and second_interest > 0
            and _amounts_within(second_interest, new_interest)
        ):
            action = "form_second"
        elif (
            new_interest > 0
            and second_interest <= 0
            and first_interest > 0
            and not _amounts_within(first_interest, new_interest)
        ):
            action = "form_second"
        elif new_interest > 0 and first_interest > 0 and second_interest > 0:
            action = "form_skip_interest"
        elif second_payment <= 0 or first_interest <= 0:
            action = "form_first"
        else:
            action = "form_skip_interest"

    out = []
    for item in fields:
        key = getattr(item, "field_key", None)
        if key == "mortgage_annual" and document_id == "form_1098":
            continue
        if action == "refresh_first" or action == "keep":
            out.append(item)
            continue
        if action == "form_first" or action == "form_first_interest":
            if action == "form_first_interest" and key in {
                "mortgage_payoff_year",
                "mortgage_payoff_month",
            }:
                continue
            out.append(item)
            continue
        if action in {"form_drop_payoff", "form_skip_interest"} and key in {
            "mortgage_payoff_year",
            "mortgage_payoff_month",
        }:
            continue
        if action == "form_skip_interest" and key == "itemized_mortgage_interest":
            continue
        if action == "form_drop_payoff" and key == "itemized_mortgage_interest":
            out.append(_retarget_loan_fields(item, slot="second", quote_note="second loan interest"))
            continue
        if action in {"refresh_second", "form_second"}:
            moved = _retarget_loan_fields(
                item,
                slot="second",
                quote_note="this loan's own payoff" if key != "mortgage_annual" else "refreshed second mortgage",
            )
            if action == "refresh_second" and key == "mortgage_annual":
                second_field = FIELDS_BY_KEY.get("mortgage_second_annual")
                if second_field is not None:
                    moved = replace(
                        moved,
                        raw_value=format_field_value(second_field, proposed),
                        display_value=f"${proposed:,.0f}",
                    )
            out.append(moved)
            continue
        if action == "add_second" and key == "mortgage_annual":
            second_field = FIELDS_BY_KEY.get("mortgage_second_annual")
            if second_field is None:
                out.append(item)
                continue
            total = proposed if second_payment <= 0 else second_payment + proposed
            quote = str(getattr(item, "source_quote", "") or "")
            if second_payment > 0:
                note = (
                    f"added to the second mortgage ${second_payment:,.0f}; "
                    f"kept apart from ${first_payment:,.0f}"
                )
            else:
                note = f"second mortgage, kept apart from ${first_payment:,.0f}"
            out.append(
                replace(
                    item,
                    field_key="mortgage_second_annual",
                    raw_value=format_field_value(second_field, total),
                    display_value=f"${total:,.0f}",
                    source_quote=f"{quote}; {note}".strip("; "),
                )
            )
            continue
        if action == "add_second" and key in {"mortgage_payoff_year", "mortgage_payoff_month"}:
            out.append(
                _retarget_loan_fields(item, slot="second", quote_note="this loan's own payoff")
            )
            continue
        out.append(item)
    return out


def retarget_stacked_import_rows(
    snapshot: PlanningSnapshot,
    rows: list[dict[str, Any]],
    *,
    document_id: str,
) -> list[dict[str, Any]]:
    """Apply the same stack rules at import time as at extract time.

    Two documents can be extracted while the plan is still empty. Each suggestion
    then names the first mortgage or the first tax bill. Applying the second one
    would replace the first. Re-check against the plan as it stands now.
    """
    kind = str(document_id or "")
    if kind not in {"form_1098", "mortgage_statement", "property_tax_bill"}:
        return rows
    from dataclasses import dataclass

    @dataclass
    class _Row:
        field_key: str
        raw_value: str
        source_quote: str = ""
        display_value: str = ""
        index: int = 0

    pending = [dict(row) for row in rows]
    persons: list[str] = []
    for row in pending:
        person = row.get("person") if row.get("person") in {"self", "wife"} else "self"
        if person not in persons:
            persons.append(person)

    mortgage_keys = {
        "mortgage_annual",
        "mortgage_payoff_year",
        "mortgage_payoff_month",
        "itemized_mortgage_interest",
    }
    for person in persons:
        wrapped: list[_Row] = []
        for index, row in enumerate(pending):
            row_person = row.get("person") if row.get("person") in {"self", "wife"} else "self"
            if row_person != person or not row.get("apply", True):
                continue
            key = str(row.get("field_key") or "")
            quote = str(row.get("source_quote") or "")
            if any(
                marker in quote.lower()
                for marker in (
                    "second mortgage",
                    "refreshed second mortgage",
                    "second loan interest",
                    "summed with existing",
                    "refreshed one property-tax bill",
                    "bills=",
                )
            ):
                continue
            if kind == "property_tax_bill" and key != "property_tax_annual":
                continue
            if kind != "property_tax_bill" and key not in mortgage_keys:
                continue
            wrapped.append(
                _Row(
                    field_key=key,
                    raw_value=str(row.get("raw_value") or ""),
                    source_quote=quote,
                    display_value=str(row.get("display_value") or row.get("suggested") or ""),
                    index=index,
                )
            )
        if not wrapped:
            continue
        if kind == "property_tax_bill":
            stacked = _stack_property_tax_if_needed(
                snapshot,
                wrapped,
                document_id=kind,
                person=person,
            )
        else:
            stacked = _stack_mortgage_if_needed(
                snapshot,
                wrapped,
                document_id=kind,
                person=person,
            )
        kept = {item.index: item for item in stacked}
        for item in wrapped:
            row = pending[item.index]
            updated = kept.get(item.index)
            if updated is None:
                row["apply"] = False
                continue
            row["field_key"] = updated.field_key
            row["raw_value"] = updated.raw_value
            if updated.display_value:
                row["display_value"] = updated.display_value
                row["suggested"] = updated.display_value
            if updated.source_quote:
                row["source_quote"] = updated.source_quote
    return pending


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

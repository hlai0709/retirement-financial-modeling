"""LLM batch research payloads — UI-agnostic."""

from __future__ import annotations

from typing import Any

from app.core.controllers.llm_research_controller import (
    LLMResearchBatchRequest,
    LLMResearchController,
    LLMResearchFieldSelection,
    field_display_label,
    is_field_empty,
)
from app.core.planning_snapshot import PlanningSnapshot
from config.field_values import format_field_value
from config.model_fields import FIELDS_BY_KEY, ModelField, llm_researchable_fields
from providers.assumption_research import AssumptionResearchResult
from security.llm_keychain import get_api_key, has_api_key
from settings import get_settings, load_llm_settings


def llm_status_text(*, provider: str, model: str, configured: bool, has_key: bool) -> str:
    if not configured or provider == "none":
        return "LLM not configured — open Settings to add an API key."
    if not has_key:
        return f"{provider} selected — API key required."
    return f"API key configured · {provider} / {model or 'default model'}"


def build_llm_panel_payload(snapshot: PlanningSnapshot) -> dict[str, Any]:
    settings = load_llm_settings()
    configured = settings.configured
    provider = settings.provider
    model = settings.model
    keyed = has_api_key(provider) if configured else False

    fields: list[dict[str, Any]] = []
    for field in llm_researchable_fields():
        if field.scope == "person":
            for person in ("self", "wife"):
                fields.append(_field_row(snapshot, field, person=person))
        else:
            fields.append(_field_row(snapshot, field, person=None))

    return {
        "status": llm_status_text(provider=provider, model=model, configured=configured, has_key=keyed),
        "extra_prompt": snapshot.llm_extra_prompt,
        "provider": provider,
        "model": model,
        "configured": configured,
        "has_key": keyed,
        "fields": fields,
    }


def _field_row(snapshot: PlanningSnapshot, field: ModelField, *, person: str | None) -> dict[str, Any]:
    pid = f"{field.key}:{person}" if person else field.key
    return {
        "id": pid,
        "field_key": field.key,
        "person": person,
        "label": field_display_label(field, person=person),
        "empty": is_field_empty(snapshot, field, person=person),
    }


def _format_suggestion(field: ModelField, amount: float) -> str:
    if field.field_type == "rate_pct":
        return f"{amount * 100:.2f}%"
    if field.field_type in ("money", "int", "age", "year"):
        return format_field_value(field, amount)
    return str(amount)


def serialize_research_results(results: list[AssumptionResearchResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        field = FIELDS_BY_KEY.get(item.field_key)
        label = field_display_label(field, person=item.person) if field else item.field_key
        if item.suggestion is not None and field is not None:
            rows.append(
                {
                    "id": f"{item.field_key}:{item.person}" if item.person else item.field_key,
                    "field_key": item.field_key,
                    "person": item.person,
                    "label": label,
                    "suggested": _format_suggestion(field, item.suggestion.suggested_annual_usd),
                    "confidence": item.suggestion.confidence,
                    "notes": item.suggestion.explanation[:240],
                    "apply_default": True,
                    "ok": True,
                    "raw_value": str(item.suggestion.suggested_annual_usd),
                    "source": (
                        f"{item.suggestion.source_label()} (cached)"
                        if item.from_cache
                        else item.suggestion.source_label()
                    ),
                    "from_cache": item.from_cache,
                }
            )
        else:
            rows.append(
                {
                    "id": f"{item.field_key}:{item.person}" if item.person else item.field_key,
                    "field_key": item.field_key,
                    "person": item.person,
                    "label": label,
                    "suggested": "—",
                    "confidence": "—",
                    "notes": item.error or "Research failed",
                    "apply_default": False,
                    "ok": False,
                    "raw_value": "",
                    "source": "",
                }
            )
    return rows


def run_llm_batch(
    snapshot: PlanningSnapshot,
    *,
    selections: list[dict[str, str | None]],
    extra_prompt: str = "",
) -> dict[str, Any]:
    settings = load_llm_settings()
    snapshot.llm_extra_prompt = extra_prompt.strip()

    if not selections:
        return {"ok": False, "message": "Select at least one field to research."}

    if not settings.configured or not has_api_key(settings.provider):
        return {"ok": False, "message": "Configure LLM Settings and save an API key first.", "level": "warning"}

    request = LLMResearchBatchRequest(
        snapshot=snapshot,
        selections=[
            LLMResearchFieldSelection(field_key=str(row["field_key"]), person=row.get("person"))
            for row in selections
            if row.get("field_key")
        ],
        extra_prompt=snapshot.llm_extra_prompt,
        provider=settings.provider,
        model=settings.model,
    )

    controller = LLMResearchController()
    result = controller.run_batch(
        request,
        api_key=get_api_key(settings.provider),
        fred_api_key=get_settings().fred_api_key,
    )
    if not result.ok and result.payload is None:
        return {"ok": False, "message": result.message or result.title, "title": result.title}

    payload = result.payload
    rows = serialize_research_results(payload.results) if payload else []
    return {
        "ok": result.ok,
        "message": result.message or result.title,
        "title": result.title,
        "rows": rows,
        "succeeded": payload.succeeded if payload else 0,
        "failed": payload.failed if payload else 0,
        "cached": payload.cached if payload else 0,
    }


def apply_llm_suggestions(snapshot: PlanningSnapshot, applications: list[dict[str, Any]]) -> dict[str, Any]:
    from app.core.presentation.field_validation import validate_field_raw
    from config.field_values import format_field_value, parse_field_value

    applied = 0
    skipped = 0
    changes: list[dict[str, Any]] = []
    for row in applications:
        if not row.get("apply"):
            continue
        field = FIELDS_BY_KEY.get(str(row.get("field_key", "")))
        if field is None:
            continue
        raw = str(row.get("raw_value", "")).strip()
        if not raw:
            continue
        person = row.get("person") if field.scope == "person" else None
        if field.field_type == "money":
            raw = raw.replace(",", "").replace("$", "").strip()
        msg = validate_field_raw(field, raw, person=person, required=False)
        if msg:
            skipped += 1
            continue
        try:
            parsed = parse_field_value(field, raw)
        except (TypeError, ValueError):
            skipped += 1
            continue
        # Optional years that parse to blank (0 / out of range) — don't store junk
        if field.field_type == "year" and parsed is None:
            skipped += 1
            continue
        scope = person if field.scope == "person" else None
        previous = snapshot.get_raw(field, person=scope)
        previous_source = snapshot.get_source(scope, field.key)
        written = format_field_value(field, parsed)
        snapshot.set_raw(field, written, person=scope)
        source = str(row.get("source") or "LLM research")
        snapshot.set_source(scope, field.key, source)
        changes.append(
            {
                "field_key": field.key,
                "person": scope,
                "previous": previous,
                "previous_source": previous_source,
                "applied": written,
            }
        )
        if field.key == "mortgage_annual" and person in {"self", "wife"}:
            from app.core.presentation.spending_builder import (
                sync_mortgage_into_spending_breakdown,
            )

            sync_mortgage_into_spending_breakdown(snapshot, person=person)
        if field.key == "property_tax_annual" and person in {"self", "wife"}:
            from app.core.presentation.spending_builder import (
                sync_property_tax_into_spending_breakdown,
            )

            sync_property_tax_into_spending_breakdown(snapshot, person=person)
        applied += 1
    message = f"Updated {applied} field(s) from AI research."
    if skipped:
        message += f" Skipped {skipped} invalid value(s)."
    return {
        "ok": True,
        "applied": applied,
        "skipped": skipped,
        "message": message,
        "changes": changes,
    }
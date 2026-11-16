"""Batch LLM assumption research — orchestration without UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from app.core.action_result import ActionResult
from app.core.planning_snapshot import PlanningSnapshot
from config.field_values import parse_field_value
from config.model_fields import FIELDS_BY_KEY, ModelField, default_display_value
from providers.assumption_research import (
    AssumptionResearchContext,
    AssumptionResearchResult,
    research_assumptions_batch,
)

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class LLMResearchFieldSelection:
    field_key: str
    person: str | None = None


@dataclass
class LLMResearchBatchRequest:
    snapshot: PlanningSnapshot
    selections: list[LLMResearchFieldSelection]
    extra_prompt: str = ""
    provider: str = ""
    model: str = ""


@dataclass
class LLMResearchBatchResult:
    results: list[AssumptionResearchResult]
    succeeded: int
    failed: int
    cached: int = 0


def field_display_label(field: ModelField, *, person: str | None = None) -> str:
    if person:
        return f"{person.title()} — {field.label}"
    return field.label


def is_field_empty(snapshot: PlanningSnapshot, field: ModelField, *, person: str | None = None) -> bool:
    """True when the plan still looks unset for this field.

    Catalog defaults (e.g. state ``CA``) count as empty for gap/LLM purposes so users
    are nudged to confirm them — **unless** the value was imported or set by a live
    source (Plaid, document, market refresh). Matching the default after a 1040 import
    of California must not show as a gap.
    """
    person_scope = person if field.scope == "person" else None
    raw = snapshot.get_raw(field, person=person_scope).strip()
    if not raw:
        return True
    try:
        parsed = parse_field_value(field, raw)
    except (TypeError, ValueError):
        return False
    if field.field_type in ("money", "rate_pct", "int", "age") and parsed == 0:
        return True
    if field.field_type == "year" and parsed is None:
        return True

    # Document / live data provided a value — not a gap even if it equals catalog default
    source_key = snapshot.source_key(person_scope, field.key)
    if source_key in snapshot.field_sources:
        label = str(snapshot.field_sources.get(source_key) or "").strip()
        if label and label != "Manual":
            return False

    default = str(default_display_value(field, person=person_scope) or "").strip()
    if default and raw == default:
        return True
    return False


class LLMResearchController:
    def build_contexts(self, request: LLMResearchBatchRequest) -> list[AssumptionResearchContext]:
        snapshot = request.snapshot
        extra = request.extra_prompt.strip() or None
        contexts: list[AssumptionResearchContext] = []
        for selection in request.selections:
            field = FIELDS_BY_KEY.get(selection.field_key)
            if field is None:
                continue
            person = selection.person
            ages = snapshot.persons.get(person or "self", {})
            current_age = _optional_int(ages.get("current_age"))
            retirement_age = _optional_int(ages.get("retirement_age"))
            contexts.append(
                AssumptionResearchContext(
                    field_key=selection.field_key,
                    person=person,
                    current_age=current_age,
                    retirement_age=retirement_age,
                    state=snapshot.assumption_state or None,
                    planning_year=date.today().year,
                    fred_api_key=None,
                    extra_prompt=extra,
                )
            )
        return contexts

    def run_batch(
        self,
        request: LLMResearchBatchRequest,
        *,
        api_key: str | None = None,
        fred_api_key: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ActionResult:
        if not request.selections:
            return ActionResult.failure("No Fields Selected", "Choose at least one field to research.")
        if not request.provider or request.provider == "none":
            return ActionResult.failure(
                "LLM Not Configured",
                "Open LLM Settings to choose a provider and save an API key.",
                level="warning",
            )
        if not api_key:
            return ActionResult.failure(
                "API Key Required",
                f"Add an API key for {request.provider} in LLM Settings first.",
                level="warning",
            )

        contexts = self.build_contexts(request)
        if fred_api_key:
            contexts = [
                AssumptionResearchContext(
                    field_key=ctx.field_key,
                    person=ctx.person,
                    current_age=ctx.current_age,
                    retirement_age=ctx.retirement_age,
                    state=ctx.state,
                    planning_year=ctx.planning_year,
                    fred_api_key=fred_api_key,
                    extra_prompt=ctx.extra_prompt,
                )
                for ctx in contexts
            ]

        results = research_assumptions_batch(
            contexts,
            provider=request.provider,
            model=request.model,
            api_key=api_key,
            on_progress=on_progress,
        )

        succeeded = sum(1 for item in results if item.suggestion is not None)
        failed = len(results) - succeeded
        cached = sum(1 for item in results if item.from_cache and item.suggestion is not None)
        researched = succeeded - cached
        payload = LLMResearchBatchResult(
            results=results,
            succeeded=succeeded,
            failed=failed,
            cached=cached,
        )
        if succeeded == 0:
            first_error = next((item.error for item in results if item.error), "Unknown error")
            return ActionResult.failure("Research Failed", first_error, payload=payload)
        if cached and researched:
            message = f"Researched {researched} field(s); {cached} from cache"
        elif cached:
            message = f"{cached} field(s) from cache"
        else:
            message = f"Researched {succeeded} field(s)"
        if failed:
            message += f"; {failed} failed"
        return ActionResult.success("Research Complete", message, payload=payload)


def _optional_int(raw: str | None) -> int | None:
    if not raw or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value or None
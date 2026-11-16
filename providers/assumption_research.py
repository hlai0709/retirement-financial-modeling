"""LLM-assisted research for manual planning assumptions (not Plaid financial data)."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable

from config.model_fields import FIELDS_BY_KEY, ModelField
from providers.fred_provider import get_cpi_annual_inflation_history
from providers.llm.base import LLMProvider
from providers.llm.factory import get_llm_provider
from providers.llm.models_catalog import resolve_model_choice
from security.llm_keychain import get_api_key

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a retirement planning research assistant. Use publicly known U.S. cost data, "
    "historical trends, and reasonable forward projections. Respond with ONLY valid JSON — "
    "no markdown fences, no commentary outside the JSON object."
)

def _json_schema_hint(field: ModelField) -> str:
    value_hint = _value_hint(field)
    return (
        "{\n"
        '  "suggested_value": <number>,\n'
        '  "confidence": "high" | "medium" | "low",\n'
        '  "projection_method": "<how you estimated and projected the value>",\n'
        '  "sources": ["<source 1>", "<source 2>"],\n'
        '  "explanation": "<2-4 sentences for the user>"\n'
        "}\n"
        f"For this field: {value_hint}"
    )


@dataclass
class AssumptionResearchContext:
    field_key: str
    person: str | None = None
    current_age: int | None = None
    retirement_age: int | None = None
    state: str | None = None
    planning_year: int | None = None
    fred_api_key: str | None = None
    extra_prompt: str | None = None
    macro_context: str | None = None


@dataclass
class AssumptionSuggestion:
    field_key: str
    suggested_annual_usd: float
    confidence: str
    projection_method: str
    sources: list[str]
    explanation: str
    provider: str
    model: str
    researched_at: str

    def source_label(self) -> str:
        short_date = self.researched_at[:10] if self.researched_at else "unknown"
        return f"LLM {self.provider} ({short_date})"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _medical_inflation_context(fred_api_key: str | None) -> str:
    if not fred_api_key:
        return "Medical inflation reference: unavailable (no FRED key)."
    try:
        history = get_cpi_annual_inflation_history(fred_api_key, years=5)
        lines = [f"  {year}: {rate:.2%}" for year, rate in history]
        return "Recent CPI YoY (FRED, general inflation proxy):\n" + "\n".join(lines)
    except Exception as exc:
        logger.info("FRED context skipped: %s", exc)
        return "Medical inflation reference: unavailable."


def _value_hint(field: ModelField) -> str:
    if field.field_type == "rate_pct":
        return "Return the annual rate as a decimal in suggested_value (e.g. 0.07 for 7%)."
    if field.field_type == "money":
        return "Return a reasonable ANNUAL amount in U.S. dollars in suggested_value."
    if field.field_type == "age":
        return "Return a whole-number age in suggested_value."
    if field.field_type == "year":
        return "Return a four-digit calendar year in suggested_value."
    if field.field_type == "int":
        return "Return a whole number in suggested_value."
    return "Return a reasonable planning value in suggested_value."


def build_macro_context(fred_api_key: str | None) -> str:
    """Fetch CPI context once per batch instead of per field."""
    return _medical_inflation_context(fred_api_key)


def _llm_batch_workers() -> int:
    raw = os.environ.get("RETIREMENT_LLM_WORKERS", "").strip()
    if raw == "0":
        return 1
    if raw.isdigit():
        return max(1, min(int(raw), 6))
    return 4


def _build_user_prompt(field: ModelField, context: AssumptionResearchContext) -> str:
    macro = context.macro_context or _medical_inflation_context(context.fred_api_key)
    person_line = f"Person: {context.person}" if context.person else "Person: household"
    state_line = context.state or "not specified"
    extra = ""
    if context.extra_prompt and context.extra_prompt.strip():
        extra = f"Additional user context:\n{context.extra_prompt.strip()}\n\n"
    return (
        f"Research category: {field.research_category()}\n"
        f"Field: {field.label}\n"
        f"Description: {field.description or field.label}\n"
        f"{person_line}\n"
        f"Current age: {context.current_age}\n"
        f"Retirement age: {context.retirement_age}\n"
        f"Planning year: {context.planning_year}\n"
        f"State (if relevant): {state_line}\n\n"
        f"{extra}"
        f"{macro}\n\n"
        f"{_value_hint(field)} "
        "Use historical cost levels and project forward to the planning year where helpful. "
        "Do NOT include unrelated expenses already modeled elsewhere (base living expenses, "
        "Social Security, pension).\n\n"
        f"Return JSON matching this schema:\n{_json_schema_hint(field)}"
    )


def _extract_json_payload(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise ValueError("LLM response did not contain valid JSON") from None
        return json.loads(match.group(0))


def _parse_suggested_value(payload: dict) -> float:
    if "suggested_value" in payload:
        return float(payload["suggested_value"])
    if "suggested_annual_usd" in payload:
        return float(payload["suggested_annual_usd"])
    raise ValueError("LLM response must include suggested_value")


def _parse_suggestion(payload: dict, *, field_key: str, provider: str, model: str) -> AssumptionSuggestion:
    amount = _parse_suggested_value(payload)
    if amount < 0:
        raise ValueError("suggested_value must be non-negative")

    confidence = str(payload.get("confidence", "medium")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        sources = [str(sources)]
    sources = [str(source) for source in sources if source]

    return AssumptionSuggestion(
        field_key=field_key,
        suggested_annual_usd=amount,
        confidence=confidence,
        projection_method=str(payload.get("projection_method", "")).strip(),
        sources=sources,
        explanation=str(payload.get("explanation", "")).strip(),
        provider=provider,
        model=model,
        researched_at=_utc_now_iso(),
    )


@dataclass
class AssumptionResearchResult:
    field_key: str
    person: str | None
    suggestion: AssumptionSuggestion | None = None
    error: str | None = None
    from_cache: bool = False


def _is_retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in ("429", "502", "503", "504", "timeout", "timed out", "connection")
    )


def _complete_with_retry(
    llm: LLMProvider,
    *,
    system: str,
    user: str,
    model: str,
    max_attempts: int = 3,
    max_tokens: int | None = None,
) -> object:
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return llm.complete(
                system=system,
                user=user,
                model=model,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts - 1 or not _is_retryable(exc):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("LLM request failed")


def research_assumptions_batch(
    contexts: list[AssumptionResearchContext],
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[AssumptionResearchResult]:
    """Research multiple fields in parallel; failures do not stop the batch."""
    if not contexts:
        return []

    resolved_key = api_key or get_api_key(provider)
    if not resolved_key:
        raise RuntimeError(
            f"No API key stored for {provider}. Add your key in LLM Settings first."
        )

    resolved_model = resolve_model_choice(provider, model, api_key=resolved_key)
    from providers.llm_research_cache import LLMResearchCache

    cache = LLMResearchCache()
    llm = get_llm_provider(provider, resolved_key)
    macro = build_macro_context(contexts[0].fred_api_key)
    prepared = [
        AssumptionResearchContext(
            field_key=ctx.field_key,
            person=ctx.person,
            current_age=ctx.current_age,
            retirement_age=ctx.retirement_age,
            state=ctx.state,
            planning_year=ctx.planning_year,
            fred_api_key=ctx.fred_api_key,
            extra_prompt=ctx.extra_prompt,
            macro_context=macro,
        )
        for ctx in contexts
    ]

    total = len(prepared)
    workers = min(_llm_batch_workers(), total)
    if workers <= 1:
        results: list[AssumptionResearchResult] = []
        for index, context in enumerate(prepared, start=1):
            if on_progress is not None:
                on_progress(index, total, context.field_key)
            results.append(
                _research_one(
                    context,
                    llm=llm,
                    resolved_model=resolved_model,
                    provider=provider,
                    cache=cache,
                )
            )
        cache.save()
        return results

    ordered: list[AssumptionResearchResult | None] = [None] * total
    completed = 0
    progress_lock = Lock()

    def _run(context: AssumptionResearchContext, position: int) -> tuple[int, AssumptionResearchResult]:
        result = _research_one(
            context,
            llm=llm,
            resolved_model=resolved_model,
            provider=provider,
            cache=cache,
        )
        nonlocal completed
        with progress_lock:
            completed += 1
            if on_progress is not None:
                on_progress(completed, total, context.field_key)
        return position, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run, context, index): index
            for index, context in enumerate(prepared)
        }
        for future in as_completed(futures):
            position, result = future.result()
            ordered[position] = result

    cache.save()
    return [item for item in ordered if item is not None]


def _research_one(
    context: AssumptionResearchContext,
    *,
    llm: LLMProvider,
    resolved_model: str,
    provider: str,
    cache: object | None = None,
) -> AssumptionResearchResult:
    try:
        suggestion, from_cache = research_assumption(
            context,
            llm=llm,
            resolved_model=resolved_model,
            provider=provider,
            cache=cache,
        )
        return AssumptionResearchResult(
            field_key=context.field_key,
            person=context.person,
            suggestion=suggestion,
            from_cache=from_cache,
        )
    except Exception as exc:
        return AssumptionResearchResult(
            field_key=context.field_key,
            person=context.person,
            error=str(exc),
        )


def research_assumption(
    context: AssumptionResearchContext,
    *,
    provider: str = "",
    model: str = "",
    api_key: str | None = None,
    llm: LLMProvider | None = None,
    resolved_model: str | None = None,
    cache: object | None = None,
) -> tuple[AssumptionSuggestion, bool]:
    """Query the LLM for a researched assumption suggestion. Returns (suggestion, from_cache)."""
    field = FIELDS_BY_KEY.get(context.field_key)
    if field is None:
        raise ValueError(f"Unknown model field: {context.field_key!r}")
    if not field.supports_llm_research():
        raise ValueError(f"Field {field.label!r} does not support LLM research.")

    if llm is None:
        resolved_key = api_key or get_api_key(provider)
        if not resolved_key:
            raise RuntimeError(
                f"No API key stored for {provider}. Add your key in LLM Settings first."
            )
        resolved_model = resolve_model_choice(provider, model, api_key=resolved_key)
        llm = get_llm_provider(provider, resolved_key)
    elif resolved_model is None:
        raise ValueError("resolved_model is required when llm is provided")

    from providers.llm_research_cache import LLMResearchCache

    active_provider = provider or getattr(llm, "provider", "") or ""
    active_cache = cache if isinstance(cache, LLMResearchCache) else LLMResearchCache()
    owns_cache = cache is None

    hit = active_cache.lookup(context, provider=active_provider, model=resolved_model)
    if hit.suggestion is not None:
        if owns_cache:
            active_cache.save()
        return hit.suggestion, True

    completion = _complete_with_retry(
        llm,
        system=SYSTEM_PROMPT,
        user=_build_user_prompt(field, context),
        model=resolved_model,
    )
    payload = _extract_json_payload(completion.content)
    suggestion = _parse_suggestion(
        payload,
        field_key=context.field_key,
        provider=completion.provider,
        model=completion.model,
    )
    active_cache.store(
        hit.cache_key,
        context,
        suggestion,
        provider=active_provider,
        model=resolved_model,
    )
    if owns_cache:
        active_cache.save()
    return suggestion, False
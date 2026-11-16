"""Disk cache for LLM assumption research — skips repeat API calls for unchanged context."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from providers.assumption_research import AssumptionResearchContext, AssumptionSuggestion

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
DEFAULT_TTL_DAYS = 7
MAX_ENTRIES = 500

_file_lock = Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cache_ttl_days() -> int:
    raw = os.environ.get("RETIREMENT_LLM_CACHE_DAYS", "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 90))
    return DEFAULT_TTL_DAYS


def cache_enabled() -> bool:
    return os.environ.get("RETIREMENT_LLM_CACHE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _cache_path():
    from settings import ensure_app_data_dir, llm_research_cache_path

    ensure_app_data_dir()
    return llm_research_cache_path()


def _load_cache_file() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "entries": {}}
    if not isinstance(data, dict):
        return {"version": CACHE_VERSION, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"version": CACHE_VERSION, "entries": entries}


def _save_cache_file(data: dict[str, Any]) -> None:
    path = _cache_path()
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _suggestion_to_dict(suggestion: AssumptionSuggestion) -> dict[str, Any]:
    return asdict(suggestion)


def suggestion_from_dict(payload: dict[str, Any]) -> AssumptionSuggestion:
    return AssumptionSuggestion(
        field_key=str(payload["field_key"]),
        suggested_annual_usd=float(payload["suggested_annual_usd"]),
        confidence=str(payload.get("confidence", "medium")),
        projection_method=str(payload.get("projection_method", "")),
        sources=[str(source) for source in payload.get("sources", []) if source],
        explanation=str(payload.get("explanation", "")),
        provider=str(payload.get("provider", "")),
        model=str(payload.get("model", "")),
        researched_at=str(payload.get("researched_at", "")),
    )


def context_cache_key(
    context: AssumptionResearchContext,
    *,
    provider: str,
    model: str,
) -> str:
    """Stable key from field, person, model, and planning context."""
    macro = (context.macro_context or "").strip()
    macro_digest = hashlib.sha256(macro.encode("utf-8")).hexdigest()[:16] if macro else ""
    parts = [
        context.field_key,
        context.person or "",
        provider.strip().lower(),
        model.strip(),
        str(context.current_age or ""),
        str(context.retirement_age or ""),
        (context.state or "").strip().upper(),
        str(context.planning_year or ""),
        (context.extra_prompt or "").strip(),
        macro_digest,
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{context.field_key}:{context.person or 'shared'}:{digest[:24]}"


def _entry_stale(cached_at: str) -> bool:
    try:
        timestamp = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timestamp > timedelta(days=_cache_ttl_days())


def _prune_entries(entries: dict[str, Any]) -> dict[str, Any]:
    fresh: dict[str, Any] = {}
    for key, row in entries.items():
        if not isinstance(row, dict):
            continue
        cached_at = str(row.get("cached_at", ""))
        if _entry_stale(cached_at):
            continue
        fresh[key] = row
    if len(fresh) <= MAX_ENTRIES:
        return fresh
    ranked = sorted(
        fresh.items(),
        key=lambda item: str(item[1].get("cached_at", "")),
        reverse=True,
    )
    return dict(ranked[:MAX_ENTRIES])


@dataclass
class CacheLookup:
    suggestion: AssumptionSuggestion | None = None
    cache_key: str = ""


class LLMResearchCache:
    """In-memory view of the on-disk cache — load once per batch, save once at end."""

    def __init__(self) -> None:
        self._dirty = False
        with _file_lock:
            data = _load_cache_file()
            entries = data.get("entries", {})
            self._entries = _prune_entries(entries) if isinstance(entries, dict) else {}

    def lookup(
        self,
        context: AssumptionResearchContext,
        *,
        provider: str,
        model: str,
    ) -> CacheLookup:
        key = context_cache_key(context, provider=provider, model=model)
        if not cache_enabled():
            return CacheLookup(cache_key=key)
        row = self._entries.get(key)
        if not isinstance(row, dict):
            return CacheLookup(cache_key=key)
        cached_at = str(row.get("cached_at", ""))
        if _entry_stale(cached_at):
            self._entries.pop(key, None)
            self._dirty = True
            return CacheLookup(cache_key=key)
        payload = row.get("suggestion")
        if not isinstance(payload, dict):
            return CacheLookup(cache_key=key)
        try:
            suggestion = suggestion_from_dict(payload)
        except (KeyError, TypeError, ValueError):
            self._entries.pop(key, None)
            self._dirty = True
            return CacheLookup(cache_key=key)
        if suggestion.field_key != context.field_key:
            return CacheLookup(cache_key=key)
        return CacheLookup(suggestion=suggestion, cache_key=key)

    def store(
        self,
        cache_key: str,
        context: AssumptionResearchContext,
        suggestion: AssumptionSuggestion,
        *,
        provider: str,
        model: str,
    ) -> None:
        if not cache_enabled() or not cache_key:
            return
        self._entries[cache_key] = {
            "field_key": context.field_key,
            "person": context.person,
            "provider": provider,
            "model": model,
            "cached_at": _utc_now_iso(),
            "suggestion": _suggestion_to_dict(suggestion),
        }
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        with _file_lock:
            data = _load_cache_file()
            entries = data.get("entries", {})
            if not isinstance(entries, dict):
                entries = {}
            entries.update(self._entries)
            data["entries"] = _prune_entries(entries)
            data["version"] = CACHE_VERSION
            _save_cache_file(data)
            self._dirty = False
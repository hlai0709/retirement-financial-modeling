"""Fetch and cache available LLM models from provider APIs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from security.llm_keychain import get_api_key

FALLBACK_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
    "xai": "grok-3",
}

logger = logging.getLogger(__name__)

CACHE_TTL_HOURS = 24

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
XAI_MODELS_URL = "https://api.x.ai/v1/models"

OPENAI_SKIP_PREFIXES = (
    "text-embedding",
    "tts-",
    "whisper-",
    "dall-e",
    "davinci",
    "babbage",
    "gpt-3.5-turbo-instruct",
    "omni-moderation",
    "sora-",
)


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    display_name: str
    created_at: str = ""


@dataclass
class ModelCatalogSnapshot:
    provider: str
    models: list[ModelInfo]
    recommended: str
    as_of: str
    source: str  # "live" | "cache" | "fallback"
    errors: list[str]

    def model_ids(self) -> list[str]:
        return [model.model_id for model in self.models]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cache_path():
    from settings import ensure_app_data_dir, llm_models_cache_path

    ensure_app_data_dir()
    return llm_models_cache_path()


def _load_cache_file() -> dict:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache_file(data: dict) -> None:
    import os

    path = _cache_path()
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _is_stale(as_of: str) -> bool:
    try:
        timestamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timestamp > timedelta(hours=CACHE_TTL_HOURS)


def _fallback_snapshot(provider: str, *, errors: list[str] | None = None) -> ModelCatalogSnapshot:
    model_id = FALLBACK_MODELS.get(provider, "")
    models = [ModelInfo(model_id=model_id, display_name=model_id)] if model_id else []
    return ModelCatalogSnapshot(
        provider=provider,
        models=models,
        recommended=model_id,
        as_of=_utc_now_iso(),
        source="fallback",
        errors=errors or [],
    )


def _score_model(provider: str, model_id: str) -> tuple[int, str]:
    """Higher is better. Secondary sort key is model_id for stability."""
    lowered = model_id.lower()
    score = 0

    if provider == "anthropic":
        if "sonnet" in lowered:
            score += 100
        elif "haiku" in lowered:
            score += 70
        elif "opus" in lowered:
            score += 85
        if lowered.startswith("claude-"):
            score += 20
    elif provider == "openai":
        if lowered.startswith("gpt-4o"):
            score += 100
        elif lowered.startswith("gpt-4.1"):
            score += 95
        elif lowered.startswith("gpt-4"):
            score += 80
        elif lowered.startswith("gpt-5"):
            score += 110
        elif lowered.startswith("o"):
            score += 60
    elif provider == "xai":
        if "grok-3" in lowered:
            score += 100
        elif "grok-2" in lowered:
            score += 80
        elif "grok" in lowered:
            score += 70

    if "mini" in lowered or "nano" in lowered:
        score -= 15
    if "preview" in lowered or "beta" in lowered:
        score -= 5
    return score, model_id


def _pick_recommended(provider: str, models: list[ModelInfo]) -> str:
    if not models:
        return FALLBACK_MODELS.get(provider, "")
    ranked = sorted(
        models,
        key=lambda model: _score_model(provider, model.model_id),
        reverse=True,
    )
    return ranked[0].model_id


def _fetch_openai_models(api_key: str) -> list[ModelInfo]:
    response = requests.get(
        OPENAI_MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    models: list[ModelInfo] = []
    for item in response.json().get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        if any(model_id.startswith(prefix) for prefix in OPENAI_SKIP_PREFIXES):
            continue
        if not (
            model_id.startswith("gpt-")
            or model_id.startswith("o")
            or model_id.startswith("chatgpt-")
        ):
            continue
        models.append(
            ModelInfo(
                model_id=model_id,
                display_name=model_id,
                created_at=str(item.get("created", "")),
            )
        )
    models.sort(key=lambda model: model.model_id)
    return models


def _fetch_anthropic_models(api_key: str) -> list[ModelInfo]:
    models: list[ModelInfo] = []
    after_id = ""
    while True:
        params = {"limit": 100}
        if after_id:
            params["after_id"] = after_id
        response = requests.get(
            ANTHROPIC_MODELS_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("data", []):
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    model_id=model_id,
                    display_name=str(item.get("display_name", model_id)),
                    created_at=str(item.get("created_at", "")),
                )
            )
        if not payload.get("has_more"):
            break
        after_id = payload.get("last_id", "")
        if not after_id:
            break
    return models


def _fetch_xai_models(api_key: str) -> list[ModelInfo]:
    response = requests.get(
        XAI_MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    models: list[ModelInfo] = []
    for item in response.json().get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id or "grok" not in model_id.lower():
            continue
        models.append(
            ModelInfo(
                model_id=model_id,
                display_name=model_id,
                created_at=str(item.get("created", "")),
            )
        )
    models.sort(key=lambda model: model.model_id)
    return models


def _fetch_live_models(provider: str, api_key: str) -> list[ModelInfo]:
    if provider == "openai":
        return _fetch_openai_models(api_key)
    if provider == "anthropic":
        return _fetch_anthropic_models(api_key)
    if provider == "xai":
        return _fetch_xai_models(api_key)
    raise ValueError(f"Unsupported provider: {provider!r}")


def _snapshot_to_cache(snapshot: ModelCatalogSnapshot) -> dict:
    return {
        "as_of": snapshot.as_of,
        "recommended": snapshot.recommended,
        "models": [
            {
                "id": model.model_id,
                "display_name": model.display_name,
                "created_at": model.created_at,
            }
            for model in snapshot.models
        ],
    }


def _snapshot_from_cache(provider: str, payload: dict) -> ModelCatalogSnapshot:
    models = [
        ModelInfo(
            model_id=item["id"],
            display_name=item.get("display_name", item["id"]),
            created_at=item.get("created_at", ""),
        )
        for item in payload.get("models", [])
        if item.get("id")
    ]
    return ModelCatalogSnapshot(
        provider=provider,
        models=models,
        recommended=payload.get("recommended") or _pick_recommended(provider, models),
        as_of=payload.get("as_of", ""),
        source="cache",
        errors=[],
    )


def refresh_model_catalog(provider: str, *, api_key: str | None = None) -> ModelCatalogSnapshot:
    """Fetch the latest model list from the provider API and update the local cache."""
    normalized = provider.strip().lower()
    if normalized == "none":
        return _fallback_snapshot("none")

    resolved_key = api_key or get_api_key(normalized)
    if not resolved_key:
        return _fallback_snapshot(
            normalized,
            errors=["No API key in Keychain — using built-in fallback model."],
        )

    errors: list[str] = []
    try:
        models = _fetch_live_models(normalized, resolved_key)
        recommended = _pick_recommended(normalized, models)
        snapshot = ModelCatalogSnapshot(
            provider=normalized,
            models=models,
            recommended=recommended,
            as_of=_utc_now_iso(),
            source="live",
            errors=errors,
        )
        cache = _load_cache_file()
        cache[normalized] = _snapshot_to_cache(snapshot)
        _save_cache_file(cache)
        return snapshot
    except Exception as exc:
        logger.exception("Failed to refresh model catalog for %s", normalized)
        cached = load_model_catalog(normalized, force_refresh=False)
        cached.errors.append(f"Live refresh failed: {exc}")
        if cached.models:
            cached.source = "cache"
            return cached
        return _fallback_snapshot(normalized, errors=[str(exc)])


def load_model_catalog(
    provider: str,
    *,
    api_key: str | None = None,
    force_refresh: bool = False,
) -> ModelCatalogSnapshot:
    """Load models from cache, refreshing from the API when stale or forced."""
    normalized = provider.strip().lower()
    if normalized == "none":
        return _fallback_snapshot("none")

    cache = _load_cache_file()
    cached_payload = cache.get(normalized)
    if cached_payload and not force_refresh and not _is_stale(cached_payload.get("as_of", "")):
        return _snapshot_from_cache(normalized, cached_payload)

    if cached_payload and not force_refresh:
        snapshot = _snapshot_from_cache(normalized, cached_payload)
        snapshot.errors.append("Model cache is stale — click Refresh Models to update.")
        return snapshot

    return refresh_model_catalog(normalized, api_key=api_key)


def resolve_model_choice(provider: str, model: str, *, api_key: str | None = None) -> str:
    """Return a valid model id, using the live catalog when possible."""
    normalized = provider.strip().lower()
    chosen = (model or "").strip()
    snapshot = load_model_catalog(normalized, api_key=api_key)
    known_ids = set(snapshot.model_ids())
    if chosen and chosen in known_ids:
        return chosen
    if chosen:
        return chosen
    return snapshot.recommended or FALLBACK_MODELS.get(normalized, "")


def default_model(provider: str, *, api_key: str | None = None) -> str:
    """Best-effort recommended model for a provider."""
    normalized = provider.strip().lower()
    if normalized == "none":
        return ""
    snapshot = load_model_catalog(normalized, api_key=api_key)
    return snapshot.recommended or FALLBACK_MODELS.get(normalized, "")
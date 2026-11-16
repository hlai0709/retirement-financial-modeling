from __future__ import annotations

from providers.llm.anthropic_provider import AnthropicProvider
from providers.llm.base import LLMProvider
from providers.llm.openai_provider import OpenAIProvider
from providers.llm.xai_provider import XAIProvider

# Fallback only — live catalog is preferred (see models_catalog.py).
FALLBACK_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
    "xai": "grok-3",
}


def get_llm_provider(provider: str, api_key: str) -> LLMProvider:
    normalized = provider.strip().lower()
    if normalized == "openai":
        return OpenAIProvider(api_key)
    if normalized == "anthropic":
        return AnthropicProvider(api_key)
    if normalized == "xai":
        return XAIProvider(api_key)
    raise ValueError(f"Unsupported LLM provider: {provider!r}")


def default_model(provider: str) -> str:
    from providers.llm.models_catalog import default_model as catalog_default

    return catalog_default(provider)
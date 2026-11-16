from __future__ import annotations

import requests

from providers.llm.base import LLMCompletion

DEFAULT_MODEL = "claude-sonnet-4-6"

# Retired / invalid IDs users may still have saved in LLM Settings.
DEPRECATED_MODEL_ALIASES = {
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-sonnet-latest": "claude-sonnet-4-6",
}


def _resolve_model(model: str) -> str:
    chosen = (model or DEFAULT_MODEL).strip()
    return DEPRECATED_MODEL_ALIASES.get(chosen, chosen)


def _api_error(response: requests.Response) -> RuntimeError:
    try:
        payload = response.json()
        message = payload.get("error", {}).get("message", response.text)
    except ValueError:
        message = response.text
    if response.status_code == 404 or "model" in str(message).lower() and "not" in str(message).lower():
        return RuntimeError(
            f"Anthropic API error ({response.status_code}): {message}. "
            "In LLM Settings, try model id 'claude-sonnet-4-6' (recommended) "
            "or copy an exact model id from console.anthropic.com / platform.claude.com."
        )
    return RuntimeError(f"Anthropic API error ({response.status_code}): {message}")
API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    provider_name = "anthropic"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        resolved = _resolve_model(model or DEFAULT_MODEL)
        response = requests.post(
            API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": resolved,
                "max_tokens": int(max_tokens or 1024),
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=120,
        )
        if not response.ok:
            raise _api_error(response)
        data = response.json()
        blocks = data.get("content", [])
        content = "".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        )
        return LLMCompletion(
            content=content,
            provider=self.provider_name,
            model=data.get("model", model or DEFAULT_MODEL),
        )
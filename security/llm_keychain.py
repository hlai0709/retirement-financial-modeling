"""macOS Keychain storage for LLM provider API keys (separate from Plaid tokens)."""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE_NAME = "RetirementPlanner-LLM"
VALID_PROVIDERS = frozenset({"openai", "anthropic", "xai"})


def _validate_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in VALID_PROVIDERS:
        raise ValueError(f"provider must be one of {sorted(VALID_PROVIDERS)}")
    return normalized


def store_api_key(provider: str, api_key: str) -> None:
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required")
    keyring.set_password(SERVICE_NAME, _validate_provider(provider), api_key.strip())


def get_api_key(provider: str) -> str | None:
    return keyring.get_password(SERVICE_NAME, _validate_provider(provider))


def delete_api_key(provider: str) -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, _validate_provider(provider))
        return True
    except keyring.errors.PasswordDeleteError:
        return False


def has_api_key(provider: str) -> bool:
    return bool(get_api_key(provider))
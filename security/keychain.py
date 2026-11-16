"""
macOS Keychain wrapper for Plaid access tokens.

Tokens are stored via the ``keyring`` library (Keychain Services on macOS).
Never write access tokens to .env, JSON, or plaintext files.
"""

from __future__ import annotations

import json
import logging

import keyring
import keyring.errors

logger = logging.getLogger(__name__)

SERVICE_NAME = "RetirementPlanner-Plaid"
REGISTRY_USERNAME = "__plaid_item_registry__"


def _load_registry() -> list[str]:
    raw = keyring.get_password(SERVICE_NAME, REGISTRY_USERNAME)
    if not raw:
        return []
    try:
        item_ids = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Plaid item registry in Keychain is corrupt; resetting.")
        return []
    if not isinstance(item_ids, list):
        return []
    return [str(item_id) for item_id in item_ids if item_id]


def _save_registry(item_ids: list[str]) -> None:
    unique_ids = sorted(set(item_ids))
    keyring.set_password(SERVICE_NAME, REGISTRY_USERNAME, json.dumps(unique_ids))


def store_token(item_id: str, access_token: str) -> None:
    """Store a Plaid access token for the given Item ID."""
    if not item_id or not item_id.strip():
        raise ValueError("item_id is required")
    if not access_token or not access_token.strip():
        raise ValueError("access_token is required")

    item_id = item_id.strip()
    keyring.set_password(SERVICE_NAME, item_id, access_token.strip())

    registry = _load_registry()
    if item_id not in registry:
        registry.append(item_id)
        _save_registry(registry)


def get_token(item_id: str) -> str | None:
    """Return the Plaid access token for an Item, or None if not stored."""
    if not item_id or not item_id.strip():
        raise ValueError("item_id is required")
    return keyring.get_password(SERVICE_NAME, item_id.strip())


def delete_token(item_id: str) -> bool:
    """Remove a Plaid access token. Returns True if an entry was deleted."""
    if not item_id or not item_id.strip():
        raise ValueError("item_id is required")

    item_id = item_id.strip()
    try:
        keyring.delete_password(SERVICE_NAME, item_id)
        deleted = True
    except keyring.errors.PasswordDeleteError:
        deleted = False

    registry = _load_registry()
    if item_id in registry:
        registry = [stored_id for stored_id in registry if stored_id != item_id]
        _save_registry(registry)

    return deleted


def list_item_ids() -> list[str]:
    """Return all Plaid Item IDs that have tokens stored in Keychain."""
    return _load_registry()


if __name__ == "__main__":
    import tempfile

    test_item_id = f"test-item-{next(tempfile._get_candidate_names())}"
    test_token = "access-sandbox-test-token"

    print("Keychain smoke test")
    print(f"  item_id: {test_item_id}")

    store_token(test_item_id, test_token)
    assert get_token(test_item_id) == test_token
    assert test_item_id in list_item_ids()

    delete_token(test_item_id)
    assert get_token(test_item_id) is None
    assert test_item_id not in list_item_ids()

    print("  store / get / list / delete: OK")
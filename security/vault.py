"""
Encrypted local cache for financial snapshots (balances, transactions, market data).

The Fernet encryption key is stored in macOS Keychain — never on disk in plaintext.
Cached payloads are written to ``vault.enc`` under Application Support.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from settings import DIR_MODE, VAULT_FILENAME, app_data_dir, ensure_app_data_dir

logger = logging.getLogger(__name__)

VAULT_KEY_SERVICE = "RetirementPlanner-Vault"
VAULT_KEY_USERNAME = "encryption_key"

FILE_MODE = 0o600


def default_data_dir() -> Path:
    """Backward-compatible alias for :func:`settings.app_data_dir`."""
    return app_data_dir()


def _ensure_dir(path: Path) -> None:
    if path == app_data_dir():
        ensure_app_data_dir()
        return
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)


def _set_file_mode(path: Path) -> None:
    if path.exists():
        os.chmod(path, FILE_MODE)


def _get_or_create_encryption_key() -> bytes:
    existing = keyring.get_password(VAULT_KEY_SERVICE, VAULT_KEY_USERNAME)
    if existing:
        return existing.encode("utf-8")
    key = Fernet.generate_key()
    keyring.set_password(VAULT_KEY_SERVICE, VAULT_KEY_USERNAME, key.decode("utf-8"))
    return key


class Vault:
    """Encrypt and decrypt JSON snapshots at ``vault.enc``."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.vault_path = self.data_dir / VAULT_FILENAME

    def exists(self) -> bool:
        return self.vault_path.is_file()

    def save(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise TypeError("vault data must be a dict")

        _ensure_dir(self.data_dir)
        fernet = Fernet(_get_or_create_encryption_key())
        payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        token = fernet.encrypt(payload)

        self.vault_path.write_bytes(token)
        _set_file_mode(self.vault_path)

    def load(self, *, recover: bool = True) -> dict:
        """Load encrypted vault data.

        If the Keychain encryption key no longer matches ``vault.enc`` (or the
        file is corrupt), optionally quarantine the file and return ``{}`` so
        the app can start and re-fetch live data from Plaid. Set
        ``recover=False`` to raise ``ValueError`` instead (tests / diagnostics).
        """
        if not self.exists():
            return {}

        fernet = Fernet(_get_or_create_encryption_key())
        try:
            payload = fernet.decrypt(self.vault_path.read_bytes())
        except InvalidToken as exc:
            message = "vault.enc could not be decrypted — key mismatch or corrupt file"
            if not recover:
                raise ValueError(message) from exc
            self._quarantine_unreadable(message)
            return {}

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            message = "vault.enc payload is not valid JSON"
            if not recover:
                raise ValueError(message) from exc
            self._quarantine_unreadable(message)
            return {}

        if not isinstance(data, dict):
            message = "vault payload must be a JSON object"
            if not recover:
                raise ValueError(message)
            self._quarantine_unreadable(message)
            return {}
        return data

    def _quarantine_unreadable(self, reason: str) -> None:
        """Move a bad vault aside so a fresh key can write a new cache."""
        path = self.vault_path
        if not path.exists():
            return
        stamp = path.with_name(f"vault.enc.corrupt.{path.stat().st_mtime_ns}")
        try:
            path.replace(stamp)
            logger.warning("%s — moved to %s; will rebuild on next refresh", reason, stamp.name)
        except OSError:
            try:
                path.unlink()
                logger.warning("%s — deleted unreadable vault.enc", reason)
            except OSError as exc:
                logger.error("%s — could not remove vault.enc: %s", reason, exc)

    def clear(self) -> None:
        if self.vault_path.exists():
            self.vault_path.unlink()


if __name__ == "__main__":
    import tempfile

    sample = {
        "version": 1,
        "balances": [{"account_id": "acct_test", "current": 1234.56}],
        "as_of": "2026-06-19T12:00:00",
    }

    with tempfile.TemporaryDirectory() as tmp:
        vault = Vault(data_dir=Path(tmp))
        print("Vault smoke test")
        print(f"  data_dir: {tmp}")

        vault.save(sample)
        assert vault.exists()
        loaded = vault.load()
        assert loaded == sample

        mode = oct(vault.vault_path.stat().st_mode & 0o777)
        assert mode == oct(FILE_MODE)
        print(f"  vault.enc permissions: {mode}")
        print("  save / load / permissions: OK")

        vault.clear()
        assert not vault.exists()
        assert vault.load() == {}
        print("  clear: OK")
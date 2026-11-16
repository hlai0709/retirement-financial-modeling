"""
Application paths, environment loading, and runtime configuration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from app.bundle_paths import env_example_path, is_frozen, project_root

APP_NAME = "RetirementPlanner"
PROJECT_ROOT = project_root()

VAULT_FILENAME = "vault.enc"
ACCOUNTS_FILENAME = "accounts.json"
REFRESH_LOG_FILENAME = "refresh_log.json"
LINK_SERVER_FILENAME = "link_server.json"
TRANSACTION_CURSORS_FILENAME = "transaction_cursors.json"
LLM_SETTINGS_FILENAME = "llm_settings.json"
LLM_MODELS_CACHE_FILENAME = "llm_models_cache.json"
LLM_RESEARCH_CACHE_FILENAME = "llm_research_cache.json"

VALID_LLM_PROVIDERS = frozenset({"none", "openai", "anthropic", "xai"})

DIR_MODE = 0o700

VALID_PLAID_ENVS = frozenset({"sandbox", "development", "production"})
FRED_PLACEHOLDER_KEYS = frozenset(
    {"your_fred_key", "your_key_here", "changeme", "replace_me"}
)


def app_data_dir() -> Path:
    """macOS Application Support directory for runtime data."""
    return Path.home() / "Library" / "Application Support" / APP_NAME


def vault_path() -> Path:
    return app_data_dir() / VAULT_FILENAME


def accounts_path() -> Path:
    return app_data_dir() / ACCOUNTS_FILENAME


def refresh_log_path() -> Path:
    return app_data_dir() / REFRESH_LOG_FILENAME


def link_server_info_path() -> Path:
    return app_data_dir() / LINK_SERVER_FILENAME


def transaction_cursors_path() -> Path:
    return app_data_dir() / TRANSACTION_CURSORS_FILENAME


def llm_settings_path() -> Path:
    return app_data_dir() / LLM_SETTINGS_FILENAME


def llm_models_cache_path() -> Path:
    return app_data_dir() / LLM_MODELS_CACHE_FILENAME


def llm_research_cache_path() -> Path:
    return app_data_dir() / LLM_RESEARCH_CACHE_FILENAME


def project_config_dir() -> Path:
    """Checked-in templates (e.g. config/accounts.example.json)."""
    return PROJECT_ROOT / "config"


def ensure_app_data_dir() -> Path:
    """Create Application Support directory with owner-only permissions."""
    path = app_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)
    return path


def load_env() -> None:
    """Load ``.env`` from dev project root and Application Support (bundled .app)."""
    if not is_frozen():
        load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(app_data_dir() / ".env")
    if is_frozen() and not (app_data_dir() / ".env").is_file():
        example = env_example_path()
        if example.is_file():
            load_dotenv(example)


@dataclass(frozen=True)
class Settings:
    plaid_client_id: str
    plaid_secret: str
    plaid_env: str
    fred_api_key: str | None = None

    @property
    def plaid_configured(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)

    @property
    def fred_configured(self) -> bool:
        return bool(self.fred_api_key)


def _normalize_fred_api_key(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    key = value.strip()
    if key.lower() in FRED_PLACEHOLDER_KEYS or key.lower().startswith("your_"):
        return None
    return key


def _require_non_empty(value: str | None, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


@lru_cache
def get_settings(*, require_plaid: bool = False) -> Settings:
    """
    Load settings from environment variables (via ``.env`` when present).

    Set ``require_plaid=True`` when calling Plaid APIs to fail fast if keys
    are missing.
    """
    load_env()

    plaid_client_id = os.getenv("PLAID_CLIENT_ID", "").strip()
    plaid_secret = os.getenv("PLAID_SECRET", "").strip()
    plaid_env = os.getenv("PLAID_ENV", "sandbox").strip().lower()
    fred_api_key = _normalize_fred_api_key(os.getenv("FRED_API_KEY"))

    if plaid_env not in VALID_PLAID_ENVS:
        raise ValueError(
            f"PLAID_ENV must be one of {sorted(VALID_PLAID_ENVS)}; got {plaid_env!r}"
        )

    if require_plaid:
        plaid_client_id = _require_non_empty(plaid_client_id, "PLAID_CLIENT_ID")
        plaid_secret = _require_non_empty(plaid_secret, "PLAID_SECRET")

    return Settings(
        plaid_client_id=plaid_client_id,
        plaid_secret=plaid_secret,
        plaid_env=plaid_env,
        fred_api_key=fred_api_key,
    )


def clear_settings_cache() -> None:
    """Clear cached settings (useful in tests)."""
    get_settings.cache_clear()


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "none"
    model: str = ""

    @property
    def configured(self) -> bool:
        return self.provider != "none"


def load_llm_settings() -> LLMSettings:
    """Load non-secret LLM preferences (provider/model) from Application Support."""
    from providers.llm.models_catalog import default_model

    ensure_app_data_dir()
    path = llm_settings_path()
    provider = os.getenv("LLM_PROVIDER", "none").strip().lower() or "none"
    model = os.getenv("LLM_MODEL", "").strip()

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            provider = str(data.get("provider", provider)).strip().lower() or provider
            model = str(data.get("model", model)).strip() or model
        except (json.JSONDecodeError, OSError):
            pass

    if provider not in VALID_LLM_PROVIDERS:
        provider = "none"
    if provider != "none" and not model:
        model = default_model(provider)
    if provider != "none" and model:
        snapshot = None
        try:
            from providers.llm.models_catalog import load_model_catalog

            snapshot = load_model_catalog(provider)
        except Exception:
            snapshot = None
        if snapshot and snapshot.model_ids() and model not in snapshot.model_ids():
            if snapshot.recommended:
                model = snapshot.recommended
    return LLMSettings(provider=provider, model=model)


def save_llm_settings(settings: LLMSettings) -> None:
    from providers.llm.models_catalog import default_model

    ensure_app_data_dir()
    provider = settings.provider.strip().lower()
    if provider not in VALID_LLM_PROVIDERS:
        raise ValueError(f"provider must be one of {sorted(VALID_LLM_PROVIDERS)}")
    model = settings.model.strip() or (default_model(provider) if provider != "none" else "")
    path = llm_settings_path()
    path.write_text(
        json.dumps({"provider": provider, "model": model}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


if __name__ == "__main__":
    print("Settings smoke test")
    print(f"  project_root: {PROJECT_ROOT}")
    print(f"  app_data_dir: {app_data_dir()}")
    print(f"  vault_path:   {vault_path()}")
    print(f"  accounts_path: {accounts_path()}")

    settings = get_settings()
    print(f"  plaid_env: {settings.plaid_env}")
    print(f"  plaid_configured: {settings.plaid_configured}")
    print("  load: OK")
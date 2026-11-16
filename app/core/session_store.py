"""Persist saved plans, working drafts, and session UI state to Application Support."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.planning_snapshot import PlanningSnapshot
from settings import app_data_dir, ensure_app_data_dir

SAVED_PLAN_FILENAME = "planning_snapshot.json"
DRAFT_FILENAME = "session_draft.json"
RESULTS_FILENAME = "last_results.json"
SESSION_UI_FILENAME = "session_ui.json"

# Backward-compatible alias
SNAPSHOT_FILENAME = SAVED_PLAN_FILENAME


def saved_plan_path() -> Path:
    return app_data_dir() / SAVED_PLAN_FILENAME


def snapshot_path() -> Path:
    return saved_plan_path()


def draft_path() -> Path:
    return app_data_dir() / DRAFT_FILENAME


def results_path() -> Path:
    return app_data_dir() / RESULTS_FILENAME


def session_ui_path() -> Path:
    return app_data_dir() / SESSION_UI_FILENAME


def _atomic_write_json(path: Path, data: Any) -> None:
    ensure_app_data_dir()
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_planning_snapshot() -> PlanningSnapshot | None:
    """Load the last explicitly saved plan (restored on app launch)."""
    path = saved_plan_path()
    if not path.is_file():
        return None
    try:
        return PlanningSnapshot.load_json(path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_planning_snapshot(snapshot: PlanningSnapshot) -> None:
    """Write the last saved plan — only called from explicit Save plan."""
    _atomic_write_json(saved_plan_path(), snapshot.to_dict())


def load_session_draft() -> PlanningSnapshot | None:
    path = draft_path()
    if not path.is_file():
        return None
    try:
        return PlanningSnapshot.load_json(path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_session_draft(snapshot: PlanningSnapshot) -> None:
    """Auto-save the current working session without touching the saved plan."""
    _atomic_write_json(draft_path(), snapshot.to_dict())


def load_last_results() -> dict[str, Any] | None:
    path = results_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_last_results(results: dict[str, Any] | None) -> None:
    path = results_path()
    if not results:
        if path.exists():
            path.unlink()
        return
    _atomic_write_json(path, results)


def load_session_ui() -> dict[str, Any]:
    path = session_ui_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_session_ui(
    *,
    expert_section: str,
    expert_assumptions_scope: str,
    plan_saved_at: str | None = None,
) -> str:
    existing = load_session_ui()
    session_at = datetime.now(timezone.utc).isoformat()
    plan_at = plan_saved_at if plan_saved_at is not None else existing.get("plan_saved_at")
    _atomic_write_json(
        session_ui_path(),
        {
            "expert_section": expert_section,
            "expert_assumptions_scope": expert_assumptions_scope,
            "saved_at": session_at,
            "plan_saved_at": plan_at,
        },
    )
    return plan_at or session_at
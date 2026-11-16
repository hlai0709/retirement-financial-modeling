"""Persist UI preferences — toolkit-agnostic."""

from __future__ import annotations

import json
from pathlib import Path

from settings import app_data_dir

PREFS_FILENAME = "ui_preferences.json"
DEFAULT_PRESENTATION_MODE = "guided"


def preferences_path() -> Path:
    return app_data_dir() / PREFS_FILENAME


_VALID_MODES = frozenset({"guided", "expert", "ai", "lazy"})


def load_presentation_mode() -> str:
    path = preferences_path()
    if not path.exists():
        return DEFAULT_PRESENTATION_MODE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mode = data.get("presentation_mode", DEFAULT_PRESENTATION_MODE)
        # Legacy Lazy Mode → AI Mode
        if mode == "lazy":
            return "ai"
        return mode if mode in {"guided", "expert", "ai"} else DEFAULT_PRESENTATION_MODE
    except Exception:
        return DEFAULT_PRESENTATION_MODE


def save_presentation_mode(mode: str) -> None:
    if mode == "lazy":
        mode = "ai"
    if mode not in {"guided", "expert", "ai"}:
        mode = DEFAULT_PRESENTATION_MODE
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {"presentation_mode": mode}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["presentation_mode"] = mode
        except Exception:
            data = {"presentation_mode": mode}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
"""Re-export preferences from core (gui shells stay thin)."""

from app.core.preferences import (
    DEFAULT_PRESENTATION_MODE,
    load_presentation_mode,
    preferences_path,
    save_presentation_mode,
)

__all__ = [
    "DEFAULT_PRESENTATION_MODE",
    "load_presentation_mode",
    "preferences_path",
    "save_presentation_mode",
]
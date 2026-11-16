"""Typography helpers — hierarchy that puts questions and numbers first."""

from __future__ import annotations

import tkinter.font as tkfont
from typing import Literal

from gui.design_system.tokens import COLORS, FONTS

StyleName = Literal["hero", "title", "step_title", "question", "body", "caption", "mono", "label"]

_UI_FAMILY: str | None = None
_MONO_FAMILY: str | None = None


def _resolve_family(preferred: str) -> str:
    try:
        families = set(tkfont.families())
    except tk.TclError:
        families = set()
    for candidate in (preferred, "Helvetica Neue", "Helvetica", "Arial"):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


def ui_family() -> str:
    global _UI_FAMILY
    if _UI_FAMILY is None:
        _UI_FAMILY = _resolve_family(FONTS.family_ui)
    return _UI_FAMILY


def mono_family() -> str:
    global _MONO_FAMILY
    if _MONO_FAMILY is None:
        _MONO_FAMILY = _resolve_family(FONTS.family_mono)
    return _MONO_FAMILY


def font_for(style: StyleName) -> tuple[str, int, str]:
    if style == "hero":
        return (ui_family(), FONTS.size_hero, "bold")
    if style == "title":
        return (ui_family(), FONTS.size_title, "bold")
    if style == "step_title":
        return (ui_family(), FONTS.size_step_title, "bold")
    if style == "question":
        return (ui_family(), FONTS.size_question, "normal")
    if style == "caption":
        return (ui_family(), FONTS.size_caption, "normal")
    if style == "mono":
        return (mono_family(), FONTS.size_body, "normal")
    if style == "label":
        return (ui_family(), FONTS.size_caption, "bold")
    return (ui_family(), FONTS.size_body, "normal")


def color_for(style: StyleName) -> str:
    if style in ("hero", "title", "question", "body", "mono"):
        return COLORS.text_primary
    if style == "label":
        return COLORS.text_secondary
    return COLORS.text_secondary
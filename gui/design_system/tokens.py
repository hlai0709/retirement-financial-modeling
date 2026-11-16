"""Visual tokens — Liquid Glass dark palette (mockup-locked Jun 2026)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorTokens:
    # Canvas base + mesh wash
    bg_base: str = "#0a0a12"
    bg_wash: str = "#0d1118"
    bg: str = "#0d1118"

    # Glass surfaces — lighter panels that read as frosted on mesh
    glass: str = "#252a36"
    glass_strong: str = "#2e3442"
    glass_border: str = "#4a5568"
    glass_border_subtle: str = "#353b4a"
    glass_btn: str = "#2a3040"
    glass_btn_hover: str = "#353d4f"
    glass_disabled: str = "#1a1e28"
    specular: str = "#6b7280"

    pill_on: str = "#3a4254"
    pill_off: str = "#1c2030"
    pill_off_hover: str = "#282e3c"

    input_fill: str = "#3d4556"
    input_border: str = "#5a6478"

    # Legacy aliases
    surface: str = "#252a36"
    surface_raised: str = "#343b4a"
    surface_hover: str = "#3d4556"
    border: str = "#4a5568"
    border_subtle: str = "#353b4a"
    bg_elevated: str = "#1c2030"

    text_primary: str = "#f2f2f7"
    text_secondary: str = "#9da3ae"
    text_muted: str = "#6b7280"
    text_disabled: str = "#4b5563"
    text_inverse: str = "#1a1a22"

    accent: str = "#5ac8fa"
    accent_hover: str = "#7dd8ff"
    accent_muted: str = "#1e3a4a"

    primary_fill: str = "#ebebf0"
    primary_fill_hover: str = "#ffffff"

    success: str = "#4ade80"
    warning: str = "#fbbf24"
    error: str = "#f87171"
    info: str = "#93c5fd"

    chart_primary: str = "#5ac8fa"
    chart_band: str = "#3a4152"
    chart_grid: str = "#2a3040"


@dataclass(frozen=True)
class FontTokens:
    family_ui: str = "SF Pro Text"
    family_display: str = "SF Pro Display"
    family_mono: str = "SF Mono"

    size_caption: int = 11
    size_body: int = 15
    size_question: int = 18
    size_title: int = 15
    size_step_title: int = 36
    size_hero: int = 22


@dataclass(frozen=True)
class SpacingTokens:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32

    radius_sm: int = 10
    radius_md: int = 14
    radius_lg: int = 18
    radius_xl: int = 22

    chrome_margin: int = 12


COLORS = ColorTokens()
FONTS = FontTokens()
SPACING = SpacingTokens()
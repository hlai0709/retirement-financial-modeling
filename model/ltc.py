"""Long-term care cost scenarios (onset age, duration, care setting, LTC inflation)."""

from __future__ import annotations

from typing import Any

from config.policy.tables import LTC_CARE_SETTING_MULTIPLIERS

VALID_LTC_MODES = frozenset({"none", "annual", "scenario"})
VALID_LTC_CARE_SETTINGS = frozenset(LTC_CARE_SETTING_MULTIPLIERS.keys())


def _normalize_mode(value: object) -> str:
    mode = str(value or "annual").strip().lower()
    if mode not in VALID_LTC_MODES:
        return "annual"
    return mode


def _normalize_care_setting(value: object) -> str:
    setting = str(value or "home_health_aide").strip().lower()
    if setting not in VALID_LTC_CARE_SETTINGS:
        return "home_health_aide"
    return setting


def _ltc_active_window(
    person: dict[str, Any],
    *,
    age: int,
    retirement_age: int,
) -> tuple[bool, int, int]:
    """Return (active, start_age, duration_years) for scenario mode."""
    mode = _normalize_mode(person.get("ltc_mode"))
    if mode == "none":
        return False, 0, 0
    if mode == "annual":
        return age >= retirement_age, retirement_age, 999

    start = person.get("ltc_start_age")
    if start in (None, ""):
        start = person.get("retirement_age", 80)
    start_age = int(start or 80)
    duration = int(person.get("ltc_duration_years", 3) or 3)
    duration = max(1, duration)
    active = start_age <= age < start_age + duration
    return active, start_age, duration


def care_setting_multiplier(setting: object) -> float:
    return LTC_CARE_SETTING_MULTIPLIERS[_normalize_care_setting(setting)]


def person_ltc_annual(
    person: dict[str, Any],
    *,
    age: int,
    retirement_age: int,
    ltc_inflation_factor: float = 1.0,
) -> float:
    """
    Annual LTC cost for a person.

    ``annual`` mode: flat ``long_term_care_annual`` from retirement, escalated with LTC inflation.
    ``scenario`` mode: cost only between start age and start+duration, with care-setting multiplier.
    """
    mode = _normalize_mode(person.get("ltc_mode"))
    if mode == "none":
        return 0.0

    base = float(person.get("long_term_care_annual", 0) or 0)
    if base <= 0:
        return 0.0

    active, _, _ = _ltc_active_window(person, age=age, retirement_age=retirement_age)
    if not active:
        return 0.0

    amount = base * ltc_inflation_factor
    if mode == "scenario":
        amount *= care_setting_multiplier(person.get("ltc_care_setting"))

    return max(0.0, amount)
"""Derive planning fields from related inputs (UI-agnostic)."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable

from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.plan_completeness import touch_field_key
from config.field_values import format_field_value, parse_field_value
from config.model_fields import FIELDS_BY_KEY, ModelField
from config.policy.tables import MEDIGAP_PLAN_MONTHLY_2024
from model.ss import aime_from_career_earnings, full_retirement_age_years

_ASSET_BUCKET_KEYS = (
    "initial_taxable_assets",
    "initial_traditional_ira",
    "initial_roth_ira",
)


def planning_year() -> int:
    return date.today().year


def birth_year_from_age(current_age: int, *, year: int | None = None) -> int:
    return (year or planning_year()) - current_age


def current_age_from_birth_year(birth_year: int, *, year: int | None = None) -> int:
    return (year or planning_year()) - birth_year


def _person_from_row(row: dict[str, Any]) -> str:
    return row.get("person") or "self"


def _raw_from_updates(
    updates: list[dict[str, Any]] | None,
    key: str,
    *,
    person: str | None = None,
) -> str | None:
    if not updates:
        return None
    for row in updates:
        if row.get("key") != key:
            continue
        if person is not None:
            if _person_from_row(row) != person:
                continue
        return str(row.get("value", "")).strip()
    return None


def _snapshot_raw(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
) -> str:
    return snapshot.get_raw(field, person=person).strip()


def _resolved_raw(
    snapshot: PlanningSnapshot,
    key: str,
    *,
    person: str | None = None,
    updates: list[dict[str, Any]] | None = None,
) -> str:
    from_updates = _raw_from_updates(updates, key, person=person)
    if from_updates is not None:
        return from_updates
    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return ""
    scope_person = person if field.scope == "person" else None
    return _snapshot_raw(snapshot, field, person=scope_person)


def _parse_int_field(
    snapshot: PlanningSnapshot,
    key: str,
    *,
    person: str | None = None,
    updates: list[dict[str, Any]] | None = None,
    min_value: int = 1,
    max_value: int = 120,
) -> int | None:
    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return None
    raw = _resolved_raw(snapshot, key, person=person, updates=updates)
    if not raw:
        return None
    try:
        value = int(parse_field_value(field, raw))
    except (TypeError, ValueError):
        return None
    if value < min_value or value > max_value:
        return None
    return value


def _is_blank_optional_value(raw: str) -> bool:
    normalized = raw.strip().replace(",", "").replace("$", "").strip()
    return not normalized or normalized in {"0", "0.0", "0.00"}


def _has_meaningful_raw(raw: str) -> bool:
    return not _is_blank_optional_value(raw)


def _parse_money_field(
    snapshot: PlanningSnapshot,
    key: str,
    *,
    person: str | None = None,
    updates: list[dict[str, Any]] | None = None,
    treat_zero_as_blank: bool = False,
) -> float | None:
    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return None
    raw = _resolved_raw(snapshot, key, person=person, updates=updates)
    if not raw or (treat_zero_as_blank and _is_blank_optional_value(raw)):
        return None
    try:
        return float(parse_field_value(field, raw))
    except (TypeError, ValueError):
        return None


def _field_locked(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
    derived_text: str,
) -> bool:
    touch_key = touch_field_key(field, person=person)
    if touch_key not in snapshot.wizard.touched_fields:
        return False
    raw = _snapshot_raw(snapshot, field, person=person)
    # Blank / zero placeholders are not a deliberate user override
    if not raw or _is_blank_optional_value(raw):
        return False
    if field.field_type == "rate_pct":
        try:
            if abs(float(parse_field_value(field, raw) or 0)) < 1e-12:
                return False
        except (TypeError, ValueError):
            pass
    if raw == derived_text:
        return False
    return True


def _maybe_set_raw(
    snapshot: PlanningSnapshot,
    field: ModelField,
    text: str,
    *,
    person: str | None,
    locked_check: Callable[[], bool],
) -> bool:
    if locked_check():
        return False
    existing = _snapshot_raw(snapshot, field, person=person)
    if existing == text:
        return False
    snapshot.set_raw(field, text, person=person)
    return True


def derived_birth_year_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    age = _parse_int_field(snapshot, "current_age", person=person, updates=updates)
    if age is None:
        return None
    return birth_year_from_age(age)


def derived_current_age_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    field = FIELDS_BY_KEY["birth_year"]
    raw = _resolved_raw(snapshot, "birth_year", person=person, updates=updates)
    if not raw:
        return None
    try:
        birth_year = int(parse_field_value(field, raw))
    except (TypeError, ValueError):
        return None
    if birth_year < 1900 or birth_year > 2100:
        return None
    age = current_age_from_birth_year(birth_year)
    if age < 1 or age > 120:
        return None
    return age


def maybe_derive_birth_year(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Set birth_year from current_age when the user has not entered their own birth year."""
    birth_field = FIELDS_BY_KEY["birth_year"]
    derived = derived_birth_year_for_person(snapshot, person, updates=updates)
    if derived is None or derived < 1900 or derived > 2100:
        return False
    return _maybe_set_raw(
        snapshot,
        birth_field,
        str(derived),
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            birth_field,
            person=person,
            derived_text=str(derived),
        ),
    )


def maybe_derive_current_age(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Set current_age from birth_year when the user has not entered their own age."""
    age_field = FIELDS_BY_KEY["current_age"]
    derived = derived_current_age_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    return _maybe_set_raw(
        snapshot,
        age_field,
        str(derived),
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            age_field,
            person=person,
            derived_text=str(derived),
        ),
    )


def derived_initial_assets_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> float | None:
    total = 0.0
    any_bucket = False
    for key in _ASSET_BUCKET_KEYS:
        raw = _resolved_raw(snapshot, key, person=person, updates=updates)
        if not _has_meaningful_raw(raw):
            continue
        any_bucket = True
        amount = _parse_money_field(snapshot, key, person=person, updates=updates)
        if amount is None:
            continue
        total += max(0.0, amount)
    if not any_bucket:
        return None
    return total


def maybe_derive_initial_assets(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Set initial_assets to the sum of explicit account buckets when provided."""
    assets_field = FIELDS_BY_KEY["initial_assets"]
    derived = derived_initial_assets_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = format_field_value(assets_field, derived)
    return _maybe_set_raw(
        snapshot,
        assets_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            assets_field,
            person=person,
            derived_text=text,
        ),
    )


def derived_roth_portfolio_pct(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> float | None:
    """Roth share of retirement accounts: roth / (traditional + roth).

    The percentage splits a balance that has no account buckets into traditional
    versus Roth. Taxable assets are not in that split. A household that holds
    only taxable accounts is 0% Roth instead of leaving the field blank.
    """
    retirement = 0.0
    roth_total = 0.0
    has_taxable = False
    has_retirement_bucket = False
    for person in snapshot.persons:
        traditional = _parse_money_field(
            snapshot,
            "initial_traditional_ira",
            person=person,
            updates=updates,
            treat_zero_as_blank=True,
        )
        roth = _parse_money_field(
            snapshot,
            "initial_roth_ira",
            person=person,
            updates=updates,
            treat_zero_as_blank=True,
        )
        taxable = _parse_money_field(
            snapshot,
            "initial_taxable_assets",
            person=person,
            updates=updates,
            treat_zero_as_blank=True,
        )
        if taxable is not None and taxable > 0:
            has_taxable = True
        if traditional is None and roth is None:
            continue
        has_retirement_bucket = True
        retirement += max(0.0, traditional or 0.0) + max(0.0, roth or 0.0)
        roth_total += max(0.0, roth or 0.0)
    if not has_retirement_bucket:
        return 0.0 if has_taxable else None
    if retirement <= 0:
        return 0.0
    return max(0.0, min(1.0, roth_total / retirement))


def maybe_derive_roth_portfolio_pct(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Fill Roth Portfolio % from traditional and Roth balances when known."""
    pct_field = FIELDS_BY_KEY.get("roth_portfolio_pct")
    if pct_field is None:
        return False
    derived = derived_roth_portfolio_pct(snapshot, updates=updates)
    if derived is None:
        return False
    text = format_field_value(pct_field, derived)
    return _maybe_set_raw(
        snapshot,
        pct_field,
        text,
        person=None,
        locked_check=lambda: _field_locked(
            snapshot,
            pct_field,
            person=None,
            derived_text=text,
        ),
    )


def derived_medigap_monthly_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> float | None:
    raw_plan = _resolved_raw(snapshot, "medigap_plan", person=person, updates=updates).lower()
    if not raw_plan or raw_plan == "none":
        return None
    premium = MEDIGAP_PLAN_MONTHLY_2024.get(raw_plan)
    if premium is None:
        return None
    return premium


def maybe_derive_medigap_monthly(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Fill Medigap monthly premium from the selected plan letter."""
    monthly_field = FIELDS_BY_KEY["medigap_monthly"]
    derived = derived_medigap_monthly_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = format_field_value(monthly_field, derived)
    return _maybe_set_raw(
        snapshot,
        monthly_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            monthly_field,
            person=person,
            derived_text=text,
        ),
    )


def derived_aca_slcsp_monthly(snapshot: PlanningSnapshot, *, updates: list[dict[str, Any]] | None = None) -> float | None:
    slcsp_raw = _resolved_raw(snapshot, "aca_slcsp_monthly_age_21", updates=updates)
    if _has_meaningful_raw(slcsp_raw):
        return None
    benchmark = _parse_money_field(snapshot, "aca_benchmark_monthly_age_21", updates=updates)
    if benchmark is None or benchmark <= 0:
        return None
    return benchmark


def maybe_derive_aca_slcsp_monthly(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Default SLCSP monthly premium to the benchmark when blank."""
    slcsp_field = FIELDS_BY_KEY["aca_slcsp_monthly_age_21"]
    derived = derived_aca_slcsp_monthly(snapshot, updates=updates)
    if derived is None:
        return False
    text = format_field_value(slcsp_field, derived)
    return _maybe_set_raw(
        snapshot,
        slcsp_field,
        text,
        person=None,
        locked_check=lambda: _field_locked(
            snapshot,
            slcsp_field,
            person=None,
            derived_text=text,
        ),
    )


def derived_aca_selected_plan_monthly(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> float | None:
    selected_raw = _resolved_raw(snapshot, "aca_selected_plan_monthly_age_21", updates=updates)
    if _has_meaningful_raw(selected_raw):
        return None
    slcsp = _parse_money_field(
        snapshot,
        "aca_slcsp_monthly_age_21",
        updates=updates,
        treat_zero_as_blank=True,
    )
    if slcsp is not None and slcsp > 0:
        return slcsp
    benchmark = _parse_money_field(snapshot, "aca_benchmark_monthly_age_21", updates=updates)
    if benchmark is None or benchmark <= 0:
        return None
    return benchmark


def maybe_derive_aca_selected_plan_monthly(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Default selected ACA plan premium to SLCSP (or benchmark) when blank."""
    selected_field = FIELDS_BY_KEY["aca_selected_plan_monthly_age_21"]
    derived = derived_aca_selected_plan_monthly(snapshot, updates=updates)
    if derived is None:
        return False
    text = format_field_value(selected_field, derived)
    return _maybe_set_raw(
        snapshot,
        selected_field,
        text,
        person=None,
        locked_check=lambda: _field_locked(
            snapshot,
            selected_field,
            person=None,
            derived_text=text,
        ),
    )


def _person_age_field(
    snapshot: PlanningSnapshot,
    person: str,
    key: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    return _parse_int_field(snapshot, key, person=person, updates=updates)


def derived_ltc_start_age_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    mode = _resolved_raw(snapshot, "ltc_mode", person=person, updates=updates).lower()
    if mode != "scenario":
        return None
    if _has_meaningful_raw(_resolved_raw(snapshot, "ltc_start_age", person=person, updates=updates)):
        return None
    retirement = _person_age_field(snapshot, person, "retirement_age", updates=updates)
    return retirement


def maybe_derive_ltc_start_age(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Default LTC scenario start age to retirement age when blank."""
    start_field = FIELDS_BY_KEY["ltc_start_age"]
    derived = derived_ltc_start_age_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = str(derived)
    return _maybe_set_raw(
        snapshot,
        start_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            start_field,
            person=person,
            derived_text=text,
        ),
    )


def derived_ss_start_age_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    if _has_meaningful_raw(_resolved_raw(snapshot, "ss_start_age", person=person, updates=updates)):
        return None
    birth_year = derived_birth_year_for_person(snapshot, person, updates=updates)
    if birth_year is None:
        birth_year = _parse_int_field(
            snapshot,
            "birth_year",
            person=person,
            updates=updates,
            min_value=1900,
            max_value=2100,
        )
    if birth_year is None:
        return None
    fra = full_retirement_age_years(birth_year)
    return int(math.ceil(fra - 1e-9))


def maybe_derive_ss_start_age(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Default Social Security claiming age to full retirement age when blank."""
    ss_field = FIELDS_BY_KEY["ss_start_age"]
    derived = derived_ss_start_age_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = str(derived)
    return _maybe_set_raw(
        snapshot,
        ss_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            ss_field,
            person=person,
            derived_text=text,
        ),
    )


def derived_pension_start_age_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    if _has_meaningful_raw(_resolved_raw(snapshot, "pension_start_age", person=person, updates=updates)):
        return None
    return _person_age_field(snapshot, person, "retirement_age", updates=updates)


def maybe_derive_pension_start_age(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Default pension start age to retirement age when blank."""
    pension_field = FIELDS_BY_KEY["pension_start_age"]
    derived = derived_pension_start_age_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = str(derived)
    return _maybe_set_raw(
        snapshot,
        pension_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            pension_field,
            person=person,
            derived_text=text,
        ),
    )


def derived_roth_conversion_start_age_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> int | None:
    mode = _resolved_raw(snapshot, "roth_conversion_mode", person=person, updates=updates).lower()
    if mode not in {"fixed", "bracket_fill"}:
        return None
    if _has_meaningful_raw(
        _resolved_raw(snapshot, "roth_conversion_start_age", person=person, updates=updates)
    ):
        return None
    return _person_age_field(snapshot, person, "retirement_age", updates=updates)


def maybe_derive_roth_conversion_start_age(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Default Roth conversion start age to retirement age when blank."""
    start_field = FIELDS_BY_KEY["roth_conversion_start_age"]
    derived = derived_roth_conversion_start_age_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = str(derived)
    return _maybe_set_raw(
        snapshot,
        start_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            start_field,
            person=person,
            derived_text=text,
        ),
    )


def derived_ss_aime_for_person(
    snapshot: PlanningSnapshot,
    person: str,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> float | None:
    mode = _resolved_raw(snapshot, "ss_benefit_mode", person=person, updates=updates).lower()
    if mode != "earnings":
        return None
    if _has_meaningful_raw(_resolved_raw(snapshot, "ss_aime", person=person, updates=updates)):
        return None
    earnings = _parse_money_field(snapshot, "ss_career_avg_earnings", person=person, updates=updates)
    if earnings is None or earnings <= 0:
        earnings = _parse_money_field(snapshot, "annual_earned_income", person=person, updates=updates)
    if earnings is None or earnings <= 0:
        return None
    return aime_from_career_earnings(earnings)


def maybe_derive_ss_aime(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    """Derive AIME from career-average earnings when using the earnings SS mode."""
    aime_field = FIELDS_BY_KEY["ss_aime"]
    derived = derived_ss_aime_for_person(snapshot, person, updates=updates)
    if derived is None:
        return False
    text = format_field_value(aime_field, derived)
    return _maybe_set_raw(
        snapshot,
        aime_field,
        text,
        person=person,
        locked_check=lambda: _field_locked(
            snapshot,
            aime_field,
            person=person,
            derived_text=text,
        ),
    )


_DERIVED_VALUE_CHECKS: dict[str, Callable[..., Any]] = {
    "birth_year": derived_birth_year_for_person,
    "current_age": derived_current_age_for_person,
    "initial_assets": derived_initial_assets_for_person,
    "medigap_monthly": derived_medigap_monthly_for_person,
    "aca_slcsp_monthly_age_21": lambda snapshot, _person, *, updates=None: derived_aca_slcsp_monthly(
        snapshot, updates=updates
    ),
    "aca_selected_plan_monthly_age_21": lambda snapshot, _person, *, updates=None: derived_aca_selected_plan_monthly(
        snapshot, updates=updates
    ),
    "ltc_start_age": derived_ltc_start_age_for_person,
    "ss_start_age": derived_ss_start_age_for_person,
    "pension_start_age": derived_pension_start_age_for_person,
    "roth_conversion_start_age": derived_roth_conversion_start_age_for_person,
    "ss_aime": derived_ss_aime_for_person,
    "roth_portfolio_pct": lambda snapshot, _person, *, updates=None: derived_roth_portfolio_pct(
        snapshot, updates=updates
    ),
}


def apply_derived_fields(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Apply all cross-field derivations. Returns keys that changed."""
    changed: list[str] = []

    for person in snapshot.persons:
        if maybe_derive_birth_year(snapshot, person=person, updates=updates):
            changed.append(f"{person}:birth_year")
        if maybe_derive_current_age(snapshot, person=person, updates=updates):
            changed.append(f"{person}:current_age")
        if maybe_derive_initial_assets(snapshot, person=person, updates=updates):
            changed.append(f"{person}:initial_assets")
        if maybe_derive_medigap_monthly(snapshot, person=person, updates=updates):
            changed.append(f"{person}:medigap_monthly")
        if maybe_derive_ltc_start_age(snapshot, person=person, updates=updates):
            changed.append(f"{person}:ltc_start_age")
        if maybe_derive_ss_start_age(snapshot, person=person, updates=updates):
            changed.append(f"{person}:ss_start_age")
        if maybe_derive_pension_start_age(snapshot, person=person, updates=updates):
            changed.append(f"{person}:pension_start_age")
        if maybe_derive_roth_conversion_start_age(snapshot, person=person, updates=updates):
            changed.append(f"{person}:roth_conversion_start_age")
        if maybe_derive_ss_aime(snapshot, person=person, updates=updates):
            changed.append(f"{person}:ss_aime")

    if maybe_derive_aca_slcsp_monthly(snapshot, updates=updates):
        changed.append("shared:aca_slcsp_monthly_age_21")
    if maybe_derive_aca_selected_plan_monthly(snapshot, updates=updates):
        changed.append("shared:aca_selected_plan_monthly_age_21")
    if maybe_derive_roth_portfolio_pct(snapshot, updates=updates):
        changed.append("shared:roth_portfolio_pct")

    return changed


def apply_derived_birth_years(
    snapshot: PlanningSnapshot,
    *,
    updates: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Backward-compatible wrapper for birth-year derivation only."""
    changed: list[str] = []
    for person in snapshot.persons:
        if maybe_derive_birth_year(snapshot, person=person, updates=updates):
            changed.append(person)
    return changed


def _derived_text_for_row(
    snapshot: PlanningSnapshot,
    key: str,
    *,
    person: str | None,
    updates: list[dict[str, Any]] | None,
) -> str | None:
    checker = _DERIVED_VALUE_CHECKS.get(key)
    if checker is None:
        return None
    scope_person = person or "self"
    derived = checker(snapshot, scope_person, updates=updates)
    if derived is None:
        return None
    field = FIELDS_BY_KEY.get(key)
    if field is None:
        return str(derived)
    if field.field_type == "money":
        return format_field_value(field, float(derived))
    if field.field_type in ("age", "year", "int"):
        return str(int(derived))
    return str(derived)


def _normalize_compare_text(field: ModelField | None, raw: str) -> str:
    text = raw.strip()
    if not field:
        return text
    if field.field_type == "money":
        return text.replace(",", "").replace("$", "").strip()
    if field.field_type == "rate_pct":
        return text.replace("%", "").strip()
    return text


def is_auto_derived_update(
    snapshot: PlanningSnapshot,
    row: dict[str, Any],
    *,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    key = row.get("key")
    if not key:
        return False
    person = row.get("person") if row.get("person") in ("self", "wife") else None
    derived_text = _derived_text_for_row(snapshot, str(key), person=person, updates=updates)
    if derived_text is None:
        return False
    field = FIELDS_BY_KEY.get(str(key))
    raw = _normalize_compare_text(field, str(row.get("value", "")))
    expected = _normalize_compare_text(field, derived_text)
    return raw == expected


def is_auto_derived_birth_year_update(
    snapshot: PlanningSnapshot,
    row: dict[str, Any],
    *,
    updates: list[dict[str, Any]] | None = None,
) -> bool:
    return is_auto_derived_update(snapshot, row, updates=updates)
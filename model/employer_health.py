"""Employer-sponsored health coverage and COBRA continuation (pre-Medicare)."""

from __future__ import annotations

VALID_EMPLOYER_HEALTH_MODES = frozenset({"none", "employer", "cobra"})
DEFAULT_COBRA_MONTHS = 18


def _normalize_mode(value: object) -> str:
    mode = str(value or "none").strip().lower()
    if mode not in VALID_EMPLOYER_HEALTH_MODES:
        return "none"
    return mode


def person_employer_health_annual(
    person: dict,
    *,
    age: int,
    retirement_age: int,
    medicare_start_age: int,
    inflation_factor: float = 1.0,
) -> float:
    """
    Employee share of health premiums while working or on COBRA.

    Employer mode: active while still working (age < retirement_age).
    COBRA mode: continues for cobra_months after retirement until Medicare.
    """
    mode = _normalize_mode(person.get("employer_health_mode"))
    if mode == "none":
        return 0.0

    monthly = float(person.get("employer_health_monthly", 0) or 0)
    if monthly <= 0:
        return 0.0

    annual = monthly * 12 * inflation_factor

    if mode == "employer":
        return annual if age < retirement_age else 0.0

    cobra_months = int(person.get("cobra_months", DEFAULT_COBRA_MONTHS) or DEFAULT_COBRA_MONTHS)
    cobra_months = max(0, min(cobra_months, 36))
    if age < retirement_age or age >= medicare_start_age:
        return 0.0
    months_retired = max(0, age - retirement_age) * 12
    return annual if months_retired < cobra_months else 0.0


def household_employer_health(
    persons: dict[str, dict],
    *,
    ages: dict[str, int],
    medicare_start_age: int,
    inflation_factor: float = 1.0,
) -> float:
    return sum(
        person_employer_health_annual(
            person,
            age=ages[name],
            retirement_age=int(person.get("retirement_age", 65) or 65),
            medicare_start_age=medicare_start_age,
            inflation_factor=inflation_factor,
        )
        for name, person in persons.items()
    )
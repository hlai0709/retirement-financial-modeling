"""Qualified Charitable Distributions (QCD) from traditional IRAs."""

from __future__ import annotations

from config.policy.tables import QCD_ANNUAL_CAP_2024

QCD_MIN_AGE = 70


def qcd_cap(*, inflation_factor: float = 1.0) -> float:
    return QCD_ANNUAL_CAP_2024 * inflation_factor


def person_qcd_amount(
    person: dict,
    *,
    age: int,
    inflation_factor: float = 1.0,
) -> float:
    if age < QCD_MIN_AGE:
        return 0.0
    amount = float(person.get("qcd_annual", 0) or 0)
    return min(max(0.0, amount), qcd_cap(inflation_factor=inflation_factor))


def household_qcd_total(
    persons: dict[str, dict],
    *,
    ages: dict[str, int],
    inflation_factor: float = 1.0,
) -> float:
    return sum(
        person_qcd_amount(person, age=ages[name], inflation_factor=inflation_factor)
        for name, person in persons.items()
    )
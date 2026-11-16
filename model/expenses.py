"""Retirement expense refinements — age bands, mortgage, one-time costs, survivor helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_EXPENSE_PHASE_MODES = frozenset({"flat", "age_banded"})


@dataclass(frozen=True)
class ExpensePolicy:
    expense_phase_mode: str = "flat"
    go_go_end_age: int = 75
    slow_go_end_age: int = 85
    go_go_pct: float = 1.0
    slow_go_pct: float = 0.80
    no_go_pct: float = 0.65
    ltc_inflation_rate: float = 0.045
    survivor_filing_status: str = "single"


def expense_policy_from_params(params: dict[str, Any]) -> ExpensePolicy:
    mode = str(params.get("expense_phase_mode", "flat") or "flat").strip().lower()
    if mode not in VALID_EXPENSE_PHASE_MODES:
        mode = "flat"

    def _pct(key: str, default: float) -> float:
        # Snapshot parsing already stores 80% as 0.80 and 160% as 1.60.
        # Only a raw whole-number percent (above 2, so 80 and 100 still work)
        # needs to be divided. A blank field keeps the default.
        if key not in params or params.get(key) in (None, ""):
            return default
        val = float(params[key])
        if val > 2.0:
            val /= 100.0
        return val

    ltc_inf = params.get("ltc_inflation_rate", 4.5)
    if ltc_inf in (None, ""):
        ltc_inf = 4.5
    ltc_inf = float(ltc_inf)
    if ltc_inf > 1.5:
        ltc_inf /= 100.0

    survivor = str(params.get("survivor_filing_status", "single") or "single").strip().lower()

    return ExpensePolicy(
        expense_phase_mode=mode,
        go_go_end_age=int(params.get("expense_go_go_end_age", 75) or 75),
        slow_go_end_age=int(params.get("expense_slow_go_end_age", 85) or 85),
        go_go_pct=_pct("expense_go_go_pct", 1.0),
        slow_go_pct=_pct("expense_slow_go_pct", 0.80),
        no_go_pct=_pct("expense_no_go_pct", 0.65),
        ltc_inflation_rate=ltc_inf,
        survivor_filing_status=survivor,
    )


def person_life_expectancy(person: dict[str, Any], *, shared: int) -> int:
    override = person.get("life_expectancy")
    if override not in (None, ""):
        try:
            value = int(override)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return int(shared)


def living_person_names(
    persons: dict[str, dict[str, Any]],
    ages: dict[str, int],
    *,
    shared_life_expectancy: int,
) -> list[str]:
    return [
        name
        for name in persons
        if ages[name] <= person_life_expectancy(persons[name], shared=shared_life_expectancy)
    ]


def age_expense_phase_label(
    age: int,
    *,
    retirement_age: int,
    policy: ExpensePolicy,
) -> str:
    if policy.expense_phase_mode != "age_banded" or age < retirement_age:
        return "flat"
    if age < policy.go_go_end_age:
        return "go_go"
    if age < policy.slow_go_end_age:
        return "slow_go"
    return "no_go"


def age_expense_multiplier(
    age: int,
    *,
    retirement_age: int,
    policy: ExpensePolicy,
) -> float:
    phase = age_expense_phase_label(age, retirement_age=retirement_age, policy=policy)
    if phase == "go_go":
        return policy.go_go_pct
    if phase == "slow_go":
        return policy.slow_go_pct
    if phase == "no_go":
        return policy.no_go_pct
    return 1.0


def _calendar_year(raw: object) -> int:
    if raw in (None, ""):
        return 0
    try:
        year = int(float(str(raw).replace(",", "").replace("$", "")))
    except (TypeError, ValueError):
        return 0
    return year if 1900 <= year <= 2100 else 0


def _payoff_month(raw: object) -> int:
    """1–12 when the last payment's month is known. 0 means the year is not split."""
    if raw in (None, ""):
        return 0
    try:
        month = int(float(str(raw).replace(",", "")))
    except (TypeError, ValueError):
        return 0
    return month if 1 <= month <= 12 else 0


def _one_loan_payment(annual: float, payoff_year: int, payoff_month: int, year: int) -> float:
    """Level P&I. The payoff year is charged through the payoff month, not always 12 months."""
    if annual <= 0:
        return 0.0
    if payoff_year <= 0:
        return annual
    if year > payoff_year:
        return 0.0
    if year < payoff_year or payoff_month <= 0:
        return annual
    return annual * payoff_month / 12.0


def person_mortgage_annual(
    person: dict[str, Any],
    *,
    year: int,
    inflation_factor: float = 1.0,
) -> float:
    """Fixed nominal P&I — do not inflate with CPI. Escrow is not part of this payment.

    A second mortgage ends on its own payoff year. ``inflation_factor`` is accepted
    for call-site symmetry and is unused: a fixed-rate payment stays level.
    """
    _ = inflation_factor
    first = _one_loan_payment(
        float(person.get("mortgage_annual", 0) or 0),
        _calendar_year(person.get("mortgage_payoff_year")),
        _payoff_month(person.get("mortgage_payoff_month")),
        year,
    )
    second = _one_loan_payment(
        float(person.get("mortgage_second_annual", 0) or 0),
        _calendar_year(person.get("mortgage_second_payoff_year")),
        _payoff_month(person.get("mortgage_second_payoff_month")),
        year,
    )
    return first + second


def person_property_tax_annual(
    person: dict[str, Any],
    *,
    inflation_factor: float = 1.0,
) -> float:
    """Cash property tax, in today's dollars grown with inflation.

    This is not part of the mortgage payment and is not an itemized deduction.
    """
    amount = float(person.get("property_tax_annual", 0) or 0)
    if amount <= 0:
        return 0.0
    factor = float(inflation_factor or 1.0)
    if factor <= 0:
        factor = 1.0
    return amount * factor


def person_one_time_expense(
    person: dict[str, Any],
    *,
    year: int,
    inflation_factor: float = 1.0,
) -> float:
    expense_year = person.get("one_time_expense_year")
    if expense_year in (None, ""):
        return 0.0
    if int(expense_year) != year:
        return 0.0
    amount = float(person.get("one_time_expense_amount", 0) or 0)
    return max(0.0, amount * inflation_factor)


def person_retirement_living_expense(
    flows_expenses: float,
    person: dict[str, Any],
    *,
    age: int,
    year: int,
    retirement_age: int,
    policy: ExpensePolicy,
    inflation_factor: float = 1.0,
) -> tuple[float, str]:
    """Base living expense with age-band, mortgage, property tax, and one-time add-ons."""
    multiplier = age_expense_multiplier(
        age, retirement_age=retirement_age, policy=policy
    )
    phase = age_expense_phase_label(age, retirement_age=retirement_age, policy=policy)
    living = flows_expenses * multiplier
    living += person_mortgage_annual(person, year=year, inflation_factor=inflation_factor)
    living += person_property_tax_annual(person, inflation_factor=inflation_factor)
    living += person_one_time_expense(person, year=year, inflation_factor=inflation_factor)
    return living, phase
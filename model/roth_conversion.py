"""Roth IRA conversion planning (fixed annual and bracket-fill strategies)."""

from __future__ import annotations

from typing import Any

from model.accounts import AccountBalances
from model.taxes import (
    TaxTable,
    additional_standard_deduction_65,
    compute_retirement_tax_detail,
    tax_table_for_filing_status,
)

VALID_ROTH_CONVERSION_MODES = frozenset({"none", "fixed", "bracket_fill"})
VALID_BRACKET_TARGETS = frozenset({0.12, 0.22, 0.24, 0.32})


def _normalize_mode(value: object) -> str:
    mode = str(value or "none").strip().lower()
    if mode not in VALID_ROTH_CONVERSION_MODES:
        return "none"
    return mode


def _conversion_active(
    person: dict,
    *,
    age: int,
) -> bool:
    start = person.get("roth_conversion_start_age")
    if start in (None, ""):
        start = person.get("retirement_age", 65)
    start_age = int(start or 65)
    end = person.get("roth_conversion_end_age")
    end_age = int(end) if end not in (None, "") else 999
    return start_age <= age <= end_age


def person_fixed_roth_conversion(
    person: dict,
    *,
    age: int,
    inflation_factor: float = 1.0,
) -> float:
    if _normalize_mode(person.get("roth_conversion_mode")) != "fixed":
        return 0.0
    if not _conversion_active(person, age=age):
        return 0.0
    annual = float(person.get("roth_conversion_annual", 0) or 0)
    return max(0.0, annual * inflation_factor)


def person_wants_bracket_fill(person: dict, *, age: int) -> bool:
    return (
        _normalize_mode(person.get("roth_conversion_mode")) == "bracket_fill"
        and _conversion_active(person, age=age)
    )


def bracket_taxable_ceiling(
    target_rate: float,
    table: TaxTable,
) -> float:
    """Ordinary taxable income ceiling (after standard deduction) for a marginal rate."""
    rate = float(target_rate)
    if rate not in VALID_BRACKET_TARGETS:
        rate = 0.22
    for ceiling, bracket_rate in table.brackets:
        if abs(bracket_rate - rate) < 0.001:
            return ceiling
    return table.brackets[-2][0]


def _ordinary_taxable(
    ordinary_income: float,
    *,
    table: TaxTable,
    filing_status: str,
    seniors_65_count: int,
    inflation_factor: float,
) -> float:
    extra_sd = additional_standard_deduction_65(
        filing_status,
        seniors_65_count=seniors_65_count,
        inflation_factor=inflation_factor,
    )
    return max(0.0, ordinary_income - table.standard_deduction - extra_sd)


def solve_bracket_fill_conversion(
    *,
    target_bracket_rate: float,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    withdrawal_gross: float,
    roth_fraction: float,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
    tax_mode: str,
    flat_rate: float,
    table: TaxTable,
    state_code: str,
    state_tax_enabled: bool,
    state_tax_override_pct: float | None,
    taxable_portfolio_fraction: float,
    taxable_gain_fraction: float,
    seniors_65_count: int,
    mfs_lived_apart: bool,
    niit_enabled: bool,
    account_balances: AccountBalances,
    withdrawal_order: str,
    rmd_floor: float,
    qcd_amount: float,
    max_traditional: float,
) -> float:
    """Max Roth conversion that fills up to the target federal bracket (ordinary income)."""
    if max_traditional <= 0 or tax_mode != "federal_brackets":
        return 0.0

    ceiling = bracket_taxable_ceiling(target_bracket_rate, table)
    lo = 0.0
    hi = max_traditional

    def ordinary_at(conversion: float) -> float:
        from model.accounts import allocate_withdrawal

        allocation = allocate_withdrawal(
            account_balances,
            withdrawal_gross,
            order=withdrawal_order,
            rmd_floor=rmd_floor,
            qcd=qcd_amount,
        )
        ss_taxable = compute_retirement_tax_detail(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental_gross,
            earned_income=earned_income,
            withdrawal_gross=withdrawal_gross,
            roth_fraction=roth_fraction,
            filing_status=filing_status,
            inflation_factor=inflation_factor,
            ss_tax_method=ss_tax_method,
            ss_taxable_fraction=ss_taxable_fraction,
            tax_mode=tax_mode,
            flat_rate=flat_rate,
            table=table,
            state_code=state_code,
            state_tax_enabled=False,
            taxable_portfolio_fraction=taxable_portfolio_fraction,
            taxable_gain_fraction=taxable_gain_fraction,
            seniors_65_count=seniors_65_count,
            mfs_lived_apart=mfs_lived_apart,
            niit_enabled=False,
            withdrawal_allocation=allocation,
            qcd_exclusion=qcd_amount,
            roth_conversion=conversion,
        ).ss_taxable
        gain_frac = max(0.0, min(taxable_gain_fraction, 1.0))
        trad_ordinary = max(0.0, allocation.from_traditional - qcd_amount)
        ltcg = allocation.from_taxable * gain_frac
        ordinary = (
            pension_gross
            + rental_gross
            + earned_income
            + trad_ordinary
            + allocation.from_taxable * (1.0 - gain_frac)
            + conversion
            + ss_taxable
        )
        return _ordinary_taxable(
            ordinary,
            table=table,
            filing_status=filing_status,
            seniors_65_count=seniors_65_count,
            inflation_factor=inflation_factor,
        )

    if ordinary_at(0.0) >= ceiling:
        return 0.0

    for _ in range(48):
        mid = (lo + hi) / 2
        if ordinary_at(mid) <= ceiling:
            lo = mid
        else:
            hi = mid

    return max(0.0, min(lo, max_traditional))


def apply_roth_conversion_to_ledger(
    ledger,
    *,
    household_mode: bool,
    person_amounts: dict[str, float],
) -> float:
    """Move traditional → Roth; return total converted."""
    from model.accounts import HouseholdLedger

    assert isinstance(ledger, HouseholdLedger)
    total = 0.0
    if household_mode:
        pool = ledger.by_person.setdefault("__household__", AccountBalances())
        amount = sum(person_amounts.values())
        take = min(max(0.0, amount), pool.traditional)
        pool.traditional -= take
        pool.roth += take
        return take

    for name, amount in person_amounts.items():
        balances = ledger.by_person.setdefault(name, AccountBalances())
        take = min(max(0.0, amount), balances.traditional)
        balances.traditional -= take
        balances.roth += take
        total += take
    return total


def resolve_person_conversions(
    persons: dict[str, dict[str, Any]],
    *,
    ages: dict[str, int],
    inflation_factor: float,
) -> dict[str, float]:
    """Fixed-mode per-person conversion amounts (bracket_fill resolved separately)."""
    return {
        name: person_fixed_roth_conversion(
            person,
            age=ages[name],
            inflation_factor=inflation_factor,
        )
        for name, person in persons.items()
    }


def target_bracket_rate_from_params(params: dict) -> float:
    raw = params.get("roth_conversion_target_bracket", 22) or 22
    if isinstance(raw, str):
        raw = raw.strip().replace("%", "")
    rate = float(raw) / 100.0 if float(raw) > 1 else float(raw)
    if rate not in VALID_BRACKET_TARGETS:
        return 0.22
    return rate
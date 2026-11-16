"""Social Security benefit estimation (AIME, PIA, claiming adjustments)."""

from __future__ import annotations

from config.policy.tables import (
    SS_PIA_BEND1_2024,
    SS_PIA_BEND2_2024,
    SS_PIA_RATE1,
    SS_PIA_RATE2,
    SS_PIA_RATE3,
    policy_for_projection,
)
from model.rmd import birth_year_for_person

VALID_SS_BENEFIT_MODES = frozenset({"manual", "pia", "aime", "earnings"})

SS_MAX_CLAIM_AGE = 70
SS_MIN_CLAIM_AGE = 62


def _normalize_mode(value: object) -> str:
    mode = str(value or "manual").strip().lower()
    if mode not in VALID_SS_BENEFIT_MODES:
        return "manual"
    return mode


def full_retirement_age_months(birth_year: int) -> int:
    """Return FRA in months from age 0 (SSA schedule by birth year)."""
    if birth_year >= 1960:
        return 67 * 12
    if birth_year >= 1955:
        extra_months = 2 + (birth_year - 1955) * 2
        return 66 * 12 + extra_months
    if birth_year >= 1943:
        return 66 * 12
    if birth_year >= 1929:
        extra_months = min(12, (birth_year - 1929) * 2)
        return 65 * 12 + extra_months
    return 65 * 12


def full_retirement_age_years(birth_year: int) -> float:
    months = full_retirement_age_months(birth_year)
    return months / 12.0


def pia_from_aime(aime: float, *, inflation_factor: float = 1.0) -> float:
    """Primary Insurance Amount from Average Indexed Monthly Earnings."""
    if aime <= 0:
        return 0.0
    policy = policy_for_projection(inflation_factor=inflation_factor)
    bend1 = SS_PIA_BEND1_2024 * policy.inflation_factor
    bend2 = SS_PIA_BEND2_2024 * policy.inflation_factor
    tier1 = min(aime, bend1) * SS_PIA_RATE1
    tier2 = min(max(0.0, aime - bend1), bend2 - bend1) * SS_PIA_RATE2
    tier3 = max(0.0, aime - bend2) * SS_PIA_RATE3
    return tier1 + tier2 + tier3


def monthly_benefit_from_pia(
    pia: float,
    *,
    claiming_age: int,
    birth_year: int,
) -> float:
    """Adjust monthly PIA for early or delayed claiming relative to FRA."""
    if pia <= 0:
        return 0.0

    claim_age = max(SS_MIN_CLAIM_AGE, min(claiming_age, SS_MAX_CLAIM_AGE))
    fra_months = full_retirement_age_months(birth_year)
    claim_months = claim_age * 12

    if claim_months < fra_months:
        months_early = fra_months - claim_months
        first_36 = min(months_early, 36)
        remainder = max(0, months_early - 36)
        reduction = first_36 * (5.0 / 9.0 / 100.0) + remainder * (5.0 / 12.0 / 100.0)
        return pia * (1.0 - reduction)

    if claim_months > fra_months:
        max_drc_months = SS_MAX_CLAIM_AGE * 12 - fra_months
        drc_months = min(claim_months - fra_months, max_drc_months)
        increase = drc_months * (2.0 / 3.0 / 100.0)
        return pia * (1.0 + increase)

    return pia


# OASDI contribution and benefit base. Years not listed use the 2024 base
# grown by the projection's inflation factor.
_SS_WAGE_BASE_BY_YEAR = {
    2024: 168_600.0,
    2025: 176_100.0,
    2026: 184_500.0,
    2027: 192_300.0,
}


def ss_contribution_base(year: int, *, inflation_factor: float = 1.0) -> float:
    """Annual earnings cap used before an AIME proxy is divided by 12."""
    known = _SS_WAGE_BASE_BY_YEAR.get(int(year))
    if known is not None:
        return known
    return 168_600.0 * max(0.01, inflation_factor)


def aime_from_career_earnings(
    avg_annual_earnings: float,
    *,
    wage_base: float | None = None,
) -> float:
    """Simplified AIME proxy from career-average annual earnings.

    Earnings above the Social Security wage base do not raise AIME.
    """
    if avg_annual_earnings <= 0:
        return 0.0
    capped = avg_annual_earnings
    if wage_base is not None and wage_base > 0:
        capped = min(avg_annual_earnings, wage_base)
    return capped / 12.0


def person_pia_monthly(
    person: dict,
    *,
    current_year: int,
    inflation_factor: float = 1.0,
) -> float | None:
    """Return monthly PIA at FRA when using a calculated mode, else None."""
    mode = _normalize_mode(person.get("ss_benefit_mode"))
    if mode == "manual":
        return None

    if mode == "pia":
        pia = float(person.get("ss_pia_monthly", 0) or 0)
        return max(0.0, pia)

    if mode == "aime":
        aime = float(person.get("ss_aime", 0) or 0)
        return pia_from_aime(aime, inflation_factor=inflation_factor)

    avg_annual = person.get("ss_career_avg_earnings")
    if avg_annual in (None, ""):
        avg_annual = person.get("annual_earned_income", 0)
    wage_base = ss_contribution_base(current_year, inflation_factor=inflation_factor)
    aime = aime_from_career_earnings(float(avg_annual or 0), wage_base=wage_base)
    return pia_from_aime(aime, inflation_factor=inflation_factor)


def person_annual_ss_benefit(
    person: dict,
    *,
    current_year: int,
    inflation_factor: float = 1.0,
) -> float:
    """Annual Social Security benefit at the selected claiming age."""
    mode = _normalize_mode(person.get("ss_benefit_mode"))
    if mode == "manual":
        return float(person.get("ss_amount", 0) or 0)

    pia = person_pia_monthly(person, current_year=current_year, inflation_factor=inflation_factor)
    if pia is None or pia <= 0:
        return float(person.get("ss_amount", 0) or 0)

    birth_year = birth_year_for_person(person, current_year=current_year)
    claiming_age = int(person.get("ss_start_age", 67) or 67)
    monthly = monthly_benefit_from_pia(
        pia,
        claiming_age=claiming_age,
        birth_year=birth_year,
    )
    return monthly * 12


def ss_benefit_detail(
    person: dict,
    *,
    current_year: int,
    inflation_factor: float = 1.0,
) -> dict[str, float | str | int]:
    """Diagnostic summary for notes and result columns."""
    mode = _normalize_mode(person.get("ss_benefit_mode"))
    birth_year = birth_year_for_person(person, current_year=current_year)
    claiming_age = int(person.get("ss_start_age", 67) or 67)
    annual = person_annual_ss_benefit(
        person,
        current_year=current_year,
        inflation_factor=inflation_factor,
    )
    pia = person_pia_monthly(person, current_year=current_year, inflation_factor=inflation_factor)
    return {
        "mode": mode,
        "birth_year": birth_year,
        "fra_years": full_retirement_age_years(birth_year),
        "claiming_age": claiming_age,
        "pia_monthly": 0.0 if pia is None else pia,
        "annual_benefit": annual,
    }
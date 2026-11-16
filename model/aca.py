"""ACA marketplace premiums and income-based subsidies (pre-Medicare gap)."""

from __future__ import annotations

from dataclasses import dataclass

from config.policy.tables import (
    ACA_APPLICABLE_PCT_TIERS,
    FPL_BASE_2024,
    FPL_PER_ADDITIONAL_PERSON_2024,
    policy_for_projection,
)
from model.taxes import normalize_filing_status

VALID_ACA_MODES = frozenset({"manual", "aca_subsidized"})

# CMS age-rating factors (relative to age 21 = 1.0)
ACA_AGE_RATING: tuple[tuple[int, float], ...] = (
    (21, 1.000),
    (30, 1.135),
    (40, 1.278),
    (50, 1.786),
    (55, 1.962),
    (60, 2.230),
    (64, 2.999),
)


@dataclass(frozen=True)
class AcaPolicy:
    mode: str = "aca_subsidized"
    benchmark_monthly_age_21: float = 475.0
    selected_plan_monthly_age_21: float | None = None
    slcsp_monthly_age_21: float | None = None


@dataclass(frozen=True)
class AcaCost:
    gross: float
    subsidy: float
    net: float
    enrollee_count: int
    fpl_pct: float
    applicable_pct: float
    slcsp_benchmark: float


def aca_policy_from_params(params: dict) -> AcaPolicy:
    mode = str(params.get("aca_premium_mode", "aca_subsidized") or "aca_subsidized").strip().lower()
    if mode not in VALID_ACA_MODES:
        mode = "aca_subsidized"
    benchmark = float(params.get("aca_benchmark_monthly_age_21", 475) or 475)
    selected_raw = params.get("aca_selected_plan_monthly_age_21")
    slcsp_raw = params.get("aca_slcsp_monthly_age_21")
    selected = None if selected_raw in (None, "") else float(selected_raw)
    slcsp = None if slcsp_raw in (None, "") else float(slcsp_raw)
    return AcaPolicy(
        mode=mode,
        benchmark_monthly_age_21=benchmark,
        selected_plan_monthly_age_21=selected,
        slcsp_monthly_age_21=slcsp,
    )


def federal_poverty_level(household_size: int, *, inflation_factor: float = 1.0) -> float:
    policy = policy_for_projection(inflation_factor=inflation_factor)
    size = max(1, household_size)
    return policy.fpl_base + policy.fpl_per_additional_person * (size - 1)


def aca_age_rating_factor(age: int) -> float:
    age = max(14, min(age, 64))
    if age <= ACA_AGE_RATING[0][0]:
        return ACA_AGE_RATING[0][1]
    for (lo_age, lo_factor), (hi_age, hi_factor) in zip(ACA_AGE_RATING, ACA_AGE_RATING[1:]):
        if age <= hi_age:
            if hi_age == lo_age:
                return hi_factor
            weight = (age - lo_age) / (hi_age - lo_age)
            return lo_factor + weight * (hi_factor - lo_factor)
    return ACA_AGE_RATING[-1][1]


def applicable_percentage(fpl_percent: float) -> float:
    """Household share of income for benchmark plan (2024 IRA-enhanced table)."""
    if fpl_percent <= 0:
        return 0.0
    lower_pct = 0.0
    lower_share = 0.0
    for ceiling, share in ACA_APPLICABLE_PCT_TIERS:
        if fpl_percent <= ceiling:
            if ceiling == lower_pct or ceiling == float("inf"):
                return share
            weight = (fpl_percent - lower_pct) / (ceiling - lower_pct)
            return lower_share + weight * (share - lower_share)
        lower_pct = ceiling
        lower_share = share
    return 0.085


def gross_annual_premium(
    age: int,
    *,
    benchmark_monthly_age_21: float,
    inflation_factor: float,
) -> float:
    monthly = benchmark_monthly_age_21 * aca_age_rating_factor(age) * inflation_factor
    return monthly * 12


def aca_tax_household_size(
    *,
    filing_layout: str,
    persons_count: int,
    enrollee_count: int,
) -> int:
    """ACA household size for FPL (tax family for joint filers)."""
    if filing_layout == "joint_return":
        return max(1, persons_count)
    return max(1, enrollee_count)


def _monthly_age_21_bases(policy: AcaPolicy) -> tuple[float, float]:
    """Return (SLCSP monthly at 21, selected-plan monthly at 21)."""
    slcsp = policy.slcsp_monthly_age_21
    if slcsp is None:
        slcsp = policy.benchmark_monthly_age_21
    selected = policy.selected_plan_monthly_age_21
    if selected is None:
        selected = slcsp
    return slcsp, selected


def compute_aca_household_cost(
    enrollee_ages: list[int],
    *,
    magi: float,
    policy: AcaPolicy,
    filing_status: str,
    household_size: int,
    inflation_factor: float,
) -> AcaCost:
    """
    Net ACA premiums after premium tax credit.

    Subsidy is based on the true SLCSP benchmark (second-lowest silver plan).
    Net premium reflects the selected plan cost minus the credit.
    """
    if not enrollee_ages or policy.mode != "aca_subsidized":
        return AcaCost(0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0)

    slcsp_monthly, selected_monthly = _monthly_age_21_bases(policy)
    slcsp_by_person = [
        gross_annual_premium(
            age,
            benchmark_monthly_age_21=slcsp_monthly,
            inflation_factor=inflation_factor,
        )
        for age in enrollee_ages
    ]
    plan_by_person = [
        gross_annual_premium(
            age,
            benchmark_monthly_age_21=selected_monthly,
            inflation_factor=inflation_factor,
        )
        for age in enrollee_ages
    ]
    slcsp_total = sum(slcsp_by_person)
    plan_gross = sum(plan_by_person)
    if slcsp_total <= 0 and plan_gross <= 0:
        return AcaCost(0.0, 0.0, 0.0, len(enrollee_ages), 0.0, 0.0, 0.0)

    fpl = federal_poverty_level(household_size, inflation_factor=inflation_factor)
    fpl_pct = (magi / fpl) * 100 if fpl > 0 else 0.0
    app_pct = applicable_percentage(fpl_pct)
    expected_contribution = max(0.0, magi * app_pct)

    subsidy = max(0.0, slcsp_total - expected_contribution)
    subsidy = min(subsidy, plan_gross)
    net = max(0.0, plan_gross - subsidy)

    return AcaCost(
        gross=plan_gross,
        subsidy=subsidy,
        net=net,
        enrollee_count=len(enrollee_ages),
        fpl_pct=fpl_pct,
        applicable_pct=app_pct,
        slcsp_benchmark=slcsp_total,
    )
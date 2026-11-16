"""Medicare Part B/D premiums, IRMAA surcharges, and Medigap supplements."""

from __future__ import annotations

from dataclasses import dataclass

from config.policy.tables import MEDIGAP_PLAN_MONTHLY_2024
from model.taxes import normalize_filing_status

# 2024 CMS baseline (annualized in calculations with inflation_factor)
PART_B_STANDARD_MONTHLY_2024 = 174.70
PART_D_AVERAGE_MONTHLY_2024 = 55.50

VALID_MEDIGAP_PLANS = frozenset(MEDIGAP_PLAN_MONTHLY_2024.keys())

# (MAGI ceiling, Part B monthly surcharge, Part D monthly surcharge)
MFJ_IRMAA_TIERS_2024: tuple[tuple[float, float, float], ...] = (
    (206_000, 0.0, 0.0),
    (258_000, 69.90, 12.90),
    (322_000, 174.70, 33.30),
    (386_000, 279.50, 53.80),
    (750_000, 384.30, 74.20),
    (float("inf"), 419.30, 81.00),
)

SINGLE_IRMAA_TIERS_2024: tuple[tuple[float, float, float], ...] = (
    (103_000, 0.0, 0.0),
    (129_000, 69.90, 12.90),
    (161_000, 174.70, 33.30),
    (193_000, 279.50, 53.80),
    (500_000, 384.30, 74.20),
    (float("inf"), 419.30, 81.00),
)


@dataclass(frozen=True)
class MedicarePolicy:
    enabled: bool = True
    start_age: int = 65
    part_b_monthly: float = PART_B_STANDARD_MONTHLY_2024
    part_d_monthly: float = PART_D_AVERAGE_MONTHLY_2024
    irmaa_lookback_years: int = 2


@dataclass(frozen=True)
class MedicareCost:
    total: float
    base: float
    irmaa: float
    medigap: float
    eligible_count: int


def medicare_policy_from_params(params: dict) -> MedicarePolicy:
    enabled = str(params.get("medicare_enabled", "True")).lower() != "false"
    lookback = int(params.get("medicare_irmaa_lookback_years", 2) or 2)
    lookback = max(0, min(lookback, 3))
    part_b = float(params.get("medicare_part_b_monthly", PART_B_STANDARD_MONTHLY_2024) or PART_B_STANDARD_MONTHLY_2024)
    part_d = float(params.get("medicare_part_d_monthly", PART_D_AVERAGE_MONTHLY_2024) or PART_D_AVERAGE_MONTHLY_2024)
    return MedicarePolicy(
        enabled=enabled,
        start_age=int(params.get("medicare_start_age", 65) or 65),
        part_b_monthly=part_b,
        part_d_monthly=part_d,
        irmaa_lookback_years=lookback,
    )


def _irmaa_tiers(filing_status: str) -> tuple[tuple[float, float, float], ...]:
    status = normalize_filing_status(filing_status)
    if status == "mfj":
        return MFJ_IRMAA_TIERS_2024
    return SINGLE_IRMAA_TIERS_2024


def _inflate_tiers(
    tiers: tuple[tuple[float, float, float], ...],
    inflation_factor: float,
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        (
            ceiling * inflation_factor if ceiling != float("inf") else float("inf"),
            part_b,
            part_d,
        )
        for ceiling, part_b, part_d in tiers
    )


def irmaa_monthly_surcharges(
    magi: float,
    *,
    filing_status: str,
    inflation_factor: float = 1.0,
) -> tuple[float, float]:
    """Return (Part B monthly surcharge, Part D monthly surcharge) from MAGI."""
    if magi <= 0:
        return 0.0, 0.0
    tiers = _inflate_tiers(_irmaa_tiers(filing_status), inflation_factor)
    for ceiling, part_b_surcharge, part_d_surcharge in tiers:
        if magi <= ceiling:
            return part_b_surcharge, part_d_surcharge
    return tiers[-1][1], tiers[-1][2]


def medicare_eligible_count(ages: dict[str, int], *, start_age: int) -> int:
    return sum(1 for age in ages.values() if age >= start_age)


def person_medigap_monthly(
    person: dict,
    *,
    inflation_factor: float = 1.0,
) -> float:
    override = person.get("medigap_monthly")
    if override not in (None, ""):
        return max(0.0, float(override)) * inflation_factor
    plan = str(person.get("medigap_plan", "none") or "none").strip().lower()
    if plan not in VALID_MEDIGAP_PLANS:
        plan = "none"
    return MEDIGAP_PLAN_MONTHLY_2024[plan] * inflation_factor


def household_medigap_annual(
    persons: dict[str, dict],
    *,
    ages: dict[str, int],
    start_age: int,
    inflation_factor: float = 1.0,
) -> float:
    total = 0.0
    for name, person in persons.items():
        if ages[name] >= start_age:
            total += person_medigap_monthly(person, inflation_factor=inflation_factor) * 12
    return total


def compute_medicare_cost(
    *,
    ages: dict[str, int],
    policy: MedicarePolicy,
    magi: float,
    filing_status: str,
    inflation_factor: float,
    persons: dict[str, dict] | None = None,
) -> MedicareCost:
    count = medicare_eligible_count(ages, start_age=policy.start_age)
    if not policy.enabled or count <= 0:
        return MedicareCost(0.0, 0.0, 0.0, 0.0, 0)

    part_b_monthly = policy.part_b_monthly * inflation_factor
    part_d_monthly = policy.part_d_monthly * inflation_factor
    base_annual_per_person = (part_b_monthly + part_d_monthly) * 12

    part_b_irmaa, part_d_irmaa = irmaa_monthly_surcharges(
        magi,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
    )
    irmaa_annual_per_person = (part_b_irmaa + part_d_irmaa) * 12

    base = base_annual_per_person * count
    irmaa = irmaa_annual_per_person * count
    medigap = 0.0
    if persons:
        medigap = household_medigap_annual(
            persons,
            ages=ages,
            start_age=policy.start_age,
            inflation_factor=inflation_factor,
        )
    return MedicareCost(
        total=base + irmaa + medigap,
        base=base,
        irmaa=irmaa,
        medigap=medigap,
        eligible_count=count,
    )
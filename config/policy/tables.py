"""Versioned federal tax, ACA, and Medicare policy tables (base year 2024)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- 2024 federal income tax (IRS Rev. Proc. baseline for planning) ---

MFJ_STANDARD_DEDUCTION_2024 = 29_200.0
SINGLE_STANDARD_DEDUCTION_2024 = 14_600.0
HOH_STANDARD_DEDUCTION_2024 = 21_900.0

MFJ_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (23_200, 0.10),
    (94_300, 0.12),
    (201_050, 0.22),
    (383_900, 0.24),
    (487_450, 0.32),
    (731_200, 0.35),
    (float("inf"), 0.37),
)

SINGLE_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (11_600, 0.10),
    (47_150, 0.12),
    (100_525, 0.22),
    (191_950, 0.24),
    (243_725, 0.32),
    (609_350, 0.35),
    (float("inf"), 0.37),
)

HOH_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (17_400, 0.10),
    (66_200, 0.12),
    (105_550, 0.22),
    (201_050, 0.24),
    (243_700, 0.32),
    (609_350, 0.35),
    (float("inf"), 0.37),
)

MFJ_SS_BASE_2024 = 32_000.0
MFJ_SS_TIER2_2024 = 44_000.0
SINGLE_SS_BASE_2024 = 25_000.0
SINGLE_SS_TIER2_2024 = 34_000.0
MFS_SS_BASE_LIVED_APART_2024 = 25_000.0
MFS_SS_TIER2_LIVED_APART_2024 = 34_000.0

# Additional standard deduction per qualifying senior (2024)
MFJ_ADDITIONAL_SD_65_2024 = 1_550.0
SINGLE_ADDITIONAL_SD_65_2024 = 1_950.0

# LTCG / qualified dividend rate tiers (top of taxable-income stack, 2024)
MFJ_LTCG_TIERS_2024: tuple[tuple[float, float], ...] = (
    (94_050, 0.0),
    (583_750, 0.15),
    (float("inf"), 0.20),
)
SINGLE_LTCG_TIERS_2024: tuple[tuple[float, float], ...] = (
    (47_025, 0.0),
    (518_900, 0.15),
    (float("inf"), 0.20),
)
HOH_LTCG_TIERS_2024: tuple[tuple[float, float], ...] = (
    (63_000, 0.0),
    (551_350, 0.15),
    (float("inf"), 0.20),
)

# NIIT (IRC 1411) MAGI thresholds
NIIT_RATE = 0.038
NIIT_MFJ_THRESHOLD_2024 = 250_000.0
NIIT_SINGLE_THRESHOLD_2024 = 200_000.0
NIIT_MFS_THRESHOLD_2024 = 125_000.0

# Schedule A — SALT cap (TCJA; not inflation-indexed)
SALT_DEDUCTION_CAP_2024 = 10_000.0
MEDICAL_AGI_FLOOR_PCT = 0.075

# AMT (Form 6251, 2024 Rev. Proc. baseline)
AMT_RATE_LOW = 0.26
AMT_RATE_HIGH = 0.28
AMT_EXEMPTION_MFJ_2024 = 126_500.0
AMT_EXEMPTION_SINGLE_2024 = 85_700.0
AMT_EXEMPTION_MFS_2024 = 43_350.0
AMT_PHASEOUT_START_MFJ_2024 = 1_218_700.0
AMT_PHASEOUT_START_SINGLE_2024 = 609_350.0
AMT_PHASEOUT_START_MFS_2024 = 609_350.0
AMT_26_BRACKET_MFJ_2024 = 232_600.0
AMT_26_BRACKET_SINGLE_2024 = 116_300.0
AMT_26_BRACKET_MFS_2024 = 116_300.0
AMT_EXEMPTION_PHASEOUT_RATE = 0.25

# QCD annual cap per IRA owner (SECURE 2.0, indexed)
QCD_ANNUAL_CAP_2024 = 105_000.0

# Social Security PIA bend points (eligibility year 2024)
SS_PIA_BEND1_2024 = 1_174.0
SS_PIA_BEND2_2024 = 7_078.0
SS_PIA_RATE1 = 0.90
SS_PIA_RATE2 = 0.32
SS_PIA_RATE3 = 0.15

# --- ACA (HHS FPL + IRA applicable-percentage tiers) ---

FPL_BASE_2024 = 15_060.0
FPL_PER_ADDITIONAL_PERSON_2024 = 5_380.0

ACA_APPLICABLE_PCT_TIERS: tuple[tuple[float, float], ...] = (
    (150, 0.0),
    (200, 0.02),
    (250, 0.04),
    (300, 0.06),
    (400, 0.085),
    (float("inf"), 0.085),
)

# --- Medicare (CMS 2024 baselines) ---

PART_B_STANDARD_MONTHLY_2024 = 174.70
PART_D_AVERAGE_MONTHLY_2024 = 55.50

# 2024 national-average Medigap monthly premiums (age 65, approximate)
MEDIGAP_PLAN_MONTHLY_2024: dict[str, float] = {
    "none": 0.0,
    "plan_g": 164.0,
    "plan_n": 120.0,
    "plan_f": 185.0,
}

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

# LTC care-setting cost multipliers (relative to home-health-aide baseline)
LTC_CARE_SETTING_MULTIPLIERS: dict[str, float] = {
    "home_health_aide": 1.0,
    "assisted_living": 1.35,
    "nursing_home": 1.60,
}

DEFAULT_POLICY_BASE_YEAR = 2024


@dataclass(frozen=True)
class PolicyYear:
    """Inflation-scaled policy constants for a projection year."""

    base_year: int
    inflation_factor: float
    mfj_standard_deduction: float
    single_standard_deduction: float
    hoh_standard_deduction: float
    mfj_brackets: tuple[tuple[float, float], ...]
    single_brackets: tuple[tuple[float, float], ...]
    hoh_brackets: tuple[tuple[float, float], ...]
    mfj_ss_base: float
    mfj_ss_tier2: float
    single_ss_base: float
    single_ss_tier2: float
    fpl_base: float
    fpl_per_additional_person: float
    aca_applicable_pct_tiers: tuple[tuple[float, float], ...]
    medicare_part_b_monthly: float
    medicare_part_d_monthly: float
    mfj_irmaa_tiers: tuple[tuple[float, float, float], ...]
    single_irmaa_tiers: tuple[tuple[float, float, float], ...]
    mfj_additional_sd_65: float
    single_additional_sd_65: float
    mfj_ltcg_tiers: tuple[tuple[float, float], ...]
    single_ltcg_tiers: tuple[tuple[float, float], ...]
    hoh_ltcg_tiers: tuple[tuple[float, float], ...]
    niit_mfj_threshold: float
    niit_single_threshold: float
    niit_mfs_threshold: float


def _inflate_brackets(
    brackets: tuple[tuple[float, float], ...],
    inflation_factor: float,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (ceiling * inflation_factor if ceiling != float("inf") else float("inf"), rate)
        for ceiling, rate in brackets
    )


def _inflate_irmaa_tiers(
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


def _embedded_policy_year(
    *,
    base_year: int,
    inflation_factor: float,
    medicare_part_b_monthly: float | None,
    medicare_part_d_monthly: float | None,
) -> PolicyYear:
    factor = max(0.01, inflation_factor)
    part_b = medicare_part_b_monthly if medicare_part_b_monthly is not None else PART_B_STANDARD_MONTHLY_2024
    part_d = medicare_part_d_monthly if medicare_part_d_monthly is not None else PART_D_AVERAGE_MONTHLY_2024
    return PolicyYear(
        base_year=base_year,
        inflation_factor=factor,
        mfj_standard_deduction=MFJ_STANDARD_DEDUCTION_2024 * factor,
        single_standard_deduction=SINGLE_STANDARD_DEDUCTION_2024 * factor,
        hoh_standard_deduction=HOH_STANDARD_DEDUCTION_2024 * factor,
        mfj_brackets=_inflate_brackets(MFJ_BRACKETS_2024, factor),
        single_brackets=_inflate_brackets(SINGLE_BRACKETS_2024, factor),
        hoh_brackets=_inflate_brackets(HOH_BRACKETS_2024, factor),
        mfj_ss_base=MFJ_SS_BASE_2024,
        mfj_ss_tier2=MFJ_SS_TIER2_2024,
        single_ss_base=SINGLE_SS_BASE_2024,
        single_ss_tier2=SINGLE_SS_TIER2_2024,
        fpl_base=FPL_BASE_2024 * factor,
        fpl_per_additional_person=FPL_PER_ADDITIONAL_PERSON_2024 * factor,
        aca_applicable_pct_tiers=ACA_APPLICABLE_PCT_TIERS,
        medicare_part_b_monthly=part_b * factor,
        medicare_part_d_monthly=part_d * factor,
        mfj_irmaa_tiers=_inflate_irmaa_tiers(MFJ_IRMAA_TIERS_2024, factor),
        single_irmaa_tiers=_inflate_irmaa_tiers(SINGLE_IRMAA_TIERS_2024, factor),
        mfj_additional_sd_65=MFJ_ADDITIONAL_SD_65_2024 * factor,
        single_additional_sd_65=SINGLE_ADDITIONAL_SD_65_2024 * factor,
        mfj_ltcg_tiers=_inflate_brackets(MFJ_LTCG_TIERS_2024, factor),
        single_ltcg_tiers=_inflate_brackets(SINGLE_LTCG_TIERS_2024, factor),
        hoh_ltcg_tiers=_inflate_brackets(HOH_LTCG_TIERS_2024, factor),
        niit_mfj_threshold=NIIT_MFJ_THRESHOLD_2024,
        niit_single_threshold=NIIT_SINGLE_THRESHOLD_2024,
        niit_mfs_threshold=NIIT_MFS_THRESHOLD_2024,
    )


def _live_policy_year(
    tables: dict[str, Any],
    *,
    inflation_factor: float,
    medicare_part_b_monthly: float | None,
    medicare_part_d_monthly: float | None,
) -> PolicyYear:
    factor = max(0.01, inflation_factor)
    part_b = (
        medicare_part_b_monthly
        if medicare_part_b_monthly is not None
        else float(tables.get("medicare_part_b_monthly", PART_B_STANDARD_MONTHLY_2024))
    )
    part_d = (
        medicare_part_d_monthly
        if medicare_part_d_monthly is not None
        else float(tables.get("medicare_part_d_monthly", PART_D_AVERAGE_MONTHLY_2024))
    )
    return PolicyYear(
        base_year=int(tables["base_year"]),
        inflation_factor=factor,
        mfj_standard_deduction=float(tables["mfj_standard_deduction"]),
        single_standard_deduction=float(tables["single_standard_deduction"]),
        hoh_standard_deduction=float(tables["hoh_standard_deduction"]),
        mfj_brackets=tables["mfj_brackets"],
        single_brackets=tables["single_brackets"],
        hoh_brackets=tables["hoh_brackets"],
        mfj_ss_base=float(tables["mfj_ss_base"]),
        mfj_ss_tier2=float(tables["mfj_ss_tier2"]),
        single_ss_base=float(tables["single_ss_base"]),
        single_ss_tier2=float(tables["single_ss_tier2"]),
        fpl_base=float(tables["fpl_base"]),
        fpl_per_additional_person=float(tables["fpl_per_additional_person"]),
        aca_applicable_pct_tiers=ACA_APPLICABLE_PCT_TIERS,
        medicare_part_b_monthly=part_b * factor if medicare_part_b_monthly is not None else float(part_b),
        medicare_part_d_monthly=part_d * factor if medicare_part_d_monthly is not None else float(part_d),
        mfj_irmaa_tiers=_inflate_irmaa_tiers(MFJ_IRMAA_TIERS_2024, factor),
        single_irmaa_tiers=_inflate_irmaa_tiers(SINGLE_IRMAA_TIERS_2024, factor),
        mfj_additional_sd_65=float(tables["mfj_additional_sd_65"]),
        single_additional_sd_65=float(tables["single_additional_sd_65"]),
        mfj_ltcg_tiers=tables["mfj_ltcg_tiers"],
        single_ltcg_tiers=tables["single_ltcg_tiers"],
        hoh_ltcg_tiers=tables["hoh_ltcg_tiers"],
        niit_mfj_threshold=float(tables["niit_mfj_threshold"]),
        niit_single_threshold=float(tables["niit_single_threshold"]),
        niit_mfs_threshold=float(tables["niit_mfs_threshold"]),
    )


def amt_constants_for_projection(*, inflation_factor: float = 1.0) -> dict[str, float]:
    """AMT exemption/phaseout/bracket constants for the active policy year."""
    from config.policy.live import ensure_active_policy, get_active_policy_meta, policy_tables_for_year

    factor = max(0.01, inflation_factor)
    meta = get_active_policy_meta()
    if meta is not None:
        tables = policy_tables_for_year(meta.tax_year, inflation_factor=factor)
        return {
            "exemption_mfj": float(tables["amt_exemption_mfj"]),
            "exemption_single": float(tables["amt_exemption_single"]),
            "exemption_mfs": float(tables["amt_exemption_mfs"]),
            "phaseout_start_mfj": float(tables["amt_phaseout_mfj"]),
            "phaseout_start_single": float(tables["amt_phaseout_single"]),
            "phaseout_start_mfs": float(tables["amt_phaseout_mfs"]),
            "bracket_26_mfj": float(tables["amt_26_bracket_mfj"]),
            "bracket_26_single": float(tables["amt_26_bracket_single"]),
            "bracket_26_mfs": float(tables["amt_26_bracket_single"]),
        }
    return {
        "exemption_mfj": AMT_EXEMPTION_MFJ_2024 * factor,
        "exemption_single": AMT_EXEMPTION_SINGLE_2024 * factor,
        "exemption_mfs": AMT_EXEMPTION_MFS_2024 * factor,
        "phaseout_start_mfj": AMT_PHASEOUT_START_MFJ_2024 * factor,
        "phaseout_start_single": AMT_PHASEOUT_START_SINGLE_2024 * factor,
        "phaseout_start_mfs": AMT_PHASEOUT_START_MFS_2024 * factor,
        "bracket_26_mfj": AMT_26_BRACKET_MFJ_2024 * factor,
        "bracket_26_single": AMT_26_BRACKET_SINGLE_2024 * factor,
        "bracket_26_mfs": AMT_26_BRACKET_MFS_2024 * factor,
    }


def active_policy_summary() -> dict[str, Any]:
    """Return active IRS policy metadata for GUI / assumptions export."""
    from config.policy.live import active_policy_label, get_active_policy_meta

    meta = get_active_policy_meta()
    if meta is None:
        return {
            "mode": "embedded",
            "tax_year": DEFAULT_POLICY_BASE_YEAR,
            "label": active_policy_label(),
        }
    return {
        "mode": "official",
        "tax_year": meta.tax_year,
        "source": meta.source,
        "activated_at": meta.activated_at,
        "label": active_policy_label(),
    }


def policy_for_projection(
    *,
    base_year: int = DEFAULT_POLICY_BASE_YEAR,
    inflation_factor: float = 1.0,
    medicare_part_b_monthly: float | None = None,
    medicare_part_d_monthly: float | None = None,
) -> PolicyYear:
    """Return policy tables scaled by cumulative inflation since the active base year."""
    from config.policy.live import ensure_active_policy, get_active_policy_meta, policy_tables_for_year

    ensure_active_policy()
    meta = get_active_policy_meta()
    if meta is not None:
        tables = policy_tables_for_year(meta.tax_year, inflation_factor=inflation_factor)
        return _live_policy_year(
            tables,
            inflation_factor=inflation_factor,
            medicare_part_b_monthly=medicare_part_b_monthly,
            medicare_part_d_monthly=medicare_part_d_monthly,
        )
    return _embedded_policy_year(
        base_year=base_year,
        inflation_factor=inflation_factor,
        medicare_part_b_monthly=medicare_part_b_monthly,
        medicare_part_d_monthly=medicare_part_d_monthly,
    )


def policy_tables_snapshot(policy: PolicyYear) -> dict[str, Any]:
    """Serializable summary for assumptions export / debugging."""
    return {
        "base_year": policy.base_year,
        "inflation_factor": round(policy.inflation_factor, 6),
        "mfj_standard_deduction": policy.mfj_standard_deduction,
        "single_standard_deduction": policy.single_standard_deduction,
        "fpl_base": policy.fpl_base,
        "medicare_part_b_monthly": policy.medicare_part_b_monthly,
    }
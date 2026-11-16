#!/usr/bin/env python3
"""Build golden_tax_scenarios.json with computed expected values."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from validation.golden_runner import DEFAULT_SCENARIOS_PATH, HANDLERS  # noqa: E402


def _scenario(
    *,
    scenario_id: str,
    category: str,
    scenario_type: str,
    inputs: dict,
    tolerance: float = 1.0,
    precision: int = 2,
) -> dict:
    actual = HANDLERS[scenario_type](inputs)
    if isinstance(actual, dict):
        expect = {key: round(value, precision) for key, value in actual.items()}
    else:
        expect = round(float(actual), precision)
    return {
        "id": scenario_id,
        "category": category,
        "type": scenario_type,
        "inputs": inputs,
        "expect": expect,
        "tolerance": tolerance,
    }


def build_scenario_definitions() -> list[dict]:
    scenarios: list[dict] = []

    ss_cases = [
        ("ss_mfj_none", 30_000, 10_000, "mfj"),
        ("ss_mfj_mid_tier", 30_000, 22_000, "mfj"),
        ("ss_mfj_high_tier", 30_000, 30_000, "mfj"),
        ("ss_single_none", 20_000, 15_000, "single"),
        ("ss_single_mid", 24_000, 18_000, "single"),
        ("ss_mfs_lived_apart_none", 18_000, 12_000, "mfs"),
        ("ss_inflation_scaled", 30_000, 22_000, "mfj"),
    ]
    for sid, ss, other, status in ss_cases:
        factor = 1.05 if sid == "ss_inflation_scaled" else 1.0
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="social_security",
                scenario_type="ss_taxable",
                inputs={
                    "ss_gross": ss,
                    "other_income": other,
                    "filing_status": status,
                    "inflation_factor": factor,
                    "mfs_lived_apart": sid == "ss_mfs_lived_apart_none",
                },
                tolerance=0.5,
            )
        )

    federal_income_cases = [
        ("fed_mfj_50k", "mfj", 50_000, 0),
        ("fed_mfj_100k", "mfj", 100_000, 0),
        ("fed_mfj_200k", "mfj", 200_000, 0),
        ("fed_single_60k", "single", 60_000, 0),
        ("fed_single_150k", "single", 150_000, 0),
        ("fed_hoh_80k", "head_of_household", 80_000, 0),
        ("fed_mfj_senior_sd", "mfj", 90_000, 2),
        ("fed_single_senior_sd", "single", 70_000, 1),
    ]
    for sid, status, income, seniors in federal_income_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="federal_brackets",
                scenario_type="federal_income_tax",
                inputs={
                    "filing_status": status,
                    "taxable_income": income,
                    "seniors_65_count": seniors,
                    "inflation_factor": 1.0,
                },
                tolerance=1.0,
            )
        )

    retirement_cases = [
        ("ret_mfj_moderate", {"ss_gross": 30_000, "pension_gross": 20_000, "withdrawal_gross": 50_000}),
        ("ret_mfj_high_withdrawal", {"ss_gross": 40_000, "pension_gross": 30_000, "withdrawal_gross": 120_000}),
        ("ret_single_low", {"filing_status": "single", "ss_gross": 18_000, "withdrawal_gross": 35_000}),
        ("ret_mfj_roth_heavy", {"ss_gross": 25_000, "withdrawal_gross": 80_000, "roth_fraction": 0.6}),
        ("ret_mfj_rental", {"ss_gross": 20_000, "rental_gross": 24_000, "withdrawal_gross": 40_000}),
        ("ret_mfj_earned", {"earned_income": 60_000, "withdrawal_gross": 10_000}),
        ("ret_ca_state", {"ss_gross": 30_000, "withdrawal_gross": 70_000, "state_code": "CA"}),
        ("ret_tx_no_state", {"ss_gross": 30_000, "withdrawal_gross": 70_000, "state_code": "TX"}),
        ("ret_ny_state", {"ss_gross": 35_000, "withdrawal_gross": 90_000, "state_code": "NY"}),
        ("ret_flat_mode", {"tax_mode": "flat", "flat_rate": 0.22, "withdrawal_gross": 80_000}),
        ("ret_niit_high", {"rental_gross": 40_000, "withdrawal_gross": 100_000, "taxable_portfolio_fraction": 1.0}),
        ("ret_niit_disabled", {"rental_gross": 40_000, "withdrawal_gross": 100_000, "niit_enabled": False}),
        ("ret_ltcg_mix", {"withdrawal_gross": 100_000, "taxable_portfolio_fraction": 0.5, "taxable_gain_fraction": 0.8}),
        ("ret_roth_conversion", {"withdrawal_gross": 40_000, "roth_conversion": 25_000}),
        ("ret_mfs", {"filing_status": "mfs", "ss_gross": 15_000, "withdrawal_gross": 45_000}),
    ]
    for sid, overrides in retirement_cases:
        inputs = {
            "ss_gross": 0,
            "pension_gross": 0,
            "rental_gross": 0,
            "earned_income": 0,
            "withdrawal_gross": 0,
            "roth_fraction": 0.0,
            "filing_status": "mfj",
            "inflation_factor": 1.0,
            "tax_mode": "federal_brackets",
            "flat_rate": 0.22,
            "state_code": "CA",
            "state_tax_enabled": True,
            "taxable_portfolio_fraction": 0.0,
            "niit_enabled": True,
            "roth_conversion": 0,
        }
        inputs.update(overrides)
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="retirement_tax",
                scenario_type="federal_retirement_tax",
                inputs=inputs,
                tolerance=2.0,
            )
        )

    ltcg_cases = [
        ("ltcg_mfj_ordinary_only", 80_000, 0),
        ("ltcg_mfj_with_gains", 60_000, 40_000),
        ("ltcg_single_stack", 45_000, 30_000),
        ("ltcg_hoh", 50_000, 25_000),
    ]
    for sid, ordinary, ltcg in ltcg_cases:
        status = "head_of_household" if sid == "ltcg_hoh" else ("single" if "single" in sid else "mfj")
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="ltcg",
                scenario_type="ltcg_tax",
                inputs={
                    "filing_status": status,
                    "ordinary_income": ordinary,
                    "ltcg": ltcg,
                    "inflation_factor": 1.0,
                },
                tolerance=1.0,
            )
        )

    niit_cases = [
        ("niit_mfj_below", 240_000, 20_000, "mfj"),
        ("niit_mfj_above", 320_000, 50_000, "mfj"),
        ("niit_single_above", 260_000, 40_000, "single"),
        ("niit_mfs_above", 180_000, 30_000, "mfs"),
    ]
    for sid, magi, nii, status in niit_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="niit",
                scenario_type="niit",
                inputs={"magi": magi, "net_investment_income": nii, "filing_status": status},
                tolerance=0.5,
            )
        )

    state_cases = [
        ("state_ca_moderate", "CA", 80_000, 5_000),
        ("state_ca_high", "CA", 200_000, 12_000),
        ("state_tx_zero", "TX", 120_000, 0),
        ("state_fl_zero", "FL", 120_000, 0),
        ("state_ny_moderate", "NY", 90_000, 4_000),
        ("state_wa_zero", "WA", 100_000, 0),
        ("state_or_progressive", "OR", 110_000, 6_000),
    ]
    for sid, code, gross, ss_taxable in state_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="state_tax",
                scenario_type="state_tax",
                inputs={
                    "state_code": code,
                    "gross_taxable": gross,
                    "ss_taxable": ss_taxable,
                    "filing_status": "mfj",
                    "inflation_factor": 1.0,
                },
                tolerance=2.0,
            )
        )

    aca_cases = [
        ("aca_low_magi", 28_000, [60, 58]),
        ("aca_mid_magi", 55_000, [63]),
        ("aca_high_magi", 120_000, [64]),
        ("aca_couple_62", 42_000, [62, 60]),
    ]
    for sid, magi, ages in aca_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="aca",
                scenario_type="aca_net",
                inputs={
                    "enrollee_ages": ages,
                    "magi": magi,
                    "household_size": 2,
                    "filing_status": "mfj",
                    "benchmark_monthly_age_21": 475,
                    "inflation_factor": 1.0,
                },
                tolerance=5.0,
            )
        )

    aca_pct_cases = [(120, 0.0), (175, 0.01), (225, 0.03), (275, 0.05), (350, 0.07), (450, 0.085)]
    for fpl, _ in aca_pct_cases:
        scenarios.append(
            _scenario(
                scenario_id=f"aca_pct_{fpl}",
                category="aca",
                scenario_type="aca_applicable_pct",
                inputs={"fpl_percent": fpl},
                tolerance=0.0001,
                precision=4,
            )
        )

    medicare_cases = [
        ("medicare_base_one", {"self": 67}, 80_000),
        ("medicare_couple", {"self": 67, "spouse": 65}, 90_000),
        ("medicare_irmaa_tier2", {"self": 70}, 280_000),
        ("medicare_irmaa_top", {"self": 72}, 900_000),
    ]
    for sid, ages, magi in medicare_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="medicare",
                scenario_type="medicare_total",
                inputs={
                    "ages": ages,
                    "magi": magi,
                    "filing_status": "mfj",
                    "inflation_factor": 1.0,
                },
                tolerance=5.0,
            )
        )

    irmaa_monthly_cases = [
        ("irmaa_mfj_std", 150_000, "mfj"),
        ("irmaa_mfj_tier3", 340_000, "mfj"),
        ("irmaa_single_tier2", 140_000, "single"),
    ]
    for sid, magi, status in irmaa_monthly_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="medicare",
                scenario_type="medicare_irmaa_monthly",
                inputs={"magi": magi, "filing_status": status, "inflation_factor": 1.0},
                tolerance=0.01,
            )
        )

    rmd_cases = [
        ("rmd_age_73", 1_000_000, 73, 73),
        ("rmd_age_80", 800_000, 80, 73),
        ("rmd_age_90", 500_000, 90, 73),
        ("rmd_age_75_legacy", 600_000, 75, 72),
    ]
    for sid, balance, age, start in rmd_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="rmd",
                scenario_type="rmd",
                inputs={
                    "traditional_balance": balance,
                    "age": age,
                    "rmd_start_age": start,
                },
                tolerance=1.0,
            )
        )

    fica_cases = [
        ("fica_under_cap", 80_000, "mfj"),
        ("fica_at_cap", 180_000, "mfj"),
        ("fica_additional_medicare", 260_000, "single"),
    ]
    for sid, wages, status in fica_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="payroll",
                scenario_type="fica",
                inputs={"wages": wages, "filing_status": status, "inflation_factor": 1.0},
                tolerance=1.0,
            )
        )

    ss_formula_cases = [
        ("ss_pia_low", 1_000),
        ("ss_pia_mid", 4_000),
        ("ss_pia_high", 9_000),
    ]
    for sid, aime in ss_formula_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="social_security",
                scenario_type="ss_pia",
                inputs={"aime": aime, "inflation_factor": 1.0},
                tolerance=0.5,
            )
        )

    ss_claim_cases = [
        ("ss_claim_early", 2_800, 62),
        ("ss_claim_fra", 3_200, 67),
        ("ss_claim_late", 3_200, 70),
    ]
    for sid, pia, age in ss_claim_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="social_security",
                scenario_type="ss_monthly_benefit",
                inputs={"pia_monthly": pia, "claiming_age": age, "birth_year": 1960},
                tolerance=0.5,
            )
        )

    policy_cases = [
        ("policy_mfj_sd", "mfj_standard_deduction", 1.0),
        ("policy_fpl", "fpl_base", 1.0),
        ("policy_inflation_sd", "mfj_standard_deduction", 1.03),
        ("policy_inflation_fpl", "fpl_base", 1.03),
    ]
    for sid, field, factor in policy_cases:
        scenarios.append(
            _scenario(
                scenario_id=sid,
                category="policy_tables",
                scenario_type="policy_field",
                inputs={"field": field, "inflation_factor": factor},
                tolerance=0.5,
            )
        )

    return scenarios


def main() -> None:
    scenarios = build_scenario_definitions()
    payload = {
        "version": 1,
        "base_year": 2024,
        "description": "Golden-file tax scenarios for annual regression validation",
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }
    DEFAULT_SCENARIOS_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(scenarios)} scenarios to {DEFAULT_SCENARIOS_PATH}")


if __name__ == "__main__":
    main()
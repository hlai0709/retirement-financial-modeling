#!/usr/bin/env python3
"""Full regression pass for Retirement Financial Modeling (Phases 1–9).

Selective runs (faster feedback after local edits):

  python scripts/regression_test.py --only "CPA Round 2,Phase 9"
  python scripts/regression_test.py --skip "Phase 4,Phase 5,Stochastic"
  python scripts/regression_test.py --changed
  REGRESSION_ONLY="CPA" python scripts/regression_test.py

Set REGRESSION_MC_PERF=1 to include the deferred 5000-path Monte Carlo wall-clock check.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class CheckResult:
    name: str
    phase: str
    status: str  # pass | fail | skip
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class RegressionReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.status == "pass")
        failed = sum(1 for r in self.results if r.status == "fail")
        skipped = sum(1 for r in self.results if r.status == "skip")
        lines = [
            "",
            "=" * 60,
            "REGRESSION SUMMARY",
            "=" * 60,
            f"  PASS:  {passed}",
            f"  FAIL:  {failed}",
            f"  SKIP:  {skipped}",
            f"  TOTAL: {len(self.results)}",
            "=" * 60,
        ]
        if failed:
            lines.append("\nFailures:")
            for r in self.results:
                if r.status == "fail":
                    lines.append(f"  [{r.phase}] {r.name}: {r.detail}")
        lines.append("")
        return "\n".join(lines)


# Phase substrings matched by --only / --skip (case-insensitive).
KNOWN_PHASES = (
    "Core",
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "Phase 4",
    "Phase 5",
    "Phase 6",
    "Phase 7",
    "Phase 8",
    "Phase 9",
    "Phase 0",
    "Stochastic A+",
    "CPA Precision",
    "CPA Round 2",
    "GUI",
)

# Map changed source files → phase substrings likely affected.
FILE_TO_PHASE_PATTERNS: dict[str, tuple[str, ...]] = {
    "model/projection.py": ("Core", "Phase 2", "CPA", "Phase 9"),
    "model/engine.py": ("Phase 2", "Phase 6", "CPA", "Stochastic"),
    "model/taxes.py": ("Phase 2", "Phase 8", "CPA", "Phase 9"),
    "model/cpa_tax.py": ("CPA", "Phase 9"),
    "model/payroll.py": ("Phase 2", "CPA", "Phase 9"),
    "model/state_taxes.py": ("Phase 2", "CPA", "Phase 8", "Phase 9"),
    "model/state_residence.py": ("Phase 2", "CPA"),
    "simulation/monte_carlo.py": ("Phase 4", "Stochastic", "Phase 9", "Phase 5"),
    "gui.py": ("GUI",),
    "validation/tax_return_validator.py": ("Phase 9",),
    "validation/golden_runner.py": ("Phase 8",),
    "config/policy/tables.py": ("Phase 8", "Phase 9", "CPA"),
    "config/policy/live.py": ("Phase 8", "Phase 9", "CPA"),
    "tools/fetch_policy_tables.py": ("Phase 8", "Phase 9"),
    "config/policy/irs_fetcher.py": ("Phase 9",),
    "config/policy/irs_llm.py": ("Phase 9",),
    "adviser/advisor_engine.py": ("Phase 5", "Phase 4"),
    "services/data_service.py": ("Phase 1", "Phase 3", "Phase 5"),
}


@dataclass
class RegressionFilter:
    only_patterns: tuple[str, ...] = ()
    skip_patterns: tuple[str, ...] = ()


_FILTER = RegressionFilter()


def _parse_pattern_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def configure_regression_filter(
    *,
    only: str | None = None,
    skip: str | None = None,
) -> RegressionFilter:
    global _FILTER
    only_patterns = _parse_pattern_list(only or os.getenv("REGRESSION_ONLY"))
    skip_patterns = _parse_pattern_list(skip or os.getenv("REGRESSION_SKIP"))
    _FILTER = RegressionFilter(only_patterns=only_patterns, skip_patterns=skip_patterns)
    return _FILTER


def _check_selected(phase: str, name: str) -> bool:
    if _FILTER.skip_patterns and any(
        pattern.lower() in phase.lower() or pattern.lower() in name.lower()
        for pattern in _FILTER.skip_patterns
    ):
        return False
    if _FILTER.only_patterns:
        return any(
            pattern.lower() in phase.lower() or pattern.lower() in name.lower()
            for pattern in _FILTER.only_patterns
        )
    return True


def _git_changed_project_files() -> set[str]:
    repo_root = PROJECT_ROOT.parent
    prefix = "Retirement_Financial_Modeling/"
    changed: set[str] = set()
    commands = (
        ["git", "diff", "--name-only", "HEAD", "--", prefix],
        ["git", "diff", "--name-only", "--cached", "--", prefix],
    )
    for command in commands:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            if line.startswith(prefix):
                changed.add(line[len(prefix) :])
    return changed


def phases_for_changed_files(files: set[str]) -> tuple[str, ...]:
    patterns: set[str] = {"Core"}
    for path in files:
        rel = path.replace("\\", "/")
        mapped = FILE_TO_PHASE_PATTERNS.get(rel)
        if mapped:
            patterns.update(mapped)
            continue
        basename = Path(rel).name
        mapped = FILE_TO_PHASE_PATTERNS.get(basename)
        if mapped:
            patterns.update(mapped)
    return tuple(sorted(patterns))


def _run_check(report: RegressionReport, phase: str, name: str, fn) -> None:
    if not _check_selected(phase, name):
        report.add(
            CheckResult(
                name=name,
                phase=phase,
                status="skip",
                detail="filtered (not in selection)",
            )
        )
        return
    start = time.perf_counter()
    try:
        detail = fn() or "OK"
        status = "pass"
    except Exception as exc:
        detail = f"{exc}"
        status = "fail"
        if os.getenv("REGRESSION_VERBOSE"):
            detail += "\n" + traceback.format_exc()
    elapsed = (time.perf_counter() - start) * 1000
    report.add(CheckResult(name=name, phase=phase, status=status, detail=detail, duration_ms=elapsed))


def _sample_params() -> dict:
    return {
        "mode": "joint",
        "current_year": 2026,
        "life_expectancy": 95,
        "inflation_rate": 0.03,
        "investment_return_mean": 0.07,
        "pre_retire_tax_rate": 0.35,
        "post_retire_tax_rate": 0.20,
        "tax_calculation": "flat",
        "filing_status": "mfj",
        "ss_tax_method": "irs_provisional",
        "state_of_residence": "CA",
        "state_tax_enabled": True,
        "rmd_start_age": 73,
        "doomsday_drop_year": None,
        "doomsday_drop_pct": 0.25,
        "rebalance_to_sp500": False,
        "tsla_target_price": None,
        "tsla_target_year": None,
        "tsla_current_price": 426.0,
        "persons": {
            "self": {
                "current_age": 40,
                "retirement_age": 65,
                "initial_assets": 500_000,
                "annual_contributions": 20_000,
                "base_expenses": 60_000,
                "health_insurance_post_retire": 12_000,
                "long_term_care_annual": 5_000,
                "rental_income_start": 12_000,
                "rental_growth_rate": 0.03,
                "pension_start_age": 65,
                "pension_amount": 0,
                "ss_start_age": 67,
                "ss_amount": 25_000,
            },
            "wife": {
                "current_age": 38,
                "retirement_age": 65,
                "initial_assets": 300_000,
                "annual_contributions": 15_000,
                "base_expenses": 50_000,
                "health_insurance_post_retire": 10_000,
                "long_term_care_annual": 5_000,
                "rental_income_start": 0,
                "rental_growth_rate": 0.03,
                "pension_start_age": 65,
                "pension_amount": 0,
                "ss_start_age": 67,
                "ss_amount": 20_000,
            },
        },
    }


def run_regression() -> RegressionReport:
    report = RegressionReport()

    # --- Core imports ---
    def check_core_imports():
        import gui  # noqa: F401
        import model  # noqa: F401
        import simulation.monte_carlo  # noqa: F401
        import services.data_service  # noqa: F401
        from adviser.advisor_engine import analyze  # noqa: F401
        from config.model_fields import ALL_FIELDS, llm_researchable_fields  # noqa: F401
        assert len(ALL_FIELDS) >= 20
        assert len(llm_researchable_fields()) >= 10
        return f"{len(ALL_FIELDS)} fields, {len(llm_researchable_fields())} LLM-eligible"

    _run_check(report, "Core", "Module imports", check_core_imports)

    def check_federal_brackets_are_not_shifted():
        from config.policy.irs_fetcher import _brackets_from_blocks
        from model.taxes import federal_income_tax, tax_table_for_filing_status

        parsed = _brackets_from_blocks(
            [
                (0.10, 23_850),
                (0.12, 23_850),
                (0.22, 96_950),
                (0.24, 206_700),
                (0.32, 394_600),
                (0.35, 501_050),
                (0.37, 751_600),
            ]
        )
        assert parsed[0] == [23_850.0, 0.10]
        assert parsed[1] == [96_950.0, 0.12]
        assert parsed[-2] == [751_600.0, 0.35]
        assert parsed[-1] == ["inf", 0.37]
        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        tax = federal_income_tax(150_000, table)
        assert 22_000 < tax < 24_000, tax
        return f"MFJ tax on $150,000 taxable is ${tax:,.0f}"

    _run_check(report, "Core", "Federal bracket ceilings", check_federal_brackets_are_not_shifted)

    def check_gain_fraction_is_not_divided_twice():
        from model.engine import tax_policy_from_params

        parsed = tax_policy_from_params({"taxable_gain_fraction": 0.5})
        explicit_zero = tax_policy_from_params({"taxable_gain_fraction": 0})
        missing = tax_policy_from_params({})
        percent = tax_policy_from_params({"taxable_gain_fraction": 50})
        assert abs(parsed.taxable_gain_fraction - 0.5) < 1e-9
        assert explicit_zero.taxable_gain_fraction == 0.0
        assert abs(missing.taxable_gain_fraction - 0.5) < 1e-9
        assert abs(percent.taxable_gain_fraction - 0.5) < 1e-9
        return "50% snapshot stays 0.5; explicit 0% stays 0"

    _run_check(report, "Core", "Taxable gain fraction units", check_gain_fraction_is_not_divided_twice)

    def check_wages_cover_their_own_tax():
        from model.taxes import solve_withdrawal_for_expenses, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        withdrawal, *_rest = solve_withdrawal_for_expenses(
            40_000,
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=120_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.22,
            table=table,
            rmd_floor=0,
            state_tax_enabled=False,
            niit_enabled=False,
        )
        assert withdrawal < 1.0, withdrawal
        return f"paycheck covers $40k spending; withdrawal ${withdrawal:,.2f}"

    _run_check(report, "Core", "Wages count as spendable cash", check_wages_cover_their_own_tax)

    def check_benefits_inflate_before_claim():
        from model.engine import PersonFlows, inflate_flows

        flows = PersonFlows(
            contributions=0,
            earned_income=0,
            expenses=60_000,
            additives={},
            rental=0,
            ss=25_000,
            pension=10_000,
            ss_active=False,
            pension_active=False,
        )
        inflate_flows(flows, year_inflation=0.03, rental_growth=0.03)
        assert abs(flows.ss - 25_750) < 1e-6
        assert abs(flows.pension - 10_300) < 1e-6
        return f"unclaimed SS ${flows.ss:,.0f} after 3% inflation"

    _run_check(report, "Core", "Benefits inflate before they start", check_benefits_inflate_before_claim)

    def check_salt_cap_follows_obbba():
        from model.cpa_tax import salt_deduction_cap

        assert salt_deduction_cap(tax_year=2024, filing_status="mfj", magi=0) == 10_000
        assert salt_deduction_cap(tax_year=2025, filing_status="mfj", magi=100_000) == 40_000
        assert salt_deduction_cap(tax_year=2025, filing_status="mfs", magi=0) == 20_000
        cap_2026 = salt_deduction_cap(tax_year=2026, filing_status="mfj", magi=100_000)
        assert abs(cap_2026 - 40_400) < 1
        phased = salt_deduction_cap(tax_year=2026, filing_status="mfj", magi=605_000)
        assert abs(phased - 10_400) < 1
        assert salt_deduction_cap(tax_year=2030, filing_status="single", magi=0) == 10_000
        return f"2026 cap ${cap_2026:,.0f}; phase-down ${phased:,.0f}"

    _run_check(report, "Core", "SALT cap follows the statute", check_salt_cap_follows_obbba)

    def check_unused_deduction_reduces_gains():
        from model.taxes import federal_ltcg_tax, federal_tax_with_ltcg, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        taxed, _, _ = federal_tax_with_ltcg(
            ordinary_income=0,
            ltcg=150_000,
            table=table,
            filing_status="mfj",
        )
        on_all_gains = federal_ltcg_tax(
            150_000,
            ordinary_taxable_income=0.0,
            ltcg_tiers=table.ltcg_tiers,
        )
        assert taxed < on_all_gains - 1_000, (taxed, on_all_gains)
        return f"gains after unused deduction ${taxed:,.0f} vs ${on_all_gains:,.0f} on the full $150k"

    _run_check(report, "Core", "Unused deduction reduces long-term gains", check_unused_deduction_reduces_gains)

    def check_amt_does_not_add_salt_twice():
        from model.cpa_tax import compute_amti

        itemized = compute_amti(
            ordinary_income=100_000,
            ltcg=50_000,
            itemized_deduction=40_000,
            salt_deducted=10_000,
            deduction_method="itemized",
        )
        standard = compute_amti(
            ordinary_income=100_000,
            ltcg=50_000,
            deduction_method="standard",
        )
        assert itemized == 120_000
        assert standard == 150_000
        return "itemized AMTI subtracts mortgage interest and charity, not the standard deduction"

    _run_check(report, "Core", "AMT add-back is SALT only", check_amt_does_not_add_salt_twice)

    def check_ss_and_niit_are_not_indexed():
        from config.policy.live import policy_tables_for_year

        tables = policy_tables_for_year(2026, inflation_factor=1.5)
        assert tables["mfj_ss_base"] == 32_000
        assert tables["single_ss_base"] == 25_000
        assert tables["niit_mfj_threshold"] == 250_000
        assert tables["niit_mfs_threshold"] == 125_000
        return "provisional-income and NIIT thresholds stay at the statute"

    _run_check(report, "Core", "SS and NIIT thresholds stay fixed", check_ss_and_niit_are_not_indexed)

    def check_hundred_percent_roth_is_allowed():
        from model.projection import _validate_params

        params = _sample_params()
        params["roth_portfolio_pct"] = 1.0
        params["contribution_roth_pct"] = 1.0
        _validate_params(params)
        params["roth_portfolio_pct"] = 1.01
        try:
            _validate_params(params)
        except ValueError:
            return "100% Roth is accepted; above 100% is rejected"
        raise AssertionError("expected rejection above 100% Roth")

    _run_check(report, "Core", "100% Roth is allowed", check_hundred_percent_roth_is_allowed)

    def check_phase_percents_keep_parsed_ratios():
        from model.expenses import expense_policy_from_params

        high = expense_policy_from_params({"expense_go_go_pct": 1.6})
        whole = expense_policy_from_params({"expense_slow_go_pct": 80})
        blank = expense_policy_from_params({})
        assert abs(high.go_go_pct - 1.6) < 1e-9
        assert abs(whole.slow_go_pct - 0.80) < 1e-9
        assert abs(blank.go_go_pct - 1.0) < 1e-9
        return "160% stays 1.60; 80 stays 0.80; blank stays 100%"

    _run_check(report, "Core", "Phase percents keep parsed ratios", check_phase_percents_keep_parsed_ratios)

    def check_aime_caps_at_wage_base():
        from model.ss import aime_from_career_earnings, ss_contribution_base

        base = ss_contribution_base(2026)
        assert base == 184_500
        assert aime_from_career_earnings(300_000, wage_base=base) == base / 12
        return f"2026 wage base ${base:,.0f}"

    _run_check(report, "Core", "AIME respects the wage base", check_aime_caps_at_wage_base)

    def check_interest_stops_after_payoff():
        from model.cpa_tax import ItemizedInputs, itemized_for_year

        itemized = ItemizedInputs(mortgage_interest=12_000)
        still = itemized_for_year(itemized, {"self": {"mortgage_payoff_year": 2030}}, 2030)
        done = itemized_for_year(itemized, {"self": {"mortgage_payoff_year": 2030}}, 2031)
        assert still.mortgage_interest == 12_000
        assert done.mortgage_interest == 0

        parked = ItemizedInputs(mortgage_interest_second=4_000)
        one_loan = {"self": {"mortgage_annual": 24_000, "mortgage_payoff_year": 2030}}
        during = itemized_for_year(parked, one_loan, 2030)
        after = itemized_for_year(parked, one_loan, 2031)
        assert during.mortgage_interest_second == 4_000
        assert after.mortgage_interest_second == 0
        open_loan = {"self": {"mortgage_annual": 24_000}}
        assert itemized_for_year(parked, open_loan, 2040).mortgage_interest_second == 4_000

        both = ItemizedInputs(mortgage_interest=9_000, mortgage_interest_second=4_000)
        two_loans = {
            "self": {
                "mortgage_annual": 24_000,
                "mortgage_payoff_year": 2030,
                "mortgage_second_annual": 12_000,
                "mortgage_second_payoff_year": 2040,
            }
        }
        between = itemized_for_year(both, two_loans, 2035)
        assert between.mortgage_interest == 0
        assert between.mortgage_interest_second == 4_000
        assert itemized_for_year(both, two_loans, 2041).mortgage_interest_second == 0
        return "itemized interest is zero the year after payoff"

    _run_check(report, "Core", "Itemized interest ends at payoff", check_interest_stops_after_payoff)

    def check_1098_on_empty_plan_does_not_create_payment():
        from dataclasses import dataclass

        from app.core.lazy_mode.orchestrator import _stack_mortgage_if_needed
        from app.core.planning_snapshot import PlanningSnapshot

        @dataclass
        class Suggestion:
            field_key: str
            raw_value: str
            source_quote: str = ""
            display_value: str = ""

        rows = _stack_mortgage_if_needed(
            PlanningSnapshot(),
            [
                Suggestion("mortgage_annual", "24000", "payment coupon"),
                Suggestion("itemized_mortgage_interest", "9000", "box 1"),
                Suggestion("mortgage_payoff_year", "2050", "maturity"),
            ],
            document_id="form_1098",
            person="self",
        )
        keys = [row.field_key for row in rows]
        assert "mortgage_annual" not in keys
        assert "itemized_mortgage_interest" in keys
        assert "mortgage_payoff_year" in keys
        return "a 1098 on an empty plan records interest, not a payment"

    _run_check(
        report,
        "Core",
        "A 1098 on an empty plan does not create a payment",
        check_1098_on_empty_plan_does_not_create_payment,
    )

    def check_each_person_rmd_uses_own_age():
        from model.accounts import AccountBalances, HouseholdLedger
        from model.engine import pooled_rmd_floor

        ledger = HouseholdLedger(
            by_person={"__household__": AccountBalances(traditional=1_000_000)}
        )
        persons = {
            "self": {"asset_share_pct": 60},
            "wife": {"asset_share_pct": 40},
        }
        floor = pooled_rmd_floor(
            ledger,
            persons,
            {"self": 75, "wife": 60},
            {"self": 73, "wife": 73},
        )
        expected = 600_000 / 24.6
        assert abs(floor - expected) < 1, floor
        return f"older spouse RMD ${floor:,.0f} on their share only"

    _run_check(report, "Core", "RMD uses each person's age", check_each_person_rmd_uses_own_age)

    def check_pre_marriage_withdrawal_hits_the_pool():
        from model import run_retirement_projection

        params = _sample_params()
        params["marriage_year"] = 2030
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["state_tax_enabled"] = False
        params["tax_calculation"] = "flat"
        params["post_retire_tax_rate"] = 0.0
        params["pre_retire_tax_rate"] = 0.0
        params["life_expectancy"] = 72
        for name, age, assets in (("self", 70, 400_000), ("wife", 68, 200_000)):
            person = params["persons"][name]
            person["current_age"] = age
            person["retirement_age"] = 65
            person["initial_assets"] = assets
            person["annual_contributions"] = 0
            person["base_expenses"] = 40_000
            person["ss_amount"] = 0
            person["pension_amount"] = 0
            person["rental_income_start"] = 0
            person["health_insurance_post_retire"] = 0
            person["health_insurance_pre_medicare"] = 0
            person["long_term_care_annual"] = 0
        row = run_retirement_projection(params)["joint"].iloc[0]
        assert row["Withdrawals_Gross"] > 10_000
        assert row["Assets_End"] < row["Assets_Start"] - 10_000
        return (
            f"pre-marriage withdrawal ${row['Withdrawals_Gross']:,.0f} "
            f"leaves ${row['Assets_End']:,.0f}"
        )

    _run_check(
        report,
        "Core",
        "Pre-marriage withdrawals come from the household pool",
        check_pre_marriage_withdrawal_hits_the_pool,
    )

    def check_survivor_keeps_mortgage_and_larger_benefit():
        from model import run_retirement_projection

        params = _sample_params()
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["state_tax_enabled"] = False
        params["life_expectancy"] = 90
        self_p = params["persons"]["self"]
        wife_p = params["persons"]["wife"]
        self_p.update(
            current_age=86,
            retirement_age=65,
            life_expectancy=90,
            base_expenses=40_000,
            ss_amount=30_000,
            ss_start_age=67,
            pension_amount=0,
            rental_income_start=0,
            mortgage_annual=0,
            annual_contributions=0,
            health_insurance_post_retire=0,
            health_insurance_pre_medicare=0,
            long_term_care_annual=0,
        )
        wife_p.update(
            current_age=86,
            retirement_age=65,
            life_expectancy=86,
            base_expenses=35_000,
            ss_amount=20_000,
            ss_start_age=67,
            pension_amount=0,
            rental_income_start=0,
            mortgage_annual=12_000,
            mortgage_payoff_year=2040,
            annual_contributions=0,
            health_insurance_post_retire=0,
            health_insurance_pre_medicare=0,
            long_term_care_annual=0,
        )
        df = run_retirement_projection(params)["joint"]
        both = df[df["Year"] == 2026].iloc[0]
        survivor = df[df["Year"] == 2027].iloc[0]
        assert abs(both["Expenses"] - 87_000) < 1, both["Expenses"]
        assert abs(survivor["Expenses"] - 87_000) < 1, survivor["Expenses"]
        assert abs(survivor["SS"] - 30_000) < 1, survivor["SS"]
        return f"survivor expenses ${survivor['Expenses']:,.0f}, SS ${survivor['SS']:,.0f}"

    _run_check(
        report,
        "Core",
        "Survivor keeps the mortgage and the larger benefit",
        check_survivor_keeps_mortgage_and_larger_benefit,
    )

    def check_mortgage_import_does_not_double_a_refresh():
        from dataclasses import dataclass

        from app.core.lazy_mode.orchestrator import (
            _stack_mortgage_if_needed,
            _stack_property_tax_if_needed,
        )
        from app.core.planning_snapshot import PlanningSnapshot
        from config.model_fields import FIELDS_BY_KEY
        from providers.document_extractors.statement_pdf import (
            extract_1098_suggestions,
            extract_mortgage_statement_suggestions,
            extract_property_tax_bill_suggestions,
        )

        @dataclass
        class Suggestion:
            field_key: str
            raw_value: str
            source_quote: str = ""
            display_value: str = ""

        snapshot = PlanningSnapshot()
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_annual"], "24000", person="self")
        snapshot.set_source("self", "mortgage_annual", "Mortgage statement import")
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_payoff_year"], "2050", person="self")
        refreshed = _stack_mortgage_if_needed(
            snapshot,
            [Suggestion("mortgage_annual", "25000", "new statement")],
            document_id="mortgage_statement",
            person="self",
        )
        assert refreshed[0].raw_value == "25000"
        second = _stack_mortgage_if_needed(
            snapshot,
            [Suggestion("mortgage_annual", "18000", "other house")],
            document_id="mortgage_statement",
            person="self",
        )
        assert second[0].field_key == "mortgage_second_annual"
        assert float(str(second[0].raw_value).replace(",", "")) == 18_000
        form = _stack_mortgage_if_needed(
            snapshot,
            [Suggestion("mortgage_annual", "9000", "1098 interest")],
            document_id="form_1098",
            person="self",
        )
        assert form == []

        snapshot.set_raw(FIELDS_BY_KEY["property_tax_annual"], "8000", person="self")
        snapshot.set_source("self", "property_tax_annual", "Property tax bill import")
        same_bill = _stack_property_tax_if_needed(
            snapshot,
            [Suggestion("property_tax_annual", "8200")],
            document_id="property_tax_bill",
            person="self",
        )
        assert same_bill[0].raw_value == "8200"

        statement = """
        Statement date 09/15/2026
        Current P&I Payment $2,000.00
        Current Total Pymt $2,500.00
        YTD interest $8,000.00
        Outstanding Principal $400,000.00
        Interest rate 6.00%
        Maturity date 2052
        """
        rows = {row.field_key: row for row in extract_mortgage_statement_suggestions(statement)}
        payment = float(str(rows["mortgage_annual"].raw_value).replace(",", "").replace("$", ""))
        assert abs(payment - 24_000) < 1, payment
        assert rows["mortgage_annual"].confidence == "high"
        assert rows["itemized_mortgage_interest"].confidence == "low"
        assert rows["mortgage_payoff_year"].raw_value in {"2052", "2052.0"}
        from app.core.document_ingestion.ingestion_controller import build_review_rows

        ytd_review = build_review_rows(
            PlanningSnapshot(),
            extract_mortgage_statement_suggestions(statement),
            document_label="statement.pdf",
        )
        ytd_interest = next(
            row for row in ytd_review if row["field_key"] == "itemized_mortgage_interest"
        )
        assert ytd_interest["apply"] is False

        form_rows = {
            row.field_key: row
            for row in extract_1098_suggestions(
                "Tax year 2026\nMortgage interest received $9,000.00\n"
            )
        }
        assert form_rows["itemized_mortgage_interest"].confidence == "high"
        assert "mortgage_annual" not in form_rows

        escrow_only = {
            row.field_key: row
            for row in extract_mortgage_statement_suggestions(
                "Statement date 03/01/2026\nCurrent Total Pymt $2,500.00\n"
            )
        }
        assert "mortgage_annual" not in escrow_only

        both = extract_property_tax_bill_suggestions(
            "Total Amount Due $8,000.00\nFirst installment $4,000.00"
        )
        annual = float(str(both[0].raw_value).replace(",", "").replace("$", ""))
        assert abs(annual - 8_000) < 1, annual
        only_installment = extract_property_tax_bill_suggestions("First installment $4,000.00")
        doubled = float(str(only_installment[0].raw_value).replace(",", "").replace("$", ""))
        assert abs(doubled - 8_000) < 1, doubled

        different_maturity = _stack_mortgage_if_needed(
            snapshot,
            [
                Suggestion("mortgage_annual", "25000", "similar payment"),
                Suggestion("mortgage_payoff_year", "2040", "other maturity"),
            ],
            document_id="mortgage_statement",
            person="self",
        )
        by_key = {item.field_key: item for item in different_maturity}
        assert by_key["mortgage_second_annual"].raw_value == "25000"
        assert by_key["mortgage_second_payoff_year"].field_key == "mortgage_second_payoff_year"

        refinance = _stack_mortgage_if_needed(
            snapshot,
            [
                Suggestion("mortgage_annual", "40000", "rate reset"),
                Suggestion("mortgage_payoff_year", "2050", "same maturity"),
            ],
            document_id="mortgage_statement",
            person="self",
        )
        assert refinance[0].field_key == "mortgage_annual"
        assert refinance[0].raw_value == "40000"

        snapshot.set_raw(FIELDS_BY_KEY["mortgage_second_annual"], "18000", person="self")
        snapshot.set_source("self", "mortgage_second_annual", "Mortgage statement import")
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_second_payoff_year"], "2040", person="self")
        refresh_second = _stack_mortgage_if_needed(
            snapshot,
            [
                Suggestion("mortgage_annual", "19000", "next month"),
                Suggestion("mortgage_payoff_year", "2040", "same second loan"),
            ],
            document_id="mortgage_statement",
            person="self",
        )
        refreshed = {item.field_key: item for item in refresh_second}
        assert refreshed["mortgage_second_annual"].raw_value == "19000"
        assert "mortgage_annual" not in refreshed

        snapshot.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest"], "9000")
        form_second = _stack_mortgage_if_needed(
            snapshot,
            [
                Suggestion("itemized_mortgage_interest", "4000", "second 1098"),
                Suggestion("mortgage_payoff_year", "2040", "second maturity"),
                Suggestion("mortgage_payoff_month", "6", "june"),
            ],
            document_id="form_1098",
            person="self",
        )
        form_by_key = {item.field_key: item for item in form_second}
        assert "mortgage_payoff_year" not in form_by_key
        assert form_by_key["mortgage_second_payoff_year"].raw_value == "2040"
        assert form_by_key["itemized_mortgage_interest_second"].raw_value == "4000"
        assert "itemized_mortgage_interest" not in form_by_key

        snapshot.set_raw(FIELDS_BY_KEY["mortgage_second_annual"], "0", person="self")
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_second_payoff_year"], "", person="self")
        snapshot.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest"], "0")
        from app.core.document_ingestion.ingestion_controller import apply_document_import

        first = apply_document_import(
            snapshot,
            [
                {
                    "apply": True,
                    "field_key": "mortgage_annual",
                    "raw_value": "24000",
                    "person": "self",
                    "source": "Imported from first.pdf",
                },
                {
                    "apply": True,
                    "field_key": "mortgage_payoff_year",
                    "raw_value": "2050",
                    "person": "self",
                    "source": "Imported from first.pdf",
                },
            ],
            document_type="mortgage_statement",
            document_label="first.pdf",
        )
        assert first["ok"]
        second_apply = apply_document_import(
            snapshot,
            [
                {
                    "apply": True,
                    "field_key": "mortgage_annual",
                    "raw_value": "18000",
                    "person": "self",
                    "source": "Imported from second.pdf",
                },
                {
                    "apply": True,
                    "field_key": "mortgage_payoff_year",
                    "raw_value": "2044",
                    "person": "self",
                    "source": "Imported from second.pdf",
                },
            ],
            document_type="mortgage_statement",
            document_label="second.pdf",
        )
        assert second_apply["ok"]
        assert snapshot.get_raw(FIELDS_BY_KEY["mortgage_annual"], person="self") == "24000"
        assert snapshot.get_raw(FIELDS_BY_KEY["mortgage_payoff_year"], person="self") == "2050"
        assert snapshot.get_raw(FIELDS_BY_KEY["mortgage_second_annual"], person="self") == "18000"
        assert snapshot.get_raw(FIELDS_BY_KEY["mortgage_second_payoff_year"], person="self") == "2044"
        tax_apply = apply_document_import(
            snapshot,
            [
                {
                    "apply": True,
                    "field_key": "property_tax_annual",
                    "raw_value": "3000",
                    "person": "self",
                    "source": "Imported from parcel.pdf",
                }
            ],
            document_type="property_tax_bill",
            document_label="parcel.pdf",
        )
        assert tax_apply["ok"]
        assert snapshot.get_raw(FIELDS_BY_KEY["property_tax_annual"], person="self") == "11000"
        refresh_bill = apply_document_import(
            snapshot,
            [
                {
                    "apply": True,
                    "field_key": "property_tax_annual",
                    "raw_value": "8200",
                    "person": "self",
                    "source": "Imported from parcel.pdf",
                }
            ],
            document_type="property_tax_bill",
            document_label="parcel-refresh.pdf",
        )
        assert refresh_bill["ok"]
        assert snapshot.get_raw(FIELDS_BY_KEY["property_tax_annual"], person="self") == "11200"
        return "same loan refreshes; a second loan stacks; 1098 does not add a payment"

    _run_check(
        report,
        "Core",
        "Mortgage and property-tax imports do not double a refresh",
        check_mortgage_import_does_not_double_a_refresh,
    )

    def check_second_loan_ytd_interest_and_unmatched_1098():
        from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
        from app.core.document_ingestion.ingestion_controller import build_review_rows
        from app.core.lazy_mode.orchestrator import _stack_mortgage_if_needed
        from app.core.planning_snapshot import PlanningSnapshot
        from config.model_fields import FIELDS_BY_KEY

        snapshot = PlanningSnapshot()
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_annual"], "24000", person="self")
        snapshot.set_source("self", "mortgage_annual", "Mortgage statement import")
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_payoff_year"], "2050", person="self")
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_second_annual"], "18000", person="self")
        snapshot.set_source("self", "mortgage_second_annual", "Mortgage statement import")
        snapshot.set_raw(FIELDS_BY_KEY["mortgage_second_payoff_year"], "2040", person="self")

        def suggestion(key: str, raw: str, confidence: str) -> ExtractedFieldSuggestion:
            return ExtractedFieldSuggestion(
                field_key=key,
                person="self",
                label=key,
                raw_value=raw,
                display_value=raw,
                confidence=confidence,
                source_quote="Mortgage statement year-to-date interest (not a full year)",
                document_type="pdf",
            )

        stacked = _stack_mortgage_if_needed(
            snapshot,
            [
                suggestion("mortgage_annual", "19000", "high"),
                suggestion("mortgage_payoff_year", "2040", "high"),
                suggestion("itemized_mortgage_interest", "5000", "low"),
            ],
            document_id="mortgage_statement",
            person="self",
        )
        review = build_review_rows(snapshot, stacked, document_label="second.pdf")
        interest = next(item for item in review if "interest" in str(item["field_key"]))
        assert interest["field_key"] == "itemized_mortgage_interest_second"
        assert interest["apply"] is False

        form = _stack_mortgage_if_needed(
            snapshot,
            [
                suggestion("itemized_mortgage_interest", "4000", "high"),
                suggestion("mortgage_payoff_year", "2040", "high"),
            ],
            document_id="form_1098",
            person="self",
        )
        form_review = build_review_rows(snapshot, form, document_label="second-1098.pdf")
        form_interest = next(item for item in form_review if "interest" in str(item["field_key"]))
        assert form_interest["field_key"] == "itemized_mortgage_interest_second"
        assert form_interest["apply"] is True

        empty = PlanningSnapshot()
        seeded = _stack_mortgage_if_needed(
            empty,
            [
                suggestion("mortgage_annual", "24000", "high"),
                suggestion("itemized_mortgage_interest", "9000", "high"),
                suggestion("mortgage_payoff_year", "2050", "high"),
            ],
            document_id="form_1098",
            person="self",
        )
        seeded_keys = [item.field_key for item in seeded]
        assert "mortgage_annual" not in seeded_keys
        assert "itemized_mortgage_interest" in seeded_keys
        assert "mortgage_payoff_year" in seeded_keys

        only_loan = PlanningSnapshot()
        only_loan.set_raw(FIELDS_BY_KEY["mortgage_annual"], "24000", person="self")
        only_loan.set_source("self", "mortgage_annual", "Mortgage statement import")
        only_loan.set_raw(FIELDS_BY_KEY["mortgage_payoff_year"], "2050", person="self")
        only_loan.set_raw(FIELDS_BY_KEY["mortgage_payoff_month"], "6", person="self")
        only_loan.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest"], "9000")
        mismatched_one = {
            item.field_key: item
            for item in _stack_mortgage_if_needed(
                only_loan,
                [
                    suggestion("itemized_mortgage_interest", "15000", "high"),
                    suggestion("mortgage_payoff_year", "2030", "high"),
                    suggestion("mortgage_payoff_month", "3", "high"),
                ],
                document_id="form_1098",
                person="self",
            )
        }
        assert "mortgage_payoff_year" not in mismatched_one
        assert "mortgage_payoff_month" not in mismatched_one
        assert "mortgage_second_payoff_year" not in mismatched_one
        assert mismatched_one["itemized_mortgage_interest_second"].raw_value == "15000"

        close_to_both = PlanningSnapshot()
        close_to_both.set_raw(FIELDS_BY_KEY["mortgage_annual"], "24000", person="self")
        close_to_both.set_source("self", "mortgage_annual", "Mortgage statement import")
        close_to_both.set_raw(FIELDS_BY_KEY["mortgage_second_annual"], "18000", person="self")
        close_to_both.set_source("self", "mortgage_second_annual", "Mortgage statement import")
        close_to_both.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest"], "9000")
        close_to_both.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest_second"], "8500")
        stayed = {
            item.field_key: item
            for item in _stack_mortgage_if_needed(
                close_to_both,
                [suggestion("itemized_mortgage_interest", "8800", "high")],
                document_id="form_1098",
                person="self",
            )
        }
        assert stayed["itemized_mortgage_interest"].raw_value == "8800"
        assert "itemized_mortgage_interest_second" not in stayed

        snapshot.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest"], "9000")
        snapshot.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest_second"], "4000")
        unmatched = _stack_mortgage_if_needed(
            snapshot,
            [
                suggestion("itemized_mortgage_interest", "15000", "high"),
                suggestion("mortgage_payoff_year", "2030", "high"),
            ],
            document_id="form_1098",
            person="self",
        )
        assert [item.field_key for item in unmatched] == []

        snapshot.set_raw(FIELDS_BY_KEY["itemized_mortgage_interest_second"], "0")
        filled = {
            item.field_key: item
            for item in _stack_mortgage_if_needed(
                snapshot,
                [
                    suggestion("itemized_mortgage_interest", "15000", "high"),
                    suggestion("mortgage_payoff_year", "2030", "high"),
                ],
                document_id="form_1098",
                person="self",
            )
        }
        assert filled["itemized_mortgage_interest_second"].raw_value == "15000"
        assert "mortgage_payoff_year" not in filled
        assert "mortgage_second_payoff_year" not in filled
        return "second-loan year-to-date interest stays off; a 1098 does not invent a payment"

    _run_check(
        report,
        "Core",
        "Second-loan year-to-date interest is not applied by default",
        check_second_loan_ytd_interest_and_unmatched_1098,
    )

    def check_property_tax_list_follows_edits_and_deletes():
        from app.core.document_ingestion.ingestion_controller import apply_document_import
        from app.core.lazy_mode.document_library import remove_entry
        from app.core.lazy_mode.orchestrator import _stack_property_tax_if_needed
        from app.core.planning_snapshot import PlanningSnapshot
        from config.model_fields import FIELDS_BY_KEY

        @dataclass
        class Suggestion:
            field_key: str
            raw_value: str
            source_quote: str = ""
            display_value: str = ""

        edited = PlanningSnapshot()
        edited.set_raw(FIELDS_BY_KEY["property_tax_annual"], "9000", person="self")
        edited.set_source("self", "property_tax_annual", "Property tax bill import")
        edited.persons.setdefault("self", {})["_property_tax_bills"] = "8000,3000"
        refreshed = _stack_property_tax_if_needed(
            edited,
            [Suggestion("property_tax_annual", "8200", "county refresh")],
            document_id="property_tax_bill",
            person="self",
        )
        assert refreshed[0].raw_value == "8200"

        edited.set_raw(FIELDS_BY_KEY["property_tax_annual"], "15000", person="self")
        added = _stack_property_tax_if_needed(
            edited,
            [Suggestion("property_tax_annual", "4000", "other parcel")],
            document_id="property_tax_bill",
            person="self",
        )
        assert added[0].raw_value == "19000"

        def two_bills():
            snapshot = PlanningSnapshot()
            first = apply_document_import(
                snapshot,
                [
                    {
                        "apply": True,
                        "field_key": "property_tax_annual",
                        "raw_value": "8000",
                        "person": "self",
                        "source": "Imported from a.pdf",
                        "source_quote": "Property tax bill — total / annual amount",
                    }
                ],
                document_type="property_tax_bill",
                document_label="a.pdf",
            )
            second = apply_document_import(
                snapshot,
                [
                    {
                        "apply": True,
                        "field_key": "property_tax_annual",
                        "raw_value": "3000",
                        "person": "self",
                        "source": "Imported from b.pdf",
                        "source_quote": "Property tax bill — total / annual amount",
                    }
                ],
                document_type="property_tax_bill",
                document_label="b.pdf",
            )
            assert snapshot.get_raw(FIELDS_BY_KEY["property_tax_annual"], person="self") == "11000"
            assert snapshot.persons["self"]["_property_tax_bills"] == "8000,3000"
            assert first["changes"][0]["bill_component"] == "8000"
            assert second["changes"][0]["bill_component"] == "3000"
            snapshot.document_library = [
                {
                    "id": "bill-a",
                    "document_id": "property_tax_bill",
                    "person": "self",
                    "content_hash": "a",
                    "status": "applied",
                    "original_name": "a.pdf",
                    "stored_path": "",
                    "applied_revert": first["changes"],
                },
                {
                    "id": "bill-b",
                    "document_id": "property_tax_bill",
                    "person": "self",
                    "content_hash": "b",
                    "status": "applied",
                    "original_name": "b.pdf",
                    "stored_path": "",
                    "applied_revert": second["changes"],
                },
            ]
            return snapshot

        without_first = two_bills()
        removed_first = remove_entry(without_first, "bill-a")
        assert removed_first["reverted"] == 1
        assert without_first.get_raw(FIELDS_BY_KEY["property_tax_annual"], person="self") == "3000"
        assert without_first.persons["self"]["_property_tax_bills"] == "3000"

        without_second = two_bills()
        removed_second = remove_entry(without_second, "bill-b")
        assert removed_second["reverted"] == 1
        assert without_second.get_raw(FIELDS_BY_KEY["property_tax_annual"], person="self") == "8000"
        assert without_second.persons["self"]["_property_tax_bills"] == "8000"

        both_orders = two_bills()
        assert remove_entry(both_orders, "bill-a")["reverted"] == 1
        assert remove_entry(both_orders, "bill-b")["reverted"] == 1
        assert both_orders.get_raw(FIELDS_BY_KEY["property_tax_annual"], person="self") == "0"
        assert both_orders.persons["self"].get("_property_tax_bills", "") == ""
        return "an edited property-tax total is not rebuilt from a stale bill list"

    _run_check(
        report,
        "Core",
        "Property-tax bill list follows edits and deletes",
        check_property_tax_list_follows_edits_and_deletes,
    )

    def check_library_delete_stays_in_folder():
        from app.core.lazy_mode.document_library import stored_path_in_library

        assert stored_path_in_library("/etc/passwd") is False
        assert stored_path_in_library("") is False
        return "paths outside the document library are not deleted"

    _run_check(
        report,
        "Core",
        "Document library deletes stay in the library folder",
        check_library_delete_stays_in_folder,
    )

    def check_second_loan_and_payoff_month():
        from model.expenses import person_mortgage_annual

        person = {
            "mortgage_annual": 24_000,
            "mortgage_payoff_year": 2030,
            "mortgage_payoff_month": 6,
            "mortgage_second_annual": 12_000,
            "mortgage_second_payoff_year": 2040,
            "mortgage_second_payoff_month": 3,
        }
        assert abs(person_mortgage_annual(person, year=2029) - 36_000) < 1
        june = person_mortgage_annual(person, year=2030)
        assert abs(june - (24_000 * 6 / 12 + 12_000)) < 1, june
        later = person_mortgage_annual(person, year=2035)
        assert abs(later - 12_000) < 1, later
        assert person_mortgage_annual(person, year=2041) == 0
        return "each mortgage ends on its own date; the payoff year is not 12 months"

    _run_check(
        report,
        "Core",
        "Second mortgage keeps its own payoff month",
        check_second_loan_and_payoff_month,
    )

    def check_standard_mode_uses_itemized_amounts():
        from model.cpa_tax import ItemizedInputs
        from model.taxes import compute_retirement_tax_detail, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        plain = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=0,
            withdrawal_gross=200_000,
            roth_fraction=0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0,
            tax_mode="federal_brackets",
            flat_rate=0,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=0,
            taxable_gain_fraction=0,
            planning_precision="standard",
            itemized=ItemizedInputs(),
            tax_year=2026,
        )
        itemized = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=0,
            withdrawal_gross=200_000,
            roth_fraction=0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0,
            tax_mode="federal_brackets",
            flat_rate=0,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=0,
            taxable_gain_fraction=0,
            planning_precision="standard",
            itemized=ItemizedInputs(mortgage_interest=80_000),
            tax_year=2026,
        )
        assert itemized.federal < plain.federal
        assert itemized.deduction_method == "itemized"
        return (
            f"standard mode itemizes ${plain.federal:,.0f} → "
            f"${itemized.federal:,.0f}"
        )

    _run_check(
        report,
        "Core",
        "Standard mode uses entered itemized amounts",
        check_standard_mode_uses_itemized_amounts,
    )

    def check_delete_restores_applied_import():
        from app.core.lazy_mode.document_library import find_duplicate, remove_entry
        from app.core.planning_snapshot import PlanningSnapshot
        from config.model_fields import FIELDS_BY_KEY

        snapshot = PlanningSnapshot()
        field = FIELDS_BY_KEY["mortgage_annual"]
        snapshot.set_raw(field, "10000", person="self")
        snapshot.document_library.append(
            {
                "id": "doc-1",
                "document_id": "mortgage_statement",
                "person": "self",
                "content_hash": "abc",
                "status": "applied",
                "original_name": "statement.pdf",
                "stored_path": "",
                "applied_revert": [
                    {
                        "field_key": "mortgage_annual",
                        "person": "self",
                        "previous": "10000",
                        "previous_source": "Manual",
                        "applied": "24000",
                    }
                ],
            }
        )
        snapshot.set_raw(field, "24000", person="self")
        same_bytes = find_duplicate(
            snapshot,
            document_id="form_1098",
            content_hash="abc",
            person="self",
        )
        assert same_bytes is not None
        result = remove_entry(snapshot, "doc-1")
        assert result["reverted"] == 1
        assert snapshot.get_raw(field, person="self") == "10000"
        return "deleting an applied file restores the previous payment"

    _run_check(
        report,
        "Core",
        "Deleting an import restores the fields it filled",
        check_delete_restores_applied_import,
    )

    def check_delete_after_refresh_restores_original():
        from app.core.lazy_mode.document_library import merge_applied_revert, remove_entry
        from app.core.planning_snapshot import PlanningSnapshot
        from config.model_fields import FIELDS_BY_KEY

        merged = merge_applied_revert(
            [
                {
                    "field_key": "mortgage_annual",
                    "person": "self",
                    "previous": "10000",
                    "previous_source": "Manual",
                    "applied": "24000",
                }
            ],
            [
                {
                    "field_key": "mortgage_annual",
                    "person": "self",
                    "previous": "24000",
                    "previous_source": "Imported from statement.pdf",
                    "applied": "25000",
                }
            ],
        )
        assert merged[0]["previous"] == "10000"
        assert merged[0]["applied"] == "25000"
        snapshot = PlanningSnapshot()
        field = FIELDS_BY_KEY["mortgage_annual"]
        snapshot.set_raw(field, "25000", person="self")
        snapshot.document_library.append(
            {
                "id": "doc-1",
                "document_id": "mortgage_statement",
                "person": "self",
                "content_hash": "abc",
                "status": "applied",
                "original_name": "statement.pdf",
                "stored_path": "",
                "applied_revert": merged,
            }
        )
        result = remove_entry(snapshot, "doc-1")
        assert result["reverted"] == 1
        assert snapshot.get_raw(field, person="self") == "10000"
        return "refresh keeps the pre-import value for delete"

    _run_check(
        report,
        "Core",
        "Delete after a refresh restores the original value",
        check_delete_after_refresh_restores_original,
    )

    def check_survivor_benefit_starts_at_death():
        from model import run_retirement_projection

        def project(self_age: int):
            params = _sample_params()
            params["inflation_rate"] = 0.0
            params["investment_return_mean"] = 0.0
            params["state_tax_enabled"] = False
            params["tax_calculation"] = "flat"
            params["post_retire_tax_rate"] = 0.0
            params["medicare_enabled"] = False
            self_p = params["persons"]["self"]
            wife_p = params["persons"]["wife"]
            self_p.update(
                current_age=self_age,
                retirement_age=65,
                life_expectancy=90,
                base_expenses=40_000,
                ss_amount=20_000,
                ss_start_age=67,
                pension_amount=0,
                rental_income_start=0,
                annual_contributions=0,
                health_insurance_post_retire=0,
                health_insurance_pre_medicare=0,
                long_term_care_annual=0,
            )
            wife_p.update(
                current_age=68,
                retirement_age=65,
                life_expectancy=67,
                base_expenses=0,
                ss_amount=40_000,
                ss_start_age=70,
                pension_amount=0,
                rental_income_start=0,
                annual_contributions=0,
                health_insurance_post_retire=0,
                health_insurance_pre_medicare=0,
                long_term_care_annual=0,
            )
            return run_retirement_projection(params)["joint"].iloc[0]

        eligible = project(61)
        too_young = project(58)
        assert abs(eligible["SS"] - 40_000) < 1, eligible["SS"]
        assert abs(too_young["SS"]) < 1, too_young["SS"]
        return f"survivor SS ${eligible['SS']:,.0f} at 61, ${too_young['SS']:,.0f} at 58"

    _run_check(
        report,
        "Core",
        "Survivor benefit starts at death with an age floor",
        check_survivor_benefit_starts_at_death,
    )

    def check_deceased_pension_stops():
        from model import run_retirement_projection

        params = _sample_params()
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["state_tax_enabled"] = False
        params["tax_calculation"] = "flat"
        params["post_retire_tax_rate"] = 0.0
        params["medicare_enabled"] = False
        self_p = params["persons"]["self"]
        wife_p = params["persons"]["wife"]
        self_p.update(
            current_age=87,
            retirement_age=65,
            life_expectancy=90,
            base_expenses=40_000,
            ss_amount=0,
            pension_amount=0,
            rental_income_start=0,
            annual_contributions=0,
            health_insurance_post_retire=0,
            health_insurance_pre_medicare=0,
            long_term_care_annual=0,
        )
        wife_p.update(
            current_age=87,
            retirement_age=65,
            life_expectancy=86,
            base_expenses=0,
            ss_amount=0,
            pension_amount=25_000,
            pension_start_age=65,
            rental_income_start=0,
            annual_contributions=0,
            health_insurance_post_retire=0,
            health_insurance_pre_medicare=0,
            long_term_care_annual=0,
        )
        row = run_retirement_projection(params)["joint"].iloc[0]
        assert abs(row["Pension"]) < 1, row["Pension"]
        assert row["Withdrawals_Gross"] > 35_000, row["Withdrawals_Gross"]
        return (
            f"deceased pension ${row['Pension']:,.0f}, "
            f"withdrawal ${row['Withdrawals_Gross']:,.0f}"
        )

    _run_check(
        report,
        "Core",
        "A deceased spouse's pension stops",
        check_deceased_pension_stops,
    )

    def check_paycheck_withholding_is_not_spendable():
        from model import run_retirement_projection

        params = _sample_params()
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["state_tax_enabled"] = False
        params["tax_calculation"] = "flat"
        params["post_retire_tax_rate"] = 0.0
        params["pre_retire_tax_rate"] = 0.0
        params["medicare_enabled"] = False
        params["working_year_tax_enabled"] = True
        params["contribution_roth_pct"] = 0.0
        self_p = params["persons"]["self"]
        wife_p = params["persons"]["wife"]
        self_p.update(
            current_age=70,
            retirement_age=65,
            life_expectancy=90,
            base_expenses=90_000,
            ss_amount=0,
            pension_amount=0,
            rental_income_start=0,
            annual_contributions=0,
            annual_earned_income=0,
            health_insurance_post_retire=0,
            health_insurance_pre_medicare=0,
            long_term_care_annual=0,
        )
        wife_p.update(
            current_age=55,
            retirement_age=65,
            life_expectancy=90,
            base_expenses=0,
            ss_amount=0,
            pension_amount=0,
            rental_income_start=0,
            annual_contributions=20_000,
            annual_earned_income=100_000,
            health_insurance_post_retire=0,
            health_insurance_pre_medicare=0,
            long_term_care_annual=0,
        )
        row = run_retirement_projection(params)["joint"].iloc[0]
        # $100k wages - $7,650 FICA - $20k contribution leaves $72,350 of a $90k budget.
        assert 15_000 < row["Withdrawals_Gross"] < 20_000, row["Withdrawals_Gross"]
        return f"partial-retirement withdrawal ${row['Withdrawals_Gross']:,.0f}"

    _run_check(
        report,
        "Core",
        "FICA and contributions are not spendable wages",
        check_paycheck_withholding_is_not_spendable,
    )

    # --- Phase 1: Security + data layer ---
    def check_vault_roundtrip():
        from security.vault import Vault

        with tempfile.TemporaryDirectory() as tmp:
            vault = Vault(data_dir=Path(tmp))
            payload = {"version": 1, "test": True, "balances": {"self": 1000}}
            vault.save(payload)
            assert vault.load() == payload
            vault.clear()
            assert not vault.exists()
        return "encrypt/decrypt/clear OK"

    _run_check(report, "Phase 1", "Vault encrypt roundtrip", check_vault_roundtrip)

    def check_keychain_module():
        from security.keychain import list_item_ids

        ids = list_item_ids()
        assert isinstance(ids, list)
        return f"{len(ids)} Plaid item(s) in Keychain"

    _run_check(report, "Phase 1", "Keychain module", check_keychain_module)

    def check_data_service_aggregation():
        from services.data_service import DataService, MappedAccount

        service = DataService()
        accounts = [
            MappedAccount(
                account_id="a1",
                name="Checking",
                institution="Bank",
                type="depository",
                subtype="checking",
                mask="1234",
                balance=100_000,
                currency="USD",
                balance_source="accounts_balance_get",
                owner="self",
                include_in_assets=True,
            ),
            MappedAccount(
                account_id="a2",
                name="Brokerage",
                institution="Broker",
                type="investment",
                subtype="brokerage",
                mask="5678",
                balance=250_000,
                currency="USD",
                balance_source="investments_holdings_get",
                owner="wife",
                include_in_assets=True,
            ),
        ]
        totals = service.aggregate_owner_totals(accounts)
        assert totals == {"self": 100_000.0, "wife": 250_000.0}
        return "owner aggregation OK"

    _run_check(report, "Phase 1", "DataService aggregation", check_data_service_aggregation)

    def check_account_mapping_snapshot():
        import json
        from pathlib import Path

        from services.data_service import AccountMapping, AccountsConfig, DataService, MappedAccount
        from security.vault import Vault

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            accounts_file = tmp_path / "accounts.json"
            accounts_file.write_text(
                json.dumps(
                    {
                        "institutions": [],
                        "account_mappings": [
                            {
                                "account_id": "acct_1",
                                "institution": "Bank",
                                "name": "Checking",
                                "owner": "unassigned",
                                "include_in_assets": True,
                                "item_id": "item_1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            vault = Vault(data_dir=tmp_path)
            vault.save(
                {
                    "balances": {
                        "version": 1,
                        "as_of": "2026-06-22T00:00:00+00:00",
                        "accounts": [
                            {
                                "account_id": "acct_1",
                                "name": "Checking",
                                "institution": "Bank",
                                "type": "depository",
                                "subtype": "checking",
                                "mask": "1234",
                                "balance": 42_000.0,
                                "currency": "USD",
                                "balance_source": "accounts_balance_get",
                                "owner": "unassigned",
                                "include_in_assets": True,
                                "item_id": "item_1",
                            }
                        ],
                        "owner_totals": {},
                        "items_refreshed": ["item_1"],
                        "errors": [],
                    }
                }
            )
            service = DataService(vault=vault, accounts_file=accounts_file)
            snapshot = service.snapshot_for_account_mapping()
            assert len(snapshot.accounts) == 1
            assert snapshot.accounts[0].balance == 42_000.0
            updated = service.apply_account_mapping_review([("acct_1", "self", True)])
            assert updated.owner_totals["self"] == 42_000.0
            config = service.load_accounts_config()
            assert config.account_mappings[0].owner == "self"
        return "mapping snapshot without Plaid OK"

    _run_check(report, "Phase 1", "Account mapping snapshot", check_account_mapping_snapshot)

    def check_plaid_configured():
        from settings import get_settings

        s = get_settings()
        if not s.plaid_configured:
            raise RuntimeError("SKIP")
        return f"Plaid env={s.plaid_env}"

    try:
        _run_check(report, "Phase 1", "Plaid configuration", check_plaid_configured)
    except Exception:
        pass
    if report.results[-1].status == "fail" and "SKIP" in report.results[-1].detail:
        report.results[-1].status = "skip"
        report.results[-1].detail = "Plaid keys not in .env (manual mode still works)"

    # --- Phase 2: Model ---
    def check_deterministic_projection():
        from model import run_retirement_projection

        results = run_retirement_projection(_sample_params())
        joint = results["joint"]
        assert len(joint) == 58  # life_expectancy 95 - min_age 38 + 1
        assert joint["Assets_End"].iloc[-1] > 0
        assert "Health_Insurance_Post-Retire" in joint.columns or "Expenses" in joint.columns
        return f"{len(joint)} rows, ending assets ${joint['Assets_End'].iloc[-1]:,.0f}"

    _run_check(report, "Phase 2", "Deterministic projection", check_deterministic_projection)

    def check_joint_combine_rules():
        from config.field_values import combine_person_values
        from config.model_fields import FIELDS_BY_KEY

        age_field = FIELDS_BY_KEY["current_age"]
        assert combine_person_values(age_field, [40, 38]) == 38
        contrib_field = FIELDS_BY_KEY["annual_contributions"]
        assert combine_person_values(contrib_field, [20_000, 15_000]) == 35_000
        return "combine rules OK"

    _run_check(report, "Phase 2", "Joint field combine rules", check_joint_combine_rules)

    def check_retirement_expense_additives():
        from config.model_fields import RETIREMENT_EXPENSE_ADDITIVE_KEYS

        assert "health_insurance_pre_medicare" in RETIREMENT_EXPENSE_ADDITIVE_KEYS
        assert "health_insurance_post_retire" in RETIREMENT_EXPENSE_ADDITIVE_KEYS
        assert "long_term_care_annual" in RETIREMENT_EXPENSE_ADDITIVE_KEYS
        return f"{len(RETIREMENT_EXPENSE_ADDITIVE_KEYS)} additive expense keys"

    _run_check(report, "Phase 2", "Retirement expense catalog", check_retirement_expense_additives)

    def check_pretax_contributions():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["pre_retire_tax_rate"] = 0.35
        params["persons"]["self"]["annual_contributions"] = 10_000
        result = run_retirement_projection(params)["self"]
        first = result.iloc[0]
        assert first["Contributions"] == 10_000, "pre-tax contributions should enter at full face value"
        return "pre-tax contributions not reduced at deposit"

    _run_check(report, "Phase 2", "Pre-tax contribution deposit", check_pretax_contributions)

    def check_joint_staggered_retirement():
        from model import run_retirement_projection

        params = _sample_params()
        params["inflation_rate"] = 0.0
        params["persons"]["self"]["retirement_age"] = 63
        params["persons"]["wife"]["retirement_age"] = 65
        params["persons"]["self"]["annual_contributions"] = 10_000
        params["persons"]["wife"]["annual_contributions"] = 5_000
        df = run_retirement_projection(params)["joint"]
        # Anchor age 38 + 25 = 63: self retired, wife still working
        row_63 = df[df["Age"] == 63].iloc[0]
        assert row_63["Contributions"] == 5_000
        assert row_63["Phase"] == "partial_retirement"
        assert row_63["Expenses"] > 0, "retired spouse expenses should draw in partial retirement"
        row_65 = df[df["Age"] == 65].iloc[0]
        assert row_65["Contributions"] == 0.0
        assert row_65["Phase"] == "full_retirement"
        return "partial retirement expenses + staggered contributions OK"

    _run_check(report, "Phase 2", "Joint staggered retirement", check_joint_staggered_retirement)

    def check_ss_cola():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.03
        params["persons"]["self"]["retirement_age"] = 60
        params["persons"]["self"]["ss_start_age"] = 62
        params["persons"]["self"]["ss_amount"] = 20_000
        params["life_expectancy"] = 70
        df = run_retirement_projection(params)["self"]
        ss_62 = df[df["Age"] == 62].iloc[0]["SS"]
        ss_64 = df[df["Age"] == 64].iloc[0]["SS"]
        assert ss_64 > ss_62, "SS should grow with inflation after start"
        return f"SS COLA: ${ss_62:,.0f} → ${ss_64:,.0f}"

    _run_check(report, "Phase 2", "Social Security COLA", check_ss_cola)

    def check_pension_cola():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.03
        params["persons"]["self"]["retirement_age"] = 60
        params["persons"]["self"]["pension_start_age"] = 62
        params["persons"]["self"]["pension_amount"] = 30_000
        params["life_expectancy"] = 70
        df = run_retirement_projection(params)["self"]
        p62 = df[df["Age"] == 62].iloc[0]["Pension"]
        p64 = df[df["Age"] == 64].iloc[0]["Pension"]
        assert p64 > p62
        return f"pension COLA: ${p62:,.0f} → ${p64:,.0f}"

    _run_check(report, "Phase 2", "Pension COLA", check_pension_cola)

    def check_roth_reduces_withdrawal_tax():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "separate"
        base["persons"]["self"]["retirement_age"] = 60
        base["life_expectancy"] = 75
        base["roth_portfolio_pct"] = 0.0
        low_roth = run_retirement_projection(base)["self"]
        base["roth_portfolio_pct"] = 0.80
        high_roth = run_retirement_projection(base)["self"]
        tax_low = low_roth[low_roth["Phase"] == "full_retirement"]["Taxes"].mean()
        tax_high = high_roth[high_roth["Phase"] == "full_retirement"]["Taxes"].mean()
        assert tax_high < tax_low
        return f"avg taxes full retirement: {tax_low:,.0f} → {tax_high:,.0f} with 80% Roth"

    _run_check(report, "Phase 2", "Roth tax treatment", check_roth_reduces_withdrawal_tax)

    def check_model_engine_unit():
        from model.engine import split_contributions, after_tax_withdrawal_factor

        deposited, trad, roth = split_contributions(10_000, roth_pct=0.25, pre_tax_rate=0.35)
        assert deposited == 9_125
        assert trad == 7_500
        assert roth == 1_625
        assert abs(after_tax_withdrawal_factor(0.5, 0.20) - 0.9) < 0.001
        return "engine tax math OK"

    _run_check(report, "Phase 2", "Model engine unit tests", check_model_engine_unit)

    def check_rmd_floor():
        from model import run_retirement_projection
        from model.rmd import compute_rmd, uniform_lifetime_divisor

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["roth_portfolio_pct"] = 0.0
        params["persons"]["self"]["current_age"] = 72
        params["persons"]["self"]["retirement_age"] = 80
        params["persons"]["self"]["initial_assets"] = 1_000_000
        params["persons"]["self"]["annual_contributions"] = 0
        params["life_expectancy"] = 75
        df = run_retirement_projection(params)["self"]
        row_73 = df[df["Age"] == 73].iloc[0]
        trad_start = row_73["Assets_Start"]
        if params["roth_portfolio_pct"] > 0:
            trad_start = row_73["Assets_Start"] * (1.0 - params["roth_portfolio_pct"])
        expected = compute_rmd(trad_start, 73, rmd_start_age=73)
        assert abs(row_73["RMD"] - expected) < 1.0
        assert row_73["Withdrawals_Gross"] >= row_73["RMD"]
        return f"age-73 RMD ${row_73['RMD']:,.0f} (divisor {uniform_lifetime_divisor(73)})"

    _run_check(report, "Phase 2", "RMD floor enforcement", check_rmd_floor)

    def check_federal_bracket_taxes():
        from model.taxes import compute_retirement_tax, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj")
        bracket_tax = compute_retirement_tax(
            ss_gross=30_000,
            pension_gross=20_000,
            rental_gross=0,
            withdrawal_gross=50_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.50,
            tax_mode="federal_brackets",
            flat_rate=0.22,
            table=table,
        )
        flat_tax = compute_retirement_tax(
            ss_gross=30_000,
            pension_gross=20_000,
            rental_gross=0,
            withdrawal_gross=50_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.50,
            tax_mode="flat",
            flat_rate=0.22,
            table=table,
        )
        assert bracket_tax < flat_tax, "progressive brackets should beat flat 22% on moderate income"
        return f"bracket ${bracket_tax:,.0f} vs flat ${flat_tax:,.0f}"

    _run_check(report, "Phase 2", "Federal bracket taxes", check_federal_bracket_taxes)

    def check_ss_provisional_tiers():
        from model.taxes import taxable_social_security_irs

        # MFJ: combined = 10k other + 15k (50% of 30k SS) = 25k <= 32k → none taxable
        assert taxable_social_security_irs(30_000, other_income=10_000, filing_status="mfj") == 0.0

        # MFJ: combined = 30k + 15k = 45k → third tier
        high = taxable_social_security_irs(30_000, other_income=30_000, filing_status="mfj")
        assert 6_000 < high < 30_000 * 0.85

        # MFJ middle tier: combined = 32k + 5k = 37k
        mid = taxable_social_security_irs(30_000, other_income=22_000, filing_status="mfj")
        assert 0 < mid < 15_000

        # Single base threshold
        assert taxable_social_security_irs(20_000, other_income=15_000, filing_status="single") == 0.0

        return f"tiers: low=$0 mid=${mid:,.0f} high=${high:,.0f}"

    _run_check(report, "Phase 2", "SS provisional income tiers", check_ss_provisional_tiers)

    def check_head_of_household_brackets():
        from model.taxes import compute_retirement_tax, tax_table_for_filing_status

        table_hoh = tax_table_for_filing_status("head_of_household")
        table_single = tax_table_for_filing_status("single")
        assert table_hoh.standard_deduction > table_single.standard_deduction

        hoh_tax = compute_retirement_tax(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            withdrawal_gross=80_000,
            roth_fraction=0.0,
            filing_status="head_of_household",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.0,
            tax_mode="federal_brackets",
            flat_rate=0.22,
            table=table_hoh,
        )
        single_tax = compute_retirement_tax(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            withdrawal_gross=80_000,
            roth_fraction=0.0,
            filing_status="single",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.0,
            tax_mode="federal_brackets",
            flat_rate=0.22,
            table=table_single,
        )
        assert hoh_tax < single_tax
        return f"HOH ${hoh_tax:,.0f} vs single ${single_tax:,.0f} on $80k withdrawal"

    _run_check(report, "Phase 2", "Head of household brackets", check_head_of_household_brackets)

    def check_ss_irs_vs_flat_in_projection():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "separate"
        base["tax_calculation"] = "flat"
        base["post_retire_tax_rate"] = 0.22
        base["persons"]["self"]["retirement_age"] = 67
        base["persons"]["self"]["ss_start_age"] = 67
        base["persons"]["self"]["ss_amount"] = 30_000
        base["life_expectancy"] = 75
        irs = run_retirement_projection({**base, "ss_tax_method": "irs_provisional"})["self"]
        flat = run_retirement_projection(
            {**base, "ss_tax_method": "flat_fraction", "ss_taxable_fraction": 0.50}
        )["self"]
        irs_row = irs[irs["Age"] == 70].iloc[0]
        flat_row = flat[flat["Age"] == 70].iloc[0]
        assert irs_row["SS_Taxable"] != flat_row["SS_Taxable"]
        return (
            f"age 70 SS taxable IRS=${irs_row['SS_Taxable']:,.0f} "
            f"flat=${flat_row['SS_Taxable']:,.0f}"
        )

    _run_check(report, "Phase 2", "SS IRS vs flat in projection", check_ss_irs_vs_flat_in_projection)

    def check_earned_income_accumulation():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["tax_calculation"] = "flat"
        params["persons"]["self"]["annual_earned_income"] = 180_000
        params["persons"]["self"]["retirement_age"] = 65
        params["life_expectancy"] = 66
        df = run_retirement_projection(params)["self"]
        accum = df[df["Phase"] == "accumulation"]
        assert not accum.empty
        assert (accum["Withdrawals_Gross"] == 0).all()
        assert accum["Earned_Income"].iloc[0] == 180_000
        assert accum["Taxes"].iloc[0] > 0
        return "working years funded by salary, no portfolio draw"

    _run_check(report, "Phase 2", "Earned income covers working years", check_earned_income_accumulation)

    def check_bracket_mode_in_projection():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "separate"
        base["persons"]["self"]["retirement_age"] = 60
        base["life_expectancy"] = 75
        flat = run_retirement_projection({**base, "tax_calculation": "flat"})["self"]
        brackets = run_retirement_projection({**base, "tax_calculation": "federal_brackets"})["self"]
        flat_tax = flat[flat["Phase"] == "full_retirement"]["Taxes"].sum()
        bracket_tax = brackets[brackets["Phase"] == "full_retirement"]["Taxes"].sum()
        assert bracket_tax != flat_tax
        assert brackets["Tax_Mode"].iloc[0] == "federal_brackets"
        return f"full-retirement taxes flat=${flat_tax:,.0f} brackets=${bracket_tax:,.0f}"

    _run_check(report, "Phase 2", "Bracket mode in projection", check_bracket_mode_in_projection)

    def check_marriage_year_filing_transition():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "joint"
        params["tax_calculation"] = "flat"
        params["marriage_year"] = 2035
        params["filing_status_before_marriage"] = "head_of_household"
        params["filing_status_after_marriage"] = "mfj"
        params["current_year"] = 2026
        params["persons"]["self"]["retirement_age"] = 70
        params["persons"]["wife"]["retirement_age"] = 70
        params["life_expectancy"] = 80
        df = run_retirement_projection(params)["joint"]
        before = df[df["Year"] == 2034].iloc[0]
        after = df[df["Year"] == 2036].iloc[0]
        assert before["Filing_Layout"] == "separate_returns"
        assert "head_of_household" in before["Filing_Status"]
        assert after["Filing_Layout"] == "joint_return"
        assert after["Filing_Status"] == "mfj"
        return f"2034={before['Filing_Status']} → 2036={after['Filing_Status']}"

    _run_check(report, "Phase 2", "Marriage year filing transition", check_marriage_year_filing_transition)

    def check_marriage_changes_retirement_taxes():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "joint"
        base["tax_calculation"] = "federal_brackets"
        base["marriage_year"] = 2030
        base["filing_status_before_marriage"] = "head_of_household"
        base["filing_status_after_marriage"] = "mfj"
        base["persons"]["self"]["current_age"] = 58
        base["persons"]["wife"]["current_age"] = 56
        base["persons"]["self"]["retirement_age"] = 60
        base["persons"]["wife"]["retirement_age"] = 60
        base["marriage_year"] = 2032
        base["life_expectancy"] = 75
        df = run_retirement_projection(base)["joint"]
        retired = df[df["Phase"] == "full_retirement"]
        before = retired[retired["Year"] < 2032]
        after = retired[retired["Year"] >= 2032]
        assert not before.empty and not after.empty
        assert before["Filing_Layout"].iloc[0] == "separate_returns"
        assert after["Filing_Layout"].iloc[0] == "joint_return"
        return "retirement taxes use separate HOH then joint MFJ"

    _run_check(report, "Phase 2", "Marriage year tax layout", check_marriage_changes_retirement_taxes)

    def check_california_state_tax():
        from model.state_taxes import compute_state_income_tax, state_profile

        profile = state_profile("CA")
        tax = compute_state_income_tax(
            120_000,
            ss_taxable=10_000,
            profile=profile,
            filing_status="mfj",
            inflation_factor=1.0,
        )
        assert tax > 3_000
        assert compute_state_income_tax(
            120_000,
            ss_taxable=10_000,
            profile=state_profile("TX"),
            filing_status="mfj",
            inflation_factor=1.0,
        ) == 0.0
        return f"CA tax ${tax:,.0f} on $120k taxable; TX $0"

    _run_check(report, "Phase 2", "California state tax", check_california_state_tax)

    def check_state_relocation_in_projection():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "joint"
        base["tax_calculation"] = "federal_brackets"
        base["persons"]["self"]["current_age"] = 58
        base["persons"]["wife"]["current_age"] = 56
        base["persons"]["self"]["retirement_age"] = 60
        base["persons"]["wife"]["retirement_age"] = 60
        base["life_expectancy"] = 75
        ca = run_retirement_projection(
            {
                **base,
                "state_of_residence": "CA",
                "state_move_year": 2099,
            }
        )["joint"]
        nv = run_retirement_projection(
            {
                **base,
                "state_of_residence": "CA",
                "state_move_year": 2035,
                "state_after_move": "NV",
            }
        )["joint"]
        ca_retired = ca[ca["Phase"] == "full_retirement"]
        nv_before = nv[(nv["Phase"] == "full_retirement") & (nv["Year"] < 2035)]
        nv_after = nv[(nv["Phase"] == "full_retirement") & (nv["Year"] > 2035)]
        assert ca_retired["State_Taxes"].sum() > 0
        assert nv_before["Tax_State"].iloc[0] == "CA"
        assert set(nv_after["Tax_State"]) == {"NV"}
        assert nv_after["State_Taxes"].sum() == 0.0
        return (
            f"CA state taxes ${ca_retired['State_Taxes'].sum():,.0f} vs "
            f"NV after move ${nv_after['State_Taxes'].sum():,.0f}"
        )

    _run_check(report, "Phase 2", "State relocation projection", check_state_relocation_in_projection)

    def check_medicare_premiums_at_65():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["tax_calculation"] = "flat"
        params["persons"]["self"]["current_age"] = 64
        params["persons"]["self"]["retirement_age"] = 64
        params["life_expectancy"] = 67
        df = run_retirement_projection(params)["self"]
        at_64 = df[df["Age"] == 64].iloc[0]
        at_65 = df[df["Age"] == 65].iloc[0]
        assert at_64["Medicare"] == 0.0
        assert at_65["Medicare"] > 2_000
        return f"age 65 Medicare ${at_65['Medicare']:,.0f}/yr"

    _run_check(report, "Phase 2", "Medicare premiums at 65", check_medicare_premiums_at_65)

    def check_medicare_irmaa_high_income():
        from model.medicare import compute_medicare_cost, medicare_policy_from_params

        policy = medicare_policy_from_params({"medicare_enabled": True})
        low = compute_medicare_cost(
            ages={"self": 67},
            policy=policy,
            magi=80_000,
            filing_status="mfj",
            inflation_factor=1.0,
        )
        high = compute_medicare_cost(
            ages={"self": 67},
            policy=policy,
            magi=300_000,
            filing_status="mfj",
            inflation_factor=1.0,
        )
        assert high.irmaa > low.irmaa
        assert high.total > low.total
        return f"IRMAA low=${low.irmaa:,.0f} high=${high.irmaa:,.0f}"

    _run_check(report, "Phase 2", "Medicare IRMAA tiers", check_medicare_irmaa_high_income)

    def check_medicare_disabled():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["persons"]["self"]["current_age"] = 67
        params["persons"]["self"]["retirement_age"] = 65
        params["life_expectancy"] = 70
        params["medicare_enabled"] = False
        df = run_retirement_projection(params)["self"]
        assert (df["Medicare"] == 0).all()
        return "medicare_enabled=False yields zero premiums"

    _run_check(report, "Phase 2", "Medicare disable flag", check_medicare_disabled)

    def check_early_retirement_medicare_gap():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["tax_calculation"] = "flat"
        params["inflation_rate"] = 0.0
        params["persons"]["self"]["current_age"] = 48
        params["persons"]["self"]["retirement_age"] = 52
        params["aca_premium_mode"] = "manual"
        params["persons"]["self"]["health_insurance_pre_medicare"] = 24_000
        params["persons"]["self"]["health_insurance_post_retire"] = 5_000
        params["life_expectancy"] = 68
        df = run_retirement_projection(params)["self"]
        gap = df[(df["Age"] >= 52) & (df["Age"] < 65)]
        medicare_years = df[df["Age"] >= 65]
        assert not gap.empty and not medicare_years.empty
        assert (gap["Medicare"] == 0).all()
        assert (gap["Medicare_Phase"] == "pre_medicare_gap").all()
        assert gap["Health_Pre-Medicare"].iloc[0] == 24_000
        assert gap["Health_Post-Medicare"].iloc[0] == 0.0
        assert medicare_years["Medicare"].iloc[0] > 2_000
        assert medicare_years["Health_Pre-Medicare"].iloc[0] == 0.0
        assert medicare_years["Health_Post-Medicare"].iloc[0] == 5_000
        return (
            f"ages 52–64: ACA ${gap['Health_Pre-Medicare'].iloc[0]:,.0f}, "
            f"65+: Medicare ${medicare_years['Medicare'].iloc[0]:,.0f}"
        )

    _run_check(report, "Phase 2", "Early retirement Medicare gap", check_early_retirement_medicare_gap)

    def check_aca_subsidy_low_magi():
        from model.aca import compute_aca_household_cost, aca_policy_from_params

        policy = aca_policy_from_params({"aca_premium_mode": "aca_subsidized"})
        low = compute_aca_household_cost(
            [55],
            magi=40_000,
            policy=policy,
            filing_status="mfj",
            household_size=2,
            inflation_factor=1.0,
        )
        high = compute_aca_household_cost(
            [55],
            magi=200_000,
            policy=policy,
            filing_status="mfj",
            household_size=2,
            inflation_factor=1.0,
        )
        assert low.subsidy > high.subsidy
        assert low.net < high.net
        assert low.net < low.gross
        return f"age 55 low-MAGI net=${low.net:,.0f} subsidy=${low.subsidy:,.0f}"

    _run_check(report, "Phase 2", "ACA subsidy low MAGI", check_aca_subsidy_low_magi)

    def check_aca_85pct_cap():
        from model.aca import compute_aca_household_cost, aca_policy_from_params

        policy = aca_policy_from_params({"aca_premium_mode": "aca_subsidized"})
        at_cap = compute_aca_household_cost(
            [64, 64],
            magi=200_000,
            policy=policy,
            filing_status="mfj",
            household_size=2,
            inflation_factor=1.0,
        )
        expected_contribution = 200_000 * 0.085
        assert at_cap.applicable_pct == 0.085
        assert at_cap.gross > expected_contribution
        assert abs(at_cap.net - expected_contribution) < 100
        return f"MFJ age 64×2 net=${at_cap.net:,.0f} (8.5% of $200k MAGI)"

    _run_check(report, "Phase 2", "ACA 8.5% income cap", check_aca_85pct_cap)

    def check_aca_age_rating():
        from model.aca import compute_aca_household_cost, aca_policy_from_params

        policy = aca_policy_from_params({"aca_premium_mode": "aca_subsidized"})
        age_55 = compute_aca_household_cost(
            [55],
            magi=80_000,
            policy=policy,
            filing_status="single",
            household_size=1,
            inflation_factor=1.0,
        )
        age_64 = compute_aca_household_cost(
            [64],
            magi=80_000,
            policy=policy,
            filing_status="single",
            household_size=1,
            inflation_factor=1.0,
        )
        unsub_55 = compute_aca_household_cost(
            [55],
            magi=500_000,
            policy=policy,
            filing_status="single",
            household_size=1,
            inflation_factor=1.0,
        )
        unsub_64 = compute_aca_household_cost(
            [64],
            magi=500_000,
            policy=policy,
            filing_status="single",
            household_size=1,
            inflation_factor=1.0,
        )
        assert age_64.gross > age_55.gross
        assert unsub_64.net > unsub_55.net
        assert unsub_64.subsidy == 0.0
        return f"age 64 gross=${age_64.gross:,.0f} vs age 55 ${age_55.gross:,.0f}"

    _run_check(report, "Phase 2", "ACA age rating factors", check_aca_age_rating)

    def check_aca_early_retire_projection():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["tax_calculation"] = "flat"
        params["inflation_rate"] = 0.0
        params["aca_premium_mode"] = "aca_subsidized"
        params["persons"]["self"]["current_age"] = 48
        params["persons"]["self"]["retirement_age"] = 52
        params["persons"]["self"]["base_expenses"] = 50_000
        params["persons"]["self"]["health_insurance_pre_medicare"] = 0
        params["life_expectancy"] = 56
        df = run_retirement_projection(params)["self"]
        gap = df[(df["Age"] >= 52) & (df["Age"] < 65)]
        assert not gap.empty
        row = gap.iloc[0]
        assert row["ACA_Gross"] > 10_000
        assert row["ACA_Net"] > 0
        assert row["ACA_Net"] < row["ACA_Gross"]
        assert row["Health_Pre-Medicare"] == row["ACA_Net"]
        return (
            f"age {int(row['Age'])}: ACA gross ${row['ACA_Gross']:,.0f}, "
            f"net ${row['ACA_Net']:,.0f}, subsidy ${row['ACA_Subsidy']:,.0f}"
        )

    _run_check(report, "Phase 2", "ACA early retire projection", check_aca_early_retire_projection)

    def check_ledger_legacy_split():
        from model.accounts import ledger_from_persons

        persons = {"self": {"initial_assets": 800_000}}
        ledger = ledger_from_persons(persons, roth_portfolio_pct=0.25, household_mode=True)
        balances = ledger.by_person["__household__"]
        assert balances.roth == 200_000
        assert balances.traditional == 600_000
        assert balances.taxable == 0.0
        return "legacy 25% Roth split OK"

    _run_check(report, "Phase 0", "Ledger legacy split", check_ledger_legacy_split)

    def check_ledger_explicit_buckets():
        from model.accounts import ledger_from_persons

        persons = {
            "self": {
                "initial_assets": 1_000_000,
                "initial_taxable_assets": 300_000,
                "initial_traditional_ira": 500_000,
                "initial_roth_ira": 200_000,
            },
        }
        ledger = ledger_from_persons(persons, roth_portfolio_pct=0.0, household_mode=False)
        balances = ledger.by_person["self"]
        assert balances.taxable == 300_000
        assert balances.traditional == 500_000
        assert balances.roth == 200_000
        return "explicit account buckets OK"

    _run_check(report, "Phase 0", "Ledger explicit buckets", check_ledger_explicit_buckets)

    def check_magi_worksheet_matches_legacy():
        from model.magi import build_magi_worksheet, magi_and_taxable_ss
        from model.taxes import gross_taxable_income

        legacy_magi, _ = gross_taxable_income(
            ss_gross=30_000,
            pension_gross=10_000,
            rental_gross=5_000,
            earned_income=0,
            withdrawal_gross=40_000,
            roth_fraction=0.2,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
        )
        worksheet = build_magi_worksheet(
            ss_gross=30_000,
            pension_gross=10_000,
            rental_gross=5_000,
            earned_income=0,
            withdrawal_gross=40_000,
            roth_fraction=0.2,
        )
        magi, _ = magi_and_taxable_ss(
            worksheet,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
        )
        assert abs(magi - legacy_magi) < 1.0
        return f"MAGI worksheet ${magi:,.0f} matches legacy"

    _run_check(report, "Phase 0", "MAGI worksheet parity", check_magi_worksheet_matches_legacy)

    def check_multi_account_projection_columns():
        from model import run_retirement_projection

        params = _sample_params()
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["persons"]["self"]["annual_contributions"] = 0
        params["persons"]["wife"]["annual_contributions"] = 0
        params["persons"]["self"]["initial_taxable_assets"] = 100_000
        params["persons"]["self"]["initial_traditional_ira"] = 400_000
        params["persons"]["self"]["initial_roth_ira"] = 0
        params["persons"]["wife"]["initial_taxable_assets"] = 50_000
        params["persons"]["wife"]["initial_traditional_ira"] = 250_000
        params["persons"]["wife"]["initial_roth_ira"] = 0
        df = run_retirement_projection(params)["joint"]
        row = df.iloc[0]
        assert row["Assets_Taxable"] == 150_000
        assert row["Assets_Traditional"] == 650_000
        assert row["Assets_Roth"] == 0.0
        return (
            f"Y1 taxable ${row['Assets_Taxable']:,.0f}, "
            f"traditional ${row['Assets_Traditional']:,.0f}"
        )

    _run_check(report, "Phase 0", "Multi-account projection", check_multi_account_projection_columns)

    def check_ltcg_preferential_rate():
        from model.taxes import compute_retirement_tax_detail, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        ltcg_tax = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=0,
            withdrawal_gross=100_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=1.0,
            taxable_gain_fraction=1.0,
            niit_enabled=False,
        ).federal
        trad_ordinary = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=0,
            withdrawal_gross=100_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=0.0,
            taxable_gain_fraction=0.0,
            niit_enabled=False,
        ).federal
        assert ltcg_tax < trad_ordinary
        return f"LTCG $100k tax ${ltcg_tax:,.0f} vs ordinary IRA ${trad_ordinary:,.0f}"

    _run_check(report, "Phase 1", "LTCG preferential rates", check_ltcg_preferential_rate)

    def check_niit_surtax():
        from model.taxes import compute_retirement_tax_detail, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        below = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=20_000,
            earned_income=0,
            withdrawal_gross=200_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=1.0,
            taxable_gain_fraction=1.0,
            niit_enabled=True,
        ).federal
        above = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=50_000,
            earned_income=0,
            withdrawal_gross=300_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=1.0,
            taxable_gain_fraction=1.0,
            niit_enabled=True,
        ).federal
        no_niit = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=50_000,
            earned_income=0,
            withdrawal_gross=300_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            taxable_portfolio_fraction=1.0,
            taxable_gain_fraction=1.0,
            niit_enabled=False,
        ).federal
        assert above > no_niit
        assert above > below
        return f"high MAGI federal ${above:,.0f} incl. NIIT vs ${no_niit:,.0f} without"

    _run_check(report, "Phase 1", "NIIT surtax", check_niit_surtax)

    def check_age_65_standard_deduction():
        from model.taxes import compute_retirement_tax_detail, tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj", inflation_factor=1.0)
        young = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=0,
            withdrawal_gross=80_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            seniors_65_count=0,
            niit_enabled=False,
        ).federal
        senior = compute_retirement_tax_detail(
            ss_gross=0,
            pension_gross=0,
            rental_gross=0,
            earned_income=0,
            withdrawal_gross=80_000,
            roth_fraction=0.0,
            filing_status="mfj",
            inflation_factor=1.0,
            ss_tax_method="irs_provisional",
            ss_taxable_fraction=0.5,
            tax_mode="federal_brackets",
            flat_rate=0.2,
            table=table,
            state_tax_enabled=False,
            seniors_65_count=2,
            niit_enabled=False,
        ).federal
        assert senior < young
        return f"MFJ 65+ federal ${senior:,.0f} vs ${young:,.0f}"

    _run_check(report, "Phase 1", "Age 65+ standard deduction", check_age_65_standard_deduction)

    def check_birth_year_rmd_age():
        from model.rmd import person_rmd_start_age, rmd_start_age_for_birth_year

        assert rmd_start_age_for_birth_year(1959) == 73
        assert rmd_start_age_for_birth_year(1960) == 75
        person_59 = {"birth_year": 1959, "current_age": 67}
        person_60 = {"birth_year": 1960, "current_age": 65}
        assert person_rmd_start_age(person_59, current_year=2026) == 73
        assert person_rmd_start_age(person_60, current_year=2026) == 75
        return "born 1959→73, born 1960→75"

    _run_check(report, "Phase 1", "Birth-year RMD start age", check_birth_year_rmd_age)

    def check_mfs_ss_taxation():
        from model.taxes import taxable_social_security_irs

        ss = 30_000
        other = 20_000
        mfs_together = taxable_social_security_irs(
            ss, other_income=other, filing_status="mfs", mfs_lived_apart=False
        )
        mfs_apart = taxable_social_security_irs(
            ss, other_income=other, filing_status="mfs", mfs_lived_apart=True
        )
        mfj = taxable_social_security_irs(ss, other_income=other, filing_status="mfj")
        assert mfs_together > mfs_apart
        assert mfs_together >= 0.85 * ss - 1
        assert mfj < mfs_together
        return f"MFS together SS taxable ${mfs_together:,.0f}"

    _run_check(report, "Phase 1", "MFS Social Security rules", check_mfs_ss_taxation)

    def check_tax_efficient_withdrawal_order():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["tax_calculation"] = "flat"
        params["post_retire_tax_rate"] = 0.0
        params["withdrawal_order"] = "tax_efficient"
        params["persons"]["self"]["current_age"] = 64
        params["persons"]["self"]["retirement_age"] = 64
        params["persons"]["self"]["annual_contributions"] = 0
        params["persons"]["self"]["initial_taxable_assets"] = 200_000
        params["persons"]["self"]["initial_traditional_ira"] = 500_000
        params["persons"]["self"]["initial_roth_ira"] = 0
        params["persons"]["self"]["base_expenses"] = 80_000
        params["life_expectancy"] = 66
        df = run_retirement_projection(params)["self"]
        row = df.iloc[0]
        assert row["Withdrawals_Gross"] > 0
        assert row["Assets_Taxable"] < 200_000
        assert row["Assets_Traditional"] == 500_000
        return (
            f"taxable ${200_000:,.0f} → ${row['Assets_Taxable']:,.0f}, "
            f"traditional unchanged ${row['Assets_Traditional']:,.0f}"
        )

    _run_check(report, "Phase 2", "Tax-efficient withdrawal order", check_tax_efficient_withdrawal_order)

    def check_working_year_payroll_taxes():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["tax_calculation"] = "federal_brackets"
        params["working_year_tax_enabled"] = True
        params["persons"]["self"]["annual_earned_income"] = 180_000
        params["persons"]["self"]["retirement_age"] = 65
        params["life_expectancy"] = 66
        df = run_retirement_projection(params)["self"]
        accum = df[df["Phase"] == "accumulation"]
        assert not accum.empty
        row = accum.iloc[0]
        assert row["Withdrawals_Gross"] == 0
        assert row["Earned_Income"] == 180_000
        assert row["Taxes"] > 20_000
        assert row["FICA"] > 5_000
        return (
            f"W-2 taxes ${row['Taxes']:,.0f} (FICA ${row['FICA']:,.0f}), "
            f"no portfolio draw"
        )

    _run_check(report, "Phase 2", "Working-year W-2 and FICA taxes", check_working_year_payroll_taxes)

    def check_qcd_reduces_taxable_withdrawal():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "separate"
        base["inflation_rate"] = 0.0
        base["investment_return_mean"] = 0.0
        base["tax_calculation"] = "federal_brackets"
        base["state_tax_enabled"] = False
        base["niit_enabled"] = False
        base["persons"]["self"]["current_age"] = 74
        base["persons"]["self"]["retirement_age"] = 70
        base["persons"]["self"]["annual_contributions"] = 0
        base["persons"]["self"]["initial_taxable_assets"] = 0
        base["persons"]["self"]["initial_traditional_ira"] = 1_000_000
        base["persons"]["self"]["initial_roth_ira"] = 0
        base["persons"]["self"]["base_expenses"] = 60_000
        base["persons"]["self"]["ss_amount"] = 0
        base["persons"]["self"]["pension_amount"] = 0
        base["life_expectancy"] = 76
        no_qcd = run_retirement_projection(base)["self"]
        base["persons"]["self"]["qcd_annual"] = 50_000
        with_qcd = run_retirement_projection(base)["self"]
        row_no = no_qcd.iloc[0]
        row_yes = with_qcd.iloc[0]
        assert row_yes["QCD"] == 50_000
        assert row_yes["Federal_Taxes"] < row_no["Federal_Taxes"]
        assert row_yes["Withdrawals_Gross"] >= row_yes["RMD"]
        return (
            f"QCD ${row_yes['QCD']:,.0f}: federal ${row_no['Federal_Taxes']:,.0f} "
            f"→ ${row_yes['Federal_Taxes']:,.0f}"
        )

    _run_check(report, "Phase 2", "QCD reduces taxable IRA draw", check_qcd_reduces_taxable_withdrawal)

    def check_aca_true_slcsp_subsidy():
        from model.aca import compute_aca_household_cost, aca_policy_from_params

        policy = aca_policy_from_params({
            "aca_premium_mode": "aca_subsidized",
            "aca_slcsp_monthly_age_21": 500,
            "aca_selected_plan_monthly_age_21": 350,
        })
        cost = compute_aca_household_cost(
            [55],
            magi=60_000,
            policy=policy,
            filing_status="mfj",
            household_size=2,
            inflation_factor=1.0,
        )
        assert cost.slcsp_benchmark > cost.gross
        assert cost.subsidy > 0
        assert cost.net < cost.gross
        slcsp_only = aca_policy_from_params({
            "aca_premium_mode": "aca_subsidized",
            "aca_slcsp_monthly_age_21": 500,
            "aca_selected_plan_monthly_age_21": 500,
        })
        same_plan = compute_aca_household_cost(
            [55], magi=60_000, policy=slcsp_only, filing_status="mfj",
            household_size=2, inflation_factor=1.0,
        )
        assert cost.net == 0.0
        assert cost.subsidy == min(same_plan.subsidy, cost.gross)
        return (
            f"SLCSP ${cost.slcsp_benchmark:,.0f}, plan ${cost.gross:,.0f}, "
            f"subsidy ${cost.subsidy:,.0f}, net ${cost.net:,.0f}"
        )

    _run_check(report, "Phase 3", "ACA true SLCSP subsidy", check_aca_true_slcsp_subsidy)

    def check_employer_health_working_years():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["persons"]["self"]["employer_health_mode"] = "employer"
        params["persons"]["self"]["employer_health_monthly"] = 500
        params["persons"]["self"]["retirement_age"] = 65
        params["life_expectancy"] = 66
        df = run_retirement_projection(params)["self"]
        accum = df[df["Phase"] == "accumulation"]
        assert accum["Employer_Health"].iloc[0] == 6_000
        assert accum["ACA_Net"].iloc[0] == 0
        return "working year employer premium $6,000/yr"

    _run_check(report, "Phase 3", "Employer health working years", check_employer_health_working_years)

    def check_cobra_after_early_retirement():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["aca_premium_mode"] = "aca_subsidized"
        params["persons"]["self"]["current_age"] = 50
        params["persons"]["self"]["retirement_age"] = 52
        params["persons"]["self"]["employer_health_mode"] = "cobra"
        params["persons"]["self"]["employer_health_monthly"] = 900
        params["persons"]["self"]["cobra_months"] = 18
        params["life_expectancy"] = 55
        df = run_retirement_projection(params)["self"]
        age_52 = df[df["Age"] == 52].iloc[0]
        age_53 = df[df["Age"] == 53].iloc[0]
        age_54 = df[df["Age"] == 54].iloc[0]
        assert age_52["Employer_Health"] == 10_800
        assert age_52["ACA_Net"] == 0
        assert age_53["Employer_Health"] == 10_800
        assert age_53["ACA_Net"] == 0
        assert age_54["Employer_Health"] == 0
        assert age_54["ACA_Net"] > 0
        return f"age 52–53 COBRA ${age_52['Employer_Health']:,.0f}, age 54 ACA ${age_54['ACA_Net']:,.0f}"

    _run_check(report, "Phase 3", "COBRA then ACA transition", check_cobra_after_early_retirement)

    def check_medigap_plan_g():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["persons"]["self"]["current_age"] = 65
        params["persons"]["self"]["retirement_age"] = 65
        params["persons"]["self"]["medigap_plan"] = "plan_g"
        params["persons"]["self"]["health_insurance_post_retire"] = 0
        params["life_expectancy"] = 66
        df = run_retirement_projection(params)["self"]
        row = df.iloc[0]
        assert row["Medicare_Medigap"] == 164 * 12
        assert row["Medicare"] > row["Medicare_Medigap"]
        return f"Plan G Medigap ${row['Medicare_Medigap']:,.0f}/yr"

    _run_check(report, "Phase 3", "Medigap Plan G premiums", check_medigap_plan_g)

    def check_pia_from_aime_bend_points():
        from model.ss import pia_from_aime

        low = pia_from_aime(1_000)
        mid = pia_from_aime(6_000)
        high = pia_from_aime(10_000)
        assert abs(low - 900.0) < 1.0
        assert abs(mid - 2_600.92) < 1.0
        assert abs(high - 3_384.18) < 1.0
        return f"AIME $1k→PIA ${low:,.0f}, $6k→${mid:,.0f}, $10k→${high:,.0f}"

    _run_check(report, "Phase 4", "SS PIA bend-point formula", check_pia_from_aime_bend_points)

    def check_ss_claiming_age_adjustments():
        from model.ss import monthly_benefit_from_pia

        pia = 3_000.0
        early_62 = monthly_benefit_from_pia(pia, claiming_age=62, birth_year=1960)
        fra_67 = monthly_benefit_from_pia(pia, claiming_age=67, birth_year=1960)
        late_70 = monthly_benefit_from_pia(pia, claiming_age=70, birth_year=1960)
        assert early_62 < fra_67 < late_70
        assert abs(fra_67 - 3_000.0) < 0.01
        assert abs(early_62 - pia * 0.70) < 5.0
        assert abs(late_70 - pia * 1.24) < 5.0
        return f"PIA $3k: age 62 ${early_62:,.0f}, 67 ${fra_67:,.0f}, 70 ${late_70:,.0f}"

    _run_check(report, "Phase 4", "SS early/late claiming adjustments", check_ss_claiming_age_adjustments)

    def check_ss_fra_by_birth_year():
        from model.ss import full_retirement_age_years

        assert full_retirement_age_years(1954) == 66.0
        assert abs(full_retirement_age_years(1955) - 66.17) < 0.01
        assert full_retirement_age_years(1960) == 67.0
        return "FRA 1954→66, 1955→66y2m, 1960+→67"

    _run_check(report, "Phase 4", "SS full retirement age by birth year", check_ss_fra_by_birth_year)

    def check_ss_earnings_mode_projection():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["persons"]["self"]["birth_year"] = 1960
        params["persons"]["self"]["retirement_age"] = 67
        params["persons"]["self"]["ss_benefit_mode"] = "earnings"
        params["persons"]["self"]["ss_career_avg_earnings"] = 120_000
        params["persons"]["self"]["ss_start_age"] = 67
        params["persons"]["self"]["ss_amount"] = 0
        params["life_expectancy"] = 70
        df = run_retirement_projection(params)["self"]
        row = df[df["Age"] == 67].iloc[0]
        assert row["SS"] > 30_000
        assert row["SS_PIA"] > 2_500
        assert row["SS_FRA"] == 67.0
        manual = _sample_params()
        manual["mode"] = "separate"
        manual["inflation_rate"] = 0.0
        manual["persons"]["self"]["ss_benefit_mode"] = "manual"
        manual["persons"]["self"]["ss_amount"] = 25_000
        manual["persons"]["self"]["ss_start_age"] = 67
        manual["persons"]["self"]["retirement_age"] = 67
        manual["life_expectancy"] = 70
        manual_df = run_retirement_projection(manual)["self"]
        manual_row = manual_df[manual_df["Age"] == 67].iloc[0]
        assert manual_row["SS"] == 25_000
        assert manual_row["SS_PIA"] == 0.0
        return f"earnings mode SS ${row['SS']:,.0f}/yr at age 67"

    _run_check(report, "Phase 4", "SS earnings mode in projection", check_ss_earnings_mode_projection)

    def check_ss_early_claim_lower_benefit():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "separate"
        base["inflation_rate"] = 0.0
        base["persons"]["self"]["birth_year"] = 1960
        base["persons"]["self"]["retirement_age"] = 62
        base["persons"]["self"]["ss_benefit_mode"] = "pia"
        base["persons"]["self"]["ss_pia_monthly"] = 3_000
        base["persons"]["self"]["ss_amount"] = 0
        base["life_expectancy"] = 72
        at_62 = run_retirement_projection({**base, "persons": {**base["persons"], "self": {**base["persons"]["self"], "ss_start_age": 62}}})["self"]
        at_67 = run_retirement_projection({**base, "persons": {**base["persons"], "self": {**base["persons"]["self"], "ss_start_age": 67, "retirement_age": 67}}})["self"]
        ss_62 = at_62[at_62["Age"] == 62].iloc[0]["SS"]
        ss_67 = at_67[at_67["Age"] == 67].iloc[0]["SS"]
        assert ss_62 < ss_67
        return f"claim 62 ${ss_62:,.0f}/yr vs 67 ${ss_67:,.0f}/yr"

    _run_check(report, "Phase 4", "SS early claim reduces annual benefit", check_ss_early_claim_lower_benefit)

    def check_fixed_roth_conversion():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["tax_calculation"] = "federal_brackets"
        params["persons"]["self"]["current_age"] = 60
        params["persons"]["self"]["retirement_age"] = 60
        params["persons"]["self"]["initial_taxable_assets"] = 0
        params["persons"]["self"]["initial_traditional_ira"] = 500_000
        params["persons"]["self"]["initial_roth_ira"] = 0
        params["persons"]["self"]["roth_conversion_mode"] = "fixed"
        params["persons"]["self"]["roth_conversion_annual"] = 50_000
        params["persons"]["self"]["roth_conversion_start_age"] = 60
        params["persons"]["self"]["base_expenses"] = 5_000
        params["persons"]["self"]["health_insurance_pre_medicare"] = 0
        params["persons"]["self"]["health_insurance_post_retire"] = 0
        params["persons"]["self"]["ss_amount"] = 0
        params["persons"]["self"]["pension_amount"] = 0
        params["life_expectancy"] = 62
        df = run_retirement_projection(params)["self"]
        row = df.iloc[0]
        assert row["Roth_Conversion"] == 50_000
        assert row["Assets_Roth"] == 50_000
        assert row["Assets_Traditional"] < 450_000
        assert row["Taxes"] > 5_000
        return f"converted ${row['Roth_Conversion']:,.0f}, traditional ${row['Assets_Traditional']:,.0f}"

    _run_check(report, "Phase 5", "Fixed Roth conversion", check_fixed_roth_conversion)

    def check_bracket_fill_roth_conversion():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["investment_return_mean"] = 0.0
        params["tax_calculation"] = "federal_brackets"
        params["state_tax_enabled"] = False
        params["roth_conversion_target_bracket"] = 22
        params["persons"]["self"]["current_age"] = 60
        params["persons"]["self"]["retirement_age"] = 60
        params["persons"]["self"]["initial_taxable_assets"] = 0
        params["persons"]["self"]["initial_traditional_ira"] = 800_000
        params["persons"]["self"]["initial_roth_ira"] = 0
        params["persons"]["self"]["ss_amount"] = 0
        params["persons"]["self"]["pension_amount"] = 0
        params["persons"]["self"]["base_expenses"] = 40_000
        params["persons"]["self"]["roth_conversion_mode"] = "bracket_fill"
        params["persons"]["self"]["roth_conversion_start_age"] = 60
        params["life_expectancy"] = 61
        df = run_retirement_projection(params)["self"]
        row = df.iloc[0]
        assert row["Roth_Conversion"] > 20_000
        assert row["Assets_Roth"] == row["Roth_Conversion"]
        fixed = _sample_params()
        fixed.update({
            "mode": "separate",
            "inflation_rate": 0.0,
            "investment_return_mean": 0.0,
            "tax_calculation": "federal_brackets",
            "state_tax_enabled": False,
            "life_expectancy": 61,
        })
        fixed["persons"]["self"].update({
            "current_age": 60,
            "retirement_age": 60,
            "initial_taxable_assets": 0,
            "initial_traditional_ira": 800_000,
            "initial_roth_ira": 0,
            "ss_amount": 0,
            "pension_amount": 0,
            "base_expenses": 40_000,
            "roth_conversion_mode": "none",
        })
        no_conv = run_retirement_projection(fixed)["self"].iloc[0]
        assert row["Roth_Conversion"] > 0
        assert row["Assets_Traditional"] < no_conv["Assets_Traditional"]
        return f"bracket-fill converted ${row['Roth_Conversion']:,.0f}"

    _run_check(report, "Phase 5", "Bracket-fill Roth conversion", check_bracket_fill_roth_conversion)

    def check_roth_conversion_raises_magi():
        from model import run_retirement_projection

        base = _sample_params()
        base["mode"] = "separate"
        base["inflation_rate"] = 0.0
        base["persons"]["self"]["current_age"] = 60
        base["persons"]["self"]["retirement_age"] = 60
        base["persons"]["self"]["initial_traditional_ira"] = 600_000
        base["persons"]["self"]["roth_conversion_mode"] = "none"
        base["life_expectancy"] = 61
        no_conv = run_retirement_projection(base)["self"].iloc[0]
        base["persons"]["self"]["roth_conversion_mode"] = "fixed"
        base["persons"]["self"]["roth_conversion_annual"] = 40_000
        with_conv = run_retirement_projection(base)["self"].iloc[0]
        assert with_conv["MAGI"] > no_conv["MAGI"]
        return f"MAGI ${no_conv['MAGI']:,.0f} → ${with_conv['MAGI']:,.0f}"

    _run_check(report, "Phase 5", "Roth conversion increases MAGI", check_roth_conversion_raises_magi)

    def check_ltc_scenario_window():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["ltc_inflation_rate"] = 0.0
        params["persons"]["self"]["current_age"] = 78
        params["persons"]["self"]["retirement_age"] = 65
        params["persons"]["self"]["long_term_care_annual"] = 60_000
        params["persons"]["self"]["ltc_mode"] = "scenario"
        params["persons"]["self"]["ltc_start_age"] = 80
        params["persons"]["self"]["ltc_duration_years"] = 2
        params["persons"]["self"]["ltc_care_setting"] = "assisted_living"
        params["persons"]["self"]["health_insurance_pre_medicare"] = 0
        params["persons"]["self"]["health_insurance_post_retire"] = 0
        params["life_expectancy"] = 82
        df = run_retirement_projection(params)["self"]
        at_78 = df[df["Age"] == 78].iloc[0]
        at_80 = df[df["Age"] == 80].iloc[0]
        at_82 = df[df["Age"] == 82].iloc[0]
        assert at_78["LTC_Cost"] == 0
        assert at_80["LTC_Cost"] == 81_000  # 60k * 1.35 assisted living
        assert at_82["LTC_Cost"] == 0
        return f"LTC ${at_80['LTC_Cost']:,.0f} ages 80-81 only"

    _run_check(report, "Phase 6", "LTC scenario window", check_ltc_scenario_window)

    def check_age_banded_expenses():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["expense_phase_mode"] = "age_banded"
        params["expense_go_go_end_age"] = 70
        params["expense_slow_go_end_age"] = 75
        params["expense_go_go_pct"] = 100
        params["expense_slow_go_pct"] = 80
        params["expense_no_go_pct"] = 60
        params["persons"]["self"]["current_age"] = 68
        params["persons"]["self"]["retirement_age"] = 68
        params["persons"]["self"]["base_expenses"] = 50_000
        params["persons"]["self"]["long_term_care_annual"] = 0
        params["persons"]["self"]["health_insurance_pre_medicare"] = 0
        params["persons"]["self"]["health_insurance_post_retire"] = 0
        params["life_expectancy"] = 76
        df = run_retirement_projection(params)["self"]
        go_go = df[df["Age"] == 69].iloc[0]
        slow_go = df[df["Age"] == 72].iloc[0]
        no_go = df[df["Age"] == 76].iloc[0]
        assert go_go["Expense_Phase"] == "go_go"
        assert slow_go["Expense_Phase"] == "slow_go"
        assert no_go["Expense_Phase"] == "no_go"
        assert go_go["Expenses"] > slow_go["Expenses"] > no_go["Expenses"]
        return (
            f"go-go ${go_go['Expenses']:,.0f} → slow-go ${slow_go['Expenses']:,.0f} "
            f"→ no-go ${no_go['Expenses']:,.0f}"
        )

    _run_check(report, "Phase 6", "Age-banded spending", check_age_banded_expenses)

    def check_survivor_filing_and_expenses():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "joint"
        params["inflation_rate"] = 0.0
        params["persons"]["self"]["current_age"] = 88
        params["persons"]["self"]["retirement_age"] = 65
        params["persons"]["self"]["life_expectancy"] = 90
        params["persons"]["self"]["base_expenses"] = 40_000
        params["persons"]["self"]["long_term_care_annual"] = 0
        params["persons"]["wife"]["current_age"] = 86
        params["persons"]["wife"]["retirement_age"] = 65
        params["persons"]["wife"]["life_expectancy"] = 86
        params["persons"]["wife"]["base_expenses"] = 35_000
        params["persons"]["wife"]["long_term_care_annual"] = 0
        params["life_expectancy"] = 92
        df = run_retirement_projection(params)["joint"]
        both_alive = df[df["Age"] == 86].iloc[0]
        survivor = df[df["Age"] == 87].iloc[0]
        assert both_alive["Expenses"] > survivor["Expenses"]
        assert "survivor" in str(survivor["Filing_Status"]).lower()
        return (
            f"expenses ${both_alive['Expenses']:,.0f} → ${survivor['Expenses']:,.0f}, "
            f"filing {survivor['Filing_Status']}"
        )

    _run_check(report, "Phase 6", "Survivor filing and expenses", check_survivor_filing_and_expenses)

    def check_mortgage_and_one_time_expense():
        from model import run_retirement_projection

        params = _sample_params()
        params["mode"] = "separate"
        params["inflation_rate"] = 0.0
        params["current_year"] = 2026
        params["persons"]["self"]["current_age"] = 60
        params["persons"]["self"]["retirement_age"] = 60
        params["persons"]["self"]["base_expenses"] = 30_000
        params["persons"]["self"]["mortgage_annual"] = 24_000
        params["persons"]["self"]["mortgage_payoff_year"] = 2026
        params["persons"]["self"]["one_time_expense_year"] = 2028
        params["persons"]["self"]["one_time_expense_amount"] = 50_000
        params["persons"]["self"]["long_term_care_annual"] = 0
        params["persons"]["self"]["health_insurance_pre_medicare"] = 0
        params["persons"]["self"]["health_insurance_post_retire"] = 0
        params["life_expectancy"] = 62
        df = run_retirement_projection(params)["self"]
        y2026 = df[df["Year"] == 2026].iloc[0]
        y2027 = df[df["Year"] == 2027].iloc[0]
        y2028 = df[df["Year"] == 2028].iloc[0]
        assert y2026["Expenses"] == 54_000
        assert y2027["Expenses"] == 30_000
        assert y2028["Expenses"] == 80_000

        # Fixed mortgage must not grow with inflation
        params2 = _sample_params()
        params2["mode"] = "separate"
        params2["inflation_rate"] = 0.10
        params2["current_year"] = 2026
        params2["persons"]["self"]["current_age"] = 60
        params2["persons"]["self"]["retirement_age"] = 60
        params2["persons"]["self"]["base_expenses"] = 0
        params2["persons"]["self"]["mortgage_annual"] = 12_000
        params2["persons"]["self"]["mortgage_payoff_year"] = 2030
        params2["persons"]["self"]["long_term_care_annual"] = 0
        params2["persons"]["self"]["health_insurance_pre_medicare"] = 0
        params2["persons"]["self"]["health_insurance_post_retire"] = 0
        params2["life_expectancy"] = 65
        df2 = run_retirement_projection(params2)["self"]
        y2027 = df2[df2["Year"] == 2027].iloc[0]
        assert abs(float(y2027["Expenses"]) - 12_000) < 1.0, y2027["Expenses"]
        return "mortgage + one-time expense timing OK; mortgage not inflated"

    _run_check(report, "Phase 6", "Mortgage and one-time expense", check_mortgage_and_one_time_expense)

    def check_mortgage_amortization():
        from model.mortgage_amort import amortization_summary, months_to_payoff

        assert months_to_payoff(120_000, 1_000, 0.0) == 120
        assert months_to_payoff(100_000, 100, 6.0) is None  # below interest-only
        summary = amortization_summary(
            623_358.01,
            2_891.88,
            2.625,
            as_of_year=2026,
            as_of_month=9,
        )
        assert summary is not None
        assert summary["payoff_year"] == 2050
        assert 290 <= summary["months_remaining"] <= 295
        return f"payoff {summary['payoff_year']} (~{summary['years_remaining']} yrs)"

    _run_check(report, "Phase 6", "Mortgage amortization maturity", check_mortgage_amortization)

    # --- Phase 3: Expense analyzer ---
    def check_expense_analyzer():
        from providers.expense_analyzer import analyze_expenses
        from providers.plaid_provider import TransactionRecord

        txns = [
            TransactionRecord(
                "e1", "a1", "2025-01-15", 1200.0, "Mortgage Payment", "Chase", False,
                pfc_primary="LOAN_PAYMENTS", pfc_detailed="LOAN_PAYMENTS_MORTGAGE_PAYMENT",
            ),
            TransactionRecord(
                "e2", "a1", "2025-02-15", 1200.0, "Mortgage Payment", "Chase", False,
                pfc_primary="LOAN_PAYMENTS", pfc_detailed="LOAN_PAYMENTS_MORTGAGE_PAYMENT",
            ),
            TransactionRecord(
                "e3", "a1", "2025-03-15", 1200.0, "Mortgage Payment", "Chase", False,
                pfc_primary="LOAN_PAYMENTS", pfc_detailed="LOAN_PAYMENTS_MORTGAGE_PAYMENT",
            ),
        ]
        analysis = analyze_expenses(txns)
        assert analysis.transactions_used >= 1
        assert analysis.annual_household > 0
        return f"annual household ${analysis.annual_household:,.0f}"

    _run_check(report, "Phase 3", "Expense analyzer", check_expense_analyzer)

    def check_expense_owner_attribution():
        from providers.expense_analyzer import analyze_expenses
        from providers.plaid_provider import TransactionRecord

        def utility_txn(tid: str, account: str, month: int) -> TransactionRecord:
            return TransactionRecord(
                tid,
                account,
                f"2025-{month:02d}-15",
                180.0,
                "City Power",
                "City Power",
                False,
                pfc_primary="RENT_AND_UTILITIES",
                pfc_detailed="RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
            )

        txns = [utility_txn(f"m{i}", "a_self", i) for i in range(1, 4)]
        txns += [utility_txn(f"w{i}", "a_wife", i) for i in range(1, 4)]
        analysis = analyze_expenses(
            txns,
            account_owners={"a_self": "self", "a_wife": "wife"},
            dedupe_household=False,
        )
        totals = analysis.owner_annual_totals("separate")
        assert totals["self"] > 0
        assert totals["wife"] > 0
        return f"self ${totals['self']:,.0f}, wife ${totals['wife']:,.0f}"

    _run_check(report, "Phase 7", "Expense owner attribution", check_expense_owner_attribution)

    def check_expense_household_dedup():
        from providers.expense_analyzer import analyze_expenses
        from providers.plaid_provider import TransactionRecord

        txns = []
        for i in range(1, 4):
            txns.append(
                TransactionRecord(
                    f"s{i}",
                    "acct_self",
                    f"2025-{i:02d}-10",
                    85.0,
                    "Netflix",
                    "Netflix",
                    False,
                    pfc_primary="ENTERTAINMENT",
                )
            )
            txns.append(
                TransactionRecord(
                    f"w{i}",
                    "acct_wife",
                    f"2025-{i:02d}-12",
                    85.0,
                    "Netflix",
                    "Netflix",
                    False,
                    pfc_primary="ENTERTAINMENT",
                )
            )
        deduped = analyze_expenses(
            txns,
            account_owners={"acct_self": "self", "acct_wife": "wife"},
            dedupe_household=True,
        )
        raw = analyze_expenses(
            txns,
            account_owners={"acct_self": "self", "acct_wife": "wife"},
            dedupe_household=False,
        )
        assert deduped.annual_household < raw.annual_household
        assert len(deduped.items) < len(raw.items)
        return (
            f"deduped ${deduped.annual_household:,.0f} vs raw ${raw.annual_household:,.0f}"
        )

    _run_check(report, "Phase 7", "Expense household dedup", check_expense_household_dedup)

    def check_expense_snapshot_roundtrip():
        from services.data_service import DataService, ExpenseSnapshot
        from providers.expense_analyzer import ExpenseAnalysis, ExpenseLineItem
        from security.vault import Vault

        analysis = ExpenseAnalysis(
            items=[
                ExpenseLineItem(
                    merchant="utilities",
                    category="utilities",
                    expense_type="recurring",
                    monthly_amount=250.0,
                    annual_amount=3000.0,
                    occurrence_count=6,
                    owner="self",
                )
            ],
            annual_household=3000.0,
            months_analyzed=6.0,
            transactions_used=6,
        )
        snapshot = ExpenseSnapshot(as_of="2026-06-20T12:00:00+00:00", analysis=analysis)
        restored = ExpenseSnapshot.from_vault_dict(snapshot.to_vault_dict())
        assert restored.analysis.items[0].owner == "self"
        assert restored.analysis.annual_household == 3000.0

        with tempfile.TemporaryDirectory() as tmp:
            service = DataService(vault=Vault(data_dir=Path(tmp)))
            service.save_expense_snapshot(snapshot)
            cached = service.load_cached_expense_snapshot()
            assert cached is not None
            assert cached.analysis.included_annual_total() == 3000.0
        return "vault roundtrip OK"

    _run_check(report, "Phase 7", "Expense snapshot vault roundtrip", check_expense_snapshot_roundtrip)

    def check_expense_owner_override_persistence():
        from services.data_service import DataService, ExpenseSnapshot
        from providers.expense_analyzer import ExpenseAnalysis, ExpenseLineItem
        from security.vault import Vault

        item = ExpenseLineItem(
            merchant="netflix",
            category="subscriptions",
            expense_type="recurring",
            monthly_amount=15.0,
            annual_amount=180.0,
            occurrence_count=6,
            owner="unassigned",
        )
        analysis = ExpenseAnalysis(
            items=[item],
            annual_household=180.0,
            months_analyzed=6.0,
            transactions_used=6,
        )
        snapshot = ExpenseSnapshot(as_of="2026-06-20T12:00:00+00:00", analysis=analysis)
        item.owner = "wife"
        item.included = False

        with tempfile.TemporaryDirectory() as tmp:
            service = DataService(vault=Vault(data_dir=Path(tmp)))
            service.save_expense_snapshot(snapshot)
            cached = service.load_cached_expense_snapshot()
            assert cached is not None
            restored = cached.analysis.items[0]
            assert restored.owner == "wife"
            assert restored.included is False
        return "owner override persisted"

    _run_check(
        report,
        "Phase 7",
        "Expense owner override persistence",
        check_expense_owner_override_persistence,
    )

    def check_expense_mortgage_pfc_category():
        from providers.expense_analyzer import analyze_expenses
        from providers.plaid_provider import TransactionRecord

        txns = [
            TransactionRecord(
                f"m{i}",
                "a1",
                f"2025-{i:02d}-01",
                2100.0,
                "Mortgage",
                "Wells Fargo",
                False,
                pfc_primary="LOAN_PAYMENTS",
                pfc_detailed="LOAN_PAYMENTS_MORTGAGE_PAYMENT",
            )
            for i in range(1, 4)
        ]
        analysis = analyze_expenses(txns, account_owners={"a1": "self"})
        assert analysis.items[0].category == "mortgage"
        assert analysis.items[0].expense_type == "recurring"
        return f"category={analysis.items[0].category}"

    _run_check(
        report,
        "Phase 7",
        "Expense mortgage PFC category",
        check_expense_mortgage_pfc_category,
    )

    def check_expense_excludes_credit_card_payments():
        from providers.expense_analyzer import analyze_expenses
        from providers.plaid_provider import TransactionRecord

        txns = [
            TransactionRecord(
                f"cc{i}",
                "a1",
                f"2025-{i:02d}-15",
                2000.0,
                "CAPITAL ONE CRCARDPMT",
                "Capital One",
                False,
                pfc_primary="LOAN_PAYMENTS",
                pfc_detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
            )
            for i in range(1, 4)
        ]
        txns += [
            TransactionRecord(
                f"m{i}",
                "a1",
                f"2025-{i:02d}-01",
                2891.0,
                "FREEDOM MORTGAGE",
                "Freedom Mortgage",
                False,
                pfc_primary="LOAN_PAYMENTS",
                pfc_detailed="LOAN_PAYMENTS_MORTGAGE_PAYMENT",
            )
            for i in range(1, 4)
        ]
        analysis = analyze_expenses(txns, account_owners={"a1": "self"})
        merchants = {item.merchant for item in analysis.items}
        assert not any("capital one" in m for m in merchants)
        assert any("freedom" in m for m in merchants)
        mort = next(i for i in analysis.items if "freedom" in i.merchant)
        assert mort.category == "mortgage"
        return f"items={len(analysis.items)} mortgage_ok"

    _run_check(
        report,
        "Phase 7",
        "Expense excludes credit-card payments",
        check_expense_excludes_credit_card_payments,
    )

    def check_expense_one_off_default_excluded():
        from providers.expense_analyzer import analyze_expenses
        from providers.plaid_provider import TransactionRecord

        txns = [
            TransactionRecord(
                "car1",
                "a1",
                "2025-06-01",
                35_000.0,
                "CarMax",
                "CarMax",
                False,
                pfc_primary="GENERAL_MERCHANDISE",
                pfc_detailed=None,
            )
        ]
        analysis = analyze_expenses(txns, account_owners={"a1": "self"})
        assert len(analysis.items) == 1
        assert analysis.items[0].expense_type == "variable"
        assert analysis.items[0].included is False
        return "one-off excluded by default"

    _run_check(
        report,
        "Phase 7",
        "Expense one-off default excluded",
        check_expense_one_off_default_excluded,
    )

    # --- Stochastic A+: Monte Carlo enhancements ---
    def check_mc_correlated_sampling():
        from simulation.monte_carlo import _load_correlated_bootstrap

        returns, inflation, label, _errors = _load_correlated_bootstrap(
            symbol="^GSPC",
            lookback_years=10,
            fallback_return=0.07,
            fallback_inflation=0.03,
            fred_api_key=os.getenv("FRED_API_KEY"),
        )
        assert len(returns) == len(inflation)
        assert len(returns) >= 1
        if os.getenv("FRED_API_KEY"):
            assert "Correlated" in label or "Independent" in label
        return f"{label}, {len(returns)} pairs"

    _run_check(report, "Stochastic A+", "Correlated return/inflation bootstrap", check_mc_correlated_sampling)

    def check_mc_stochastic_longevity():
        from simulation.monte_carlo import run_monte_carlo_simulation

        fixed = run_monte_carlo_simulation(
            _sample_params(),
            num_paths=120,
            seed=11,
            stochastic_longevity=False,
            healthcare_uncertainty=False,
        )["joint"]
        variable = run_monte_carlo_simulation(
            _sample_params(),
            num_paths=120,
            seed=11,
            stochastic_longevity=True,
            healthcare_uncertainty=False,
        )["joint"]
        assert fixed.longevity_model.startswith("Fixed")
        assert "Normal" in variable.longevity_model
        assert variable.percentile_paths.shape[0] >= fixed.percentile_paths.shape[0]
        return f"fixed={fixed.success_rate:.1%}, stochastic={variable.success_rate:.1%}"

    _run_check(report, "Stochastic A+", "Stochastic longevity paths", check_mc_stochastic_longevity)

    def check_mc_healthcare_bands():
        from simulation.monte_carlo import run_monte_carlo_simulation

        baseline = run_monte_carlo_simulation(
            _sample_params(),
            num_paths=200,
            seed=17,
            stochastic_longevity=False,
            healthcare_uncertainty=False,
        )["joint"]
        stressed = run_monte_carlo_simulation(
            _sample_params(),
            num_paths=200,
            seed=17,
            stochastic_longevity=False,
            healthcare_uncertainty=True,
            healthcare_sigma=0.35,
        )["joint"]
        assert stressed.healthcare_multiplier_p10 < 1.0 < stressed.healthcare_multiplier_p90
        assert stressed.success_rate <= baseline.success_rate + 0.05
        return (
            f"health band {stressed.healthcare_multiplier_p10:.0%}–"
            f"{stressed.healthcare_multiplier_p90:.0%}, success={stressed.success_rate:.1%}"
        )

    _run_check(report, "Stochastic A+", "Healthcare cost uncertainty bands", check_mc_healthcare_bands)

    def check_mc_shortfall_metrics():
        from simulation.monte_carlo import run_monte_carlo_simulation

        stressed_params = _sample_params()
        stressed_params["persons"]["self"]["base_expenses"] = 140_000
        stressed_params["persons"]["wife"]["base_expenses"] = 120_000
        mc = run_monte_carlo_simulation(stressed_params, num_paths=250, seed=23)["joint"]
        assert mc.mean_shortfall_years >= 0
        assert 0 <= mc.severe_failure_rate <= 1
        if mc.success_rate < 1.0:
            assert mc.median_depletion_age is not None
        return (
            f"success={mc.success_rate:.1%}, depletion={mc.median_depletion_age}, "
            f"shortfall yrs={mc.mean_shortfall_years:.1f}"
        )

    _run_check(report, "Stochastic A+", "Shortfall severity metrics", check_mc_shortfall_metrics)

    # --- Phase 8: Golden-file tax validation ---
    def check_golden_tax_scenarios():
        from validation.golden_runner import run_golden_scenarios

        results, summary = run_golden_scenarios()
        if summary["failed"]:
            failed = [r for r in results if not r.passed][:3]
            detail = "; ".join(f"{r.scenario_id}: {r.message}" for r in failed)
            raise AssertionError(f"{summary['failed']} failed — {detail}")
        assert summary["total"] >= 50
        return f"{summary['passed']}/{summary['total']} golden scenarios"

    _run_check(report, "Phase 8", "Golden-file tax scenarios (50+)", check_golden_tax_scenarios)

    def check_policy_table_update_tool():
        import subprocess

        script = PROJECT_ROOT / "tools" / "update_policy_tables.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        template_year = 2027
        proc2 = subprocess.run(
            [sys.executable, str(script), "--template", str(template_year)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc2.returncode != 0:
            raise AssertionError(proc2.stdout + proc2.stderr)
        template_path = PROJECT_ROOT / "config" / "policy" / "updates" / f"{template_year}.template.json"
        assert template_path.exists()
        template_path.unlink(missing_ok=True)
        return proc.stdout.strip()

    _run_check(report, "Phase 8", "Policy table update tooling", check_policy_table_update_tool)

    def check_live_policy_tables():
        import subprocess

        from config.policy.live import activate_policy_year, get_active_policy_meta
        from config.policy.tables import policy_for_projection

        script = PROJECT_ROOT / "tools" / "fetch_policy_tables.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--list"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        activate_policy_year(2025)
        meta = get_active_policy_meta()
        assert meta is not None and meta.tax_year == 2025
        policy = policy_for_projection(inflation_factor=1.0)
        assert policy.base_year == 2025
        assert policy.mfj_standard_deduction == 30_000
        return f"active {meta.tax_year}, MFJ SD ${policy.mfj_standard_deduction:,.0f}"

    _run_check(report, "Phase 9", "Live IRS policy tables", check_live_policy_tables)

    def check_tax_return_validator():
        from config.policy.live import activate_policy_year
        from validation.tax_return_validator import validate_tax_return_file

        activate_policy_year(2024)
        fixture = PROJECT_ROOT / "validation" / "examples" / "tax_return_2024_mfj_wages.json"
        result = validate_tax_return_file(fixture, auto_activate_policy=False)
        if not result.passed:
            failed = [c for c in result.comparisons if not c.passed]
            detail = "; ".join(f"{c.line}: {c.message}" for c in failed[:3])
            raise AssertionError(detail)
        return result.summary_text().splitlines()[0]

    _run_check(report, "Phase 9", "Tax return validator fixture", check_tax_return_validator)

    def check_mc_stock_bond_blend():
        from simulation.monte_carlo import _blend_returns_by_year, _normalize_stock_allocation_pct

        stock = [(2020, 0.10), (2021, 0.20)]
        bond = [(2020, 0.02), (2021, 0.03)]
        blended = _blend_returns_by_year(stock, bond, 60.0)
        assert len(blended) == 2
        assert abs(blended[0][1] - (0.6 * 0.10 + 0.4 * 0.02)) < 1e-9
        assert _normalize_stock_allocation_pct(150) == 100.0
        assert _normalize_stock_allocation_pct(-5) == 0.0
        return "60/40 blend math OK"

    _run_check(report, "Phase 9", "MC stock/bond allocation blend", check_mc_stock_bond_blend)

    def check_fetch_policy_tool():
        import subprocess

        script = PROJECT_ROOT / "tools" / "fetch_policy_tables.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--status"],
            capture_output=True,
            text=True,
            check=False,
        )
        if "Available years" not in proc.stdout:
            raise AssertionError(proc.stdout + proc.stderr)
        return "fetch_policy_tables --status OK"

    _run_check(report, "Phase 9", "fetch_policy_tables CLI", check_fetch_policy_tool)

    def check_irs_newsroom_parser_fixture():
        from config.policy.irs_fetcher import build_policy_payload

        fixture = PROJECT_ROOT / "validation" / "fixtures" / "irs_newsroom_2025_snippet.html"
        payload = build_policy_payload(tax_year=2025, html=fixture.read_text(encoding="utf-8"))
        assert payload["scalars"]["single_standard_deduction"] == 15_000
        assert payload["scalars"]["mfj_standard_deduction"] == 30_000
        assert payload["brackets"]["single"][0] == [11_925, 0.10]
        assert payload["amt"]["exemption_mfj"] == 137_000
        return "newsroom HTML parser fixture OK"

    _run_check(report, "Phase 9", "IRS newsroom policy parser", check_irs_newsroom_parser_fixture)

    def check_irs_newsroom_2026_obbb_fixture():
        from config.policy.irs_fetcher import build_policy_payload

        fixture = PROJECT_ROOT / "validation" / "fixtures" / "irs_newsroom_2026_snippet.html"
        payload = build_policy_payload(tax_year=2026, html=fixture.read_text(encoding="utf-8"))
        assert payload["scalars"]["single_standard_deduction"] == 16_100
        assert payload["scalars"]["mfj_standard_deduction"] == 32_200
        assert payload["amt"]["exemption_mfj"] == 140_200
        assert payload["amt"]["phaseout_start_mfj"] == 1_000_000
        return "2026 OBBB newsroom parser fixture OK"

    _run_check(report, "Phase 9", "IRS 2026 OBBB policy parser", check_irs_newsroom_2026_obbb_fixture)

    def check_irs_llm_fallback_gating():
        from config.policy.irs_llm import llm_fallback_enabled

        # Offline check: function is importable and returns a bool without network.
        assert isinstance(llm_fallback_enabled(), bool)
        return f"LLM policy fallback available={llm_fallback_enabled()}"

    _run_check(report, "Phase 9", "IRS LLM fallback gating", check_irs_llm_fallback_gating)

    def check_irs_live_fetch_optional():
        if os.getenv("REGRESSION_IRS_FETCH", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError("SKIP")
        from config.policy.irs_fetcher import fetch_and_build_policy

        payload = fetch_and_build_policy(2025)
        assert payload["scalars"]["mfj_standard_deduction"] == 30_000
        return f"live fetch TY2025 MFJ SD ${payload['scalars']['mfj_standard_deduction']:,.0f}"

    _run_check(report, "Phase 9", "IRS live fetch (optional)", check_irs_live_fetch_optional)
    if report.results[-1].status == "fail" and "SKIP" in report.results[-1].detail:
        report.results[-1].status = "skip"
        report.results[-1].detail = "Set REGRESSION_IRS_FETCH=1 to run live IRS pull test"

    def check_cpa_itemized_deduction():
        from model.cpa_tax import ItemizedInputs, compute_itemized_deduction

        total, salt = compute_itemized_deduction(
            ItemizedInputs(salt=12_000, mortgage_interest=18_000, charitable=5_000, medical=2_000),
            agi=100_000,
        )
        assert salt == 10_000
        assert total > 29_000
        return f"itemized ${total:,.0f} (SALT capped ${salt:,.0f})"

    _run_check(report, "CPA Precision", "Itemized SALT cap", check_cpa_itemized_deduction)

    def check_cpa_amt_triggers():
        from model.cpa_tax import ItemizedInputs, federal_tax_cpa_precision
        from model.taxes import tax_table_for_filing_status

        table = tax_table_for_filing_status("mfj")
        itemized = federal_tax_cpa_precision(
            ordinary_income=500_000,
            ltcg=0,
            table=table,
            filing_status="mfj",
            itemized=ItemizedInputs(salt=10_000, mortgage_interest=30_000, charitable=5_000),
            agi=500_000,
        )
        standard = federal_tax_cpa_precision(
            ordinary_income=500_000,
            ltcg=0,
            table=table,
            filing_status="mfj",
            itemized=ItemizedInputs(),
            agi=500_000,
        )
        assert itemized.deduction_method == "itemized"
        assert itemized.deduction_used > standard.deduction_used
        # Mortgage interest and charity reduce AMTI, so this mix stays on the regular tax.
        assert itemized.amt_surcharge == 0
        assert itemized.total_federal < standard.total_federal
        return (
            f"itemized ded ${itemized.deduction_used:,.0f}, "
            f"AMT ${itemized.amt_surcharge:,.0f}"
        )

    _run_check(report, "CPA Precision", "Itemized vs standard + AMT", check_cpa_amt_triggers)

    def check_cpa_projection_columns():
        from model import run_retirement_projection

        params = _sample_params()
        params["planning_precision"] = "cpa"
        params["itemized_salt_annual"] = 8_000
        params["itemized_mortgage_interest"] = 14_000
        df = run_retirement_projection(params)["joint"]
        assert "Deduction_Method" in df.columns
        assert "AMT_Surcharge" in df.columns
        return "CPA columns present in projection"

    _run_check(report, "CPA Precision", "CPA projection output", check_cpa_projection_columns)

    def check_cpa_unsupported_state_validation():
        from model import run_retirement_projection

        params = _sample_params()
        params["planning_precision"] = "cpa"
        params["state_of_residence"] = "VT"
        params["state_tax_override_pct"] = ""
        try:
            run_retirement_projection(params)
            raise AssertionError("expected ValueError for unsupported state")
        except ValueError as exc:
            assert "CPA precision" in str(exc)
        return "unsupported state blocked"

    _run_check(report, "CPA Precision", "Strict state validation", check_cpa_unsupported_state_validation)

    def check_cpa_ma_nj_states():
        from model.state_taxes import compute_state_income_tax, state_profile

        ma = state_profile("MA", strict=True)
        nj = state_profile("NJ", strict=True)
        assert ma is not None and nj is not None
        ma_tax = compute_state_income_tax(
            120_000,
            ss_taxable=0.0,
            profile=ma,
            filing_status="mfj",
            inflation_factor=1.0,
        )
        nj_tax = compute_state_income_tax(
            120_000,
            ss_taxable=0.0,
            profile=nj,
            filing_status="mfj",
            inflation_factor=1.0,
        )
        assert ma_tax > 4_000
        assert nj_tax > 3_000
        return f"MA ${ma_tax:,.0f}, NJ ${nj_tax:,.0f} on $120k MFJ"

    _run_check(report, "CPA Round 2", "Massachusetts and New Jersey profiles", check_cpa_ma_nj_states)

    def check_cpa_ma_projection():
        from model import run_retirement_projection

        params = _sample_params()
        params["planning_precision"] = "cpa"
        params["state_of_residence"] = "MA"
        run_retirement_projection(params)
        return "CPA projection with MA residence OK"

    _run_check(report, "CPA Round 2", "CPA mode accepts Massachusetts", check_cpa_ma_projection)

    def check_cpa_working_year_itemized():
        from model.payroll import ItemizedInputs, compute_working_year_tax_detail

        standard = compute_working_year_tax_detail(
            250_000,
            filing_status="mfj",
            inflation_factor=1.0,
            tax_mode="federal_brackets",
            flat_rate=0.0,
            state_code="CA",
            state_tax_enabled=False,
            state_tax_override_pct=None,
            planning_precision="standard",
        )
        itemized = compute_working_year_tax_detail(
            250_000,
            filing_status="mfj",
            inflation_factor=1.0,
            tax_mode="federal_brackets",
            flat_rate=0.0,
            state_code="CA",
            state_tax_enabled=False,
            state_tax_override_pct=None,
            planning_precision="cpa",
            itemized=ItemizedInputs(
                salt=10_000,
                mortgage_interest=28_000,
                charitable=6_000,
            ),
        )
        assert itemized.deduction_method == "itemized"
        assert itemized.federal_deduction > standard.federal_deduction
        return (
            f"itemized W-2 ded ${itemized.federal_deduction:,.0f}, "
            f"fed tax ${itemized.federal:,.0f}"
        )

    _run_check(report, "CPA Round 2", "Working-year CPA itemized deduction", check_cpa_working_year_itemized)

    def check_standard_state_fallback_warning():
        from model import collect_param_warnings, run_retirement_projection

        params = _sample_params()
        params["planning_precision"] = "standard"
        params["state_of_residence"] = "VT"
        warnings = collect_param_warnings(params)
        assert warnings and "VT" in warnings[0]
        df = run_retirement_projection(params)["joint"]
        formula_notes = str(df.iloc[0].get("Formula_Notes", ""))
        assert "California" in formula_notes or "catalog" in formula_notes.lower()
        return warnings[0][:80]

    _run_check(report, "CPA Round 2", "Standard mode unknown-state warning", check_standard_state_fallback_warning)

    def check_contribution_analyzer():
        from providers.contribution_analyzer import analyze_contributions
        from providers.plaid_provider import TransactionRecord

        txns = [
            TransactionRecord(
                "c1", "a1", "2025-06-01", -1500.0, "IRA Contribution", "Fidelity", False,
                pfc_detailed="TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
            ),
            TransactionRecord(
                "c2", "a1", "2025-07-01", -1500.0, "IRA Contribution", "Fidelity", False,
                pfc_detailed="TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
            ),
        ]
        analysis = analyze_contributions(txns, account_owners={"a1": "self"})
        assert analysis.transactions_used == 2
        assert analysis.owner_annual_totals("separate")["self"] > 0
        return f"annual self ${analysis.owner_annual_totals('separate')['self']:,.0f}"

    _run_check(report, "Phase 3", "Contribution analyzer", check_contribution_analyzer)

    # --- Phase 4: Monte Carlo ---
    def check_monte_carlo():
        from simulation.monte_carlo import run_simulation

        output = run_simulation(_sample_params(), simulation_mode="both", num_paths=500, seed=42)
        assert "joint" in output.deterministic
        assert "joint" in output.monte_carlo
        mc = output.monte_carlo["joint"]
        assert 0 <= mc.success_rate <= 1
        assert not mc.percentile_paths.empty
        assert {"P10", "P50", "P90"}.issubset(mc.percentile_paths.columns)
        return f"success={mc.success_rate:.1%}, median end ${mc.median_ending_balance:,.0f}"

    _run_check(report, "Phase 4", "Monte Carlo simulation", check_monte_carlo)

    def check_mc_parallel_parity():
        import os

        from simulation.monte_carlo import run_monte_carlo_simulation

        params = _sample_params()
        os.environ["RETIREMENT_MC_WORKERS"] = "0"
        sequential = run_monte_carlo_simulation(params, num_paths=60, seed=7)["joint"]
        os.environ["RETIREMENT_MC_WORKERS"] = "4"
        parallel = run_monte_carlo_simulation(params, num_paths=60, seed=7)["joint"]
        assert abs(sequential.success_rate - parallel.success_rate) < 1e-9
        assert abs(sequential.median_ending_balance - parallel.median_ending_balance) < 1.0
        return (
            f"seq/par median ${sequential.median_ending_balance:,.0f} / "
            f"${parallel.median_ending_balance:,.0f}"
        )

    _run_check(report, "Phase 4", "Monte Carlo parallel parity", check_mc_parallel_parity)

    def check_llm_batch_parallel_helpers():
        from providers.assumption_research import _llm_batch_workers, build_macro_context

        assert _llm_batch_workers() >= 1
        macro = build_macro_context(None)
        assert "unavailable" in macro.lower()
        return f"llm workers={_llm_batch_workers()}"

    _run_check(report, "Phase 5", "LLM batch parallel helpers", check_llm_batch_parallel_helpers)

    def check_llm_research_cache():
        import os
        import tempfile
        from unittest.mock import patch

        from providers.assumption_research import (
            AssumptionResearchContext,
            AssumptionSuggestion,
            research_assumption,
        )
        from providers.llm_research_cache import LLMResearchCache, context_cache_key

        context = AssumptionResearchContext(
            field_key="health_insurance_post_retire",
            person="self",
            current_age=55,
            retirement_age=62,
            state="CA",
            planning_year=2026,
            extra_prompt="Bay Area",
            macro_context="CPI reference",
        )
        key_a = context_cache_key(context, provider="anthropic", model="claude-test")
        key_b = context_cache_key(context, provider="anthropic", model="claude-test")
        assert key_a == key_b
        key_other = context_cache_key(
            AssumptionResearchContext(
                field_key="health_insurance_post_retire",
                person="self",
                planning_year=2027,
            ),
            provider="anthropic",
            model="claude-test",
        )
        assert key_other != key_a

        suggestion = AssumptionSuggestion(
            field_key="health_insurance_post_retire",
            suggested_annual_usd=72_000.0,
            confidence="medium",
            projection_method="test",
            sources=["example"],
            explanation="cached value",
            provider="anthropic",
            model="claude-test",
            researched_at="2026-06-27T00:00:00+00:00",
        )

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "llm_research_cache.json")
            with patch("providers.llm_research_cache._cache_path", return_value=__import__("pathlib").Path(cache_path)):
                cache = LLMResearchCache()
                hit = cache.lookup(context, provider="anthropic", model="claude-test")
                assert hit.suggestion is None
                cache.store(hit.cache_key, context, suggestion, provider="anthropic", model="claude-test")
                cache.save()

                cache2 = LLMResearchCache()
                hit2 = cache2.lookup(context, provider="anthropic", model="claude-test")
                assert hit2.suggestion is not None
                assert hit2.suggestion.suggested_annual_usd == 72_000.0

                class _FakeCompletion:
                    content = (
                        '{"suggested_value": 99000, "confidence": "high", '
                        '"projection_method": "live", "sources": [], "explanation": "live"}'
                    )
                    provider = "anthropic"
                    model = "claude-test"

                class _FakeLLM:
                    provider = "anthropic"

                    def complete(self, **kwargs):
                        raise AssertionError("LLM should not be called on cache hit")

                got, from_cache = research_assumption(
                    context,
                    provider="anthropic",
                    model="claude-test",
                    llm=_FakeLLM(),
                    resolved_model="claude-test",
                    cache=cache2,
                )
                assert from_cache is True
                assert got.suggested_annual_usd == 72_000.0

        return "LLM research cache hit/miss OK"

    _run_check(report, "Phase 5", "LLM research result cache", check_llm_research_cache)

    def check_mc_performance():
        # Deferred: 5000-path wall-clock budget is machine-dependent (not a correctness check).
        # Set REGRESSION_MC_PERF=1 to run locally when profiling optimizations.
        if os.getenv("REGRESSION_MC_PERF", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError("SKIP")
        from simulation.monte_carlo import run_monte_carlo_simulation

        start = time.perf_counter()
        run_monte_carlo_simulation(_sample_params(), num_paths=5000, seed=99)
        elapsed = time.perf_counter() - start
        if elapsed > 240:
            raise AssertionError(f"5000 paths took {elapsed:.1f}s (target <240s)")
        return f"5000 paths in {elapsed:.1f}s"

    _run_check(report, "Phase 4", "Monte Carlo performance (5000 paths)", check_mc_performance)
    if report.results[-1].status == "fail" and "SKIP" in report.results[-1].detail:
        report.results[-1].status = "skip"
        report.results[-1].detail = (
            "Deferred — set REGRESSION_MC_PERF=1 to run 5000-path wall-clock check"
        )

    # --- Phase 5: LLM catalog + adviser + polish ---
    def check_field_catalog_llm():
        from config.model_fields import FIELDS_BY_KEY

        health = FIELDS_BY_KEY["health_insurance_post_retire"]
        assert health.supports_llm_research()
        inflation = FIELDS_BY_KEY["inflation_rate"]
        assert not inflation.supports_llm_research()
        return "LLM eligibility rules OK"

    _run_check(report, "Phase 5", "Field catalog LLM rules", check_field_catalog_llm)

    def check_field_choice_menus():
        from config.model_fields import ALL_FIELDS, field_uses_choice_menu

        choice_fields = [field.key for field in ALL_FIELDS if field_uses_choice_menu(field)]
        assert "ss_tax_method" in choice_fields
        assert "use_birth_year_rmd" in choice_fields
        assert "state_of_residence" in choice_fields
        assert "ltc_mode" in choice_fields
        assert len(choice_fields) >= 25
        return f"{len(choice_fields)} choice-menu fields"

    _run_check(report, "GUI", "Field choice menus", check_field_choice_menus)

    def check_assumptions_export():
        from config.assumptions_report import build_assumptions_dataframe

        df = build_assumptions_dataframe(_sample_params(), risk_tolerance="moderate")
        assert len(df) >= 20
        assert "field_key" in df.columns
        assert "source" in df.columns
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            path = tmp.name
        try:
            df.to_csv(path, index=False)
            assert Path(path).stat().st_size > 100
        finally:
            Path(path).unlink(missing_ok=True)
        return f"{len(df)} rows exportable"

    _run_check(report, "Phase 5", "Assumptions CSV export", check_assumptions_export)

    def check_csv_import():
        from providers.csv_provider import owner_totals_from_rows, parse_balance_csv

        csv_text = "account_name,balance,owner\nChecking,100000,self\nBrokerage,200000,wife\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(csv_text)
            path = f.name
        try:
            rows = parse_balance_csv(path)
            totals = owner_totals_from_rows(rows)
            assert totals == {"self": 100_000.0, "wife": 200_000.0}
        finally:
            Path(path).unlink(missing_ok=True)
        return "CSV parse OK"

    _run_check(report, "Phase 5", "CSV balance import", check_csv_import)

    def check_adviser_engine():
        from adviser.advisor_engine import analyze
        from simulation.monte_carlo import run_simulation

        output = run_simulation(_sample_params(), simulation_mode="both", num_paths=200, seed=1)
        adv = analyze(output=output, params=_sample_params(), risk_tolerance="moderate")
        assert adv.goal_status
        assert adv.disclaimer
        assert adv.target_success_rate == 0.80
        return f"goal: {adv.goal_status[:60]}..."

    _run_check(report, "Phase 5", "Adviser engine", check_adviser_engine)

    def check_llm_settings_module():
        from settings import load_llm_settings

        settings = load_llm_settings()
        assert settings.provider in {"none", "openai", "anthropic", "xai"}
        return f"LLM provider={settings.provider}"

    _run_check(report, "Phase 5", "LLM settings load", check_llm_settings_module)

    def check_llm_keychain_module():
        from security.llm_keychain import VALID_PROVIDERS, has_api_key

        configured = [p for p in sorted(VALID_PROVIDERS) if has_api_key(p)]
        return f"configured LLM keys: {configured or 'none'}"

    _run_check(report, "Phase 5", "LLM keychain module", check_llm_keychain_module)

    # --- GUI smoke (headless build) ---
    def check_gui_build():
        import tkinter as tk
        from gui import RetirementGUI

        root = tk.Tk()
        root.withdraw()
        try:
            app = RetirementGUI(root)
            params = app._collect_simulation_params()
            assert params["mode"] in ("joint", "separate")
            assert "self" in params["persons"]
            assert "annual_contributions" in params["persons"]["self"]
            root.update_idletasks()
        finally:
            root.destroy()
        return "GUI builds and collects params"

    _run_check(report, "GUI", "GUI headless build", check_gui_build)

    def check_chart_formatters():
        from gui import _dollar_axis_formatter, _format_balance

        assert "$" in _format_balance(1_500_000)
        assert _dollar_axis_formatter(2_500_000, 0) == "$2.5M"
        return "dollar formatters OK"

    _run_check(report, "GUI", "Chart dollar formatters", check_chart_formatters)

    def check_planning_snapshot_params():
        from app.core.planning_snapshot import PlanningSnapshot

        snapshot = PlanningSnapshot()
        params = snapshot.to_simulation_params()
        assert params["mode"] == "joint"
        assert "annual_contributions" in params["persons"]["self"]
        roundtrip = PlanningSnapshot.from_dict(snapshot.to_dict())
        assert roundtrip.shared == snapshot.shared
        return "PlanningSnapshot params OK"

    _run_check(report, "GUI", "PlanningSnapshot params", check_planning_snapshot_params)

    def check_planning_session_core():
        from pathlib import Path

        from app.core.presentation import PlanningSession
        from app.core.presentation.field_editor import FieldUpdate, apply_updates
        from config.model_fields import FIELDS_BY_KEY

        session = PlanningSession()
        screen = session.get_guided_screen(0)
        assert screen["stepId"] == "setup"
        assert screen["fields"][0]["key"] == "household_mode"

        session.persist_setup("separate", "TX")
        assert session.snapshot.household_mode == "separate"
        assert session.snapshot.assumption_state == "TX"

        timeline = session.get_guided_screen(1)
        assert timeline["stepId"] == "timeline"
        assert len(timeline["fields"]) > 0
        assert timeline["fields"][0].get("key")

        age_field = FIELDS_BY_KEY["current_age"]
        apply_updates(session.snapshot, [FieldUpdate("current_age", "59", person="self", scope="person")])
        assert session.snapshot.get_raw(age_field, person="self") == "59"

        from app.core.presentation.field_editor import resolve_field

        shared_le = resolve_field("life_expectancy", scope="shared")
        assert shared_le is not None and shared_le.scope == "shared"
        errors = apply_updates(
            session.snapshot,
            [FieldUpdate("life_expectancy", "96", scope="shared")],
        )
        assert not errors
        assert session.snapshot.shared["life_expectancy"] == "96"

        core_root = Path(__file__).resolve().parents[1] / "app" / "core" / "presentation"
        for py in core_root.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            assert "import tkinter" not in text
            assert "from gui" not in text

        return "PlanningSession + core isolation OK"

    _run_check(report, "GUI", "PlanningSession core layer", check_planning_session_core)

    def check_web_guided_interface():
        from pathlib import Path

        from app.core.presentation import PlanningSession
        from app.core.presentation.field_editor import control_type_for, resolve_field
        from gui_web.api import WebAppApi

        js = (Path(__file__).resolve().parents[1] / "design" / "mockups" / "live-app.js").read_text(
            encoding="utf-8"
        )
        required_bindings = (
            "data-back",
            "data-continue",
            "data-skip",
            "data-run",
            "data-help-open",
            "data-connect-plaid",
            "data-manage-accounts",
            "data-refresh-expenses",
            "connect-actions",
            "connect-btn-row",
            "data-import-balances",
            "data-llm-run",
            "data-results-overlay",
            "data-expert-nav",
            "data-expert-scope",
            "data-live-action",
            "showBalanceReviewModal",
            "data-disc-inst",
            "disconnect_plaid_item",
            "showExpenseReviewModal",
            "showContributionReviewModal",
            "persist_step_draft",
            "persist_simulation_mode",
            "run_llm_research",
            "prepare_run_plan",
            "set_expert_scope",
            "apply_balances_review",
            "refresh_expenses_review",
            "refresh_market_data",
            "fanChartSvg",
            "downloadCsv",
            "apply_what_if",
            "adviser",
            "last_saved_at",
            "save-hint",
            "data-completeness-open",
            "data-journey-step",
            "data-review-jump",
            "showMissingModal",
            "showRunPlanBlockedModal",
            "showCompletenessModal",
            "bindMissingJumpHandlers",
            "journeyRailHtml",
            "completenessRingHtml",
            "updateCompletenessChrome",
            "completenessRingMetrics",
            "completenessGroupsHtml",
            "completeness-groups",
            "journey-rail",
            "completeness-btn",
            "set_sub_step",
            "microTotal",
            "micro-head",
            "stepIntroHtml",
            "microBackDisabled",
            "data-expert-search",
            "expertFieldHtml",
            "bindExpertSearch",
            "data-jump-guided-step",
            "jump_to_guided_step",
            "group-guided-link",
        )
        for token in required_bindings:
            assert token in js, f"live-app.js missing {token}"

        picker_src = Path(__file__).resolve().parents[1] / "gui_web" / "file_picker.py"
        picker_text = picker_src.read_text(encoding="utf-8")
        assert "from gui_web.tk_bridge import" not in picker_text, (
            "file_picker must not double-wrap dialogs"
        )

        api = WebAppApi()
        for name in (
            "get_state",
            "get_guided_screen",
            "persist_step_values",
            "persist_step_draft",
            "persist_simulation_mode",
            "connect_plaid",
            "import_balances",
            "run_plan",
            "get_llm_panel",
            "run_llm_research",
            "apply_llm_research",
            "get_expert_screen",
            "get_expert_nav",
            "prepare_run_plan",
            "get_simulation_results",
            "apply_import_balances",
            "set_expert_scope",
            "apply_balances_review",
            "refresh_balances_manage",
            "disconnect_plaid_item",
            "refresh_expenses_review",
            "apply_expenses_review",
            "refresh_contributions_review",
            "apply_contributions_review",
            "refresh_market_data",
            "apply_what_if",
            "set_sub_step",
            "jump_to_guided_step",
            "import_document",
            "apply_document_import",
        ):
            assert hasattr(api, name), f"WebAppApi missing {name}"

        session = PlanningSession()
        total = session.shell_state()["total_steps"]
        supported_types = {"radio", "select", "narrow", "year", "medium", "dollar", "more"}
        layouts_seen = set()

        for idx in range(total):
            screen = session.get_guided_screen(idx)
            layouts_seen.add(screen.get("layout", "fields"))
            for field in screen.get("fields", []):
                assert field["type"] in supported_types, (
                    f"step {screen['stepId']}: unsupported field type {field['type']}"
                )

        assert "connect" in layouts_seen
        assert "import" in layouts_seen
        assert "ai" in layouts_seen
        assert "review" in layouts_seen

        ai_screen = session.get_guided_screen(
            next(i for i in range(total) if session.get_guided_screen(i)["stepId"] == "llm_assist")
        )
        assert ai_screen["title"] == "AI Estimate"
        assert "llm" in ai_screen and ai_screen["llm"].get("fields")

        expert = session.get_expert_screen("llm")
        assert expert["layout"] == "llm" and expert.get("llm")
        assert expert["title"] == "AI Estimate"

        assumptions = session.get_expert_screen("assumptions")
        assert assumptions["layout"] == "assumptions"
        assert "scope_options" in assumptions and "groups" in assumptions
        session.set_expert_scope("wife")
        wife_screen = session.get_expert_screen("assumptions")
        assert wife_screen.get("scope") == "wife"

        from app.core.presentation.live_data_ops import (
            owner_totals_from_balance_rows,
            owner_totals_from_line_items,
            serialize_balance_review,
            serialize_connected_institutions,
        )
        from services.data_service import DataService, MappedAccount, RefreshSnapshot

        sample = RefreshSnapshot(
            as_of="2026-01-01T00:00:00Z",
            accounts=[
                MappedAccount(
                    account_id="a1",
                    name="Brokerage",
                    institution="Test Bank",
                    type="investment",
                    subtype=None,
                    mask="1234",
                    balance=100_000,
                    currency="USD",
                    balance_source="plaid",
                    owner="self",
                    include_in_assets=True,
                    item_id="item-test-1",
                )
            ],
            owner_totals={"self": 100_000, "wife": 0},
        )
        review = serialize_balance_review(sample, mode="refresh")
        assert review["rows"] and review["totals"]["self"] == 100_000
        manage_review = serialize_balance_review(sample, mode="manage", service=DataService())
        assert "institutions" in manage_review
        assert manage_review.get("institutions_note")
        totals = owner_totals_from_balance_rows(review["rows"])
        assert totals["self"] == 100_000
        line_totals = owner_totals_from_line_items(
            [{"included": True, "owner": "self", "annual_amount": 12_000}],
            "joint",
        )
        assert line_totals["self"] == 12_000

        session.go_to_step(1)
        draft = session.persist_step_values(
            [{"key": "birth_year", "value": "", "person": "self", "scope": "person"}],
            validate=False,
        )
        assert draft["ok"]

        session.go_to_step(total - 1)
        for label in ("Deterministic", "Monte Carlo", "Both"):
            assert session.persist_simulation_mode(label)["ok"]

        return f"{total} steps, layouts={sorted(layouts_seen)}"

    _run_check(report, "GUI", "Web guided interface coverage", check_web_guided_interface)

    def check_plan_completeness_ui():
        from app.core.planning_snapshot import blank_planning_snapshot
        from app.core.presentation import PlanningSession
        from app.core.presentation.plan_completeness import (
            build_journey_rail,
            compute_completeness,
            mark_field_touched,
            run_plan_readiness,
        )
        from config.model_fields import FIELDS_BY_KEY

        session = PlanningSession()
        state = session.shell_state()
        assert "completeness" in state
        assert "journey_rail" in state

        comp = compute_completeness(session.snapshot, session._workflow)
        readiness = session.get_completeness()
        assert readiness["percent"] == comp["percent"]
        assert readiness["missing_count"] == comp["missing_count"]
        assert "invalid_count" in readiness
        assert isinstance(comp["percent"], int)
        assert 0 <= comp["percent"] <= 100
        assert isinstance(comp["groups"], list) and comp["groups"]
        assert comp["missing_count"] == len(comp["missing"])
        assert all(
            key in group
            for group in comp["groups"]
            for key in ("filled", "total", "missing_count", "jump_step_index")
        )
        assert "ready" in comp

        prep_ready = session.prepare_run_plan()
        blank = blank_planning_snapshot()
        blank_session = PlanningSession.__new__(PlanningSession)
        blank_session.snapshot = blank
        blank_session._workflow = session._workflow
        blank_session._simulation = session._simulation
        prep_blocked = blank_session.prepare_run_plan()
        assert prep_ready.get("ready") in (True, False)
        assert prep_blocked["ready"] is False
        assert prep_blocked["blocked"] is True
        assert prep_blocked["completeness"]["missing_count"] > 0
        assert blank_session.run_plan_web()["blocked"] is True

        readiness = run_plan_readiness(session.snapshot, session._workflow)
        if readiness["missing_count"]:
            assert readiness["open_sections"]
            assert readiness["open_sections"][0]["fields"]

        rail = build_journey_rail(session._workflow, session.snapshot, current_index=state["step_index"])
        assert rail == state["journey_rail"]
        assert rail and rail[0].get("journey") and rail[0].get("steps")

        total = state["total_steps"]
        review = session.get_guided_screen(total - 1)
        assert review["layout"] == "review"
        for card in review["summary"]:
            assert card.get("step_id"), f"review card missing step_id: {card}"

        blank = blank_planning_snapshot()
        blank_comp = compute_completeness(blank, session._workflow)
        assert blank_comp["percent"] == 0

        assets = FIELDS_BY_KEY["initial_assets"]
        mark_field_touched(blank, assets, person="self")
        blank.persons["self"]["initial_assets"] = "500000"
        touched_comp = compute_completeness(blank, session._workflow)
        assert touched_comp["percent"] > blank_comp["percent"]

        ss = FIELDS_BY_KEY["ss_amount"]
        mark_field_touched(blank, ss, person="self")
        blank.persons["self"]["ss_amount"] = "0"
        zero_comp = compute_completeness(blank, session._workflow)
        self_ss_missing = [
            row
            for row in zero_comp["missing"]
            if row["field_key"] == "ss_amount" and row.get("person") == "self"
        ]
        assert not self_ss_missing

        draft = session._persist_step_draft(
            [{"key": "annual_earned_income", "value": "120000", "person": "self"}]
        )
        assert draft["ok"]
        live = session.get_completeness()
        assert live["percent"] >= comp["percent"]

        mark_field_touched(blank, FIELDS_BY_KEY["current_age"], person="self")
        blank.persons["self"]["current_age"] = "200"
        invalid_age = run_plan_readiness(blank, session._workflow)
        assert invalid_age["invalid_count"] >= 1
        assert not invalid_age["ready"]
        assert any(row["field_key"] == "current_age" for row in invalid_age["invalid"])

        timeline_idx = next(
            i
            for i in range(total)
            if session.get_guided_screen(i)["stepId"] == "timeline"
        )
        blank_session = PlanningSession.__new__(PlanningSession)
        blank_session.snapshot = blank_planning_snapshot()
        blank_session._workflow = session._workflow
        blank_session._simulation = session._simulation
        blank_session._step_index = timeline_idx
        blocked = blank_session.persist_step_values(
            [{"key": "current_age", "value": "0", "person": "self", "scope": "person"}],
            validate=True,
        )
        assert not blocked["ok"]
        assert blocked.get("field_errors")

        return f"completeness {comp['percent']}%, {comp['missing_count']} missing, {len(rail)} journey blocks"

    _run_check(report, "GUI", "Plan completeness + journey rail", check_plan_completeness_ui)

    def check_field_validation():
        from app.core.planning_snapshot import blank_planning_snapshot
        from app.core.presentation import PlanningSession
        from app.core.presentation.field_validation import (
            validate_field_raw,
            validate_guided_step,
            validate_updates,
        )
        from config.model_fields import FIELDS_BY_KEY

        age = FIELDS_BY_KEY["current_age"]
        assert validate_field_raw(age, "0", person="self") == "Age must be at least 1"
        assert validate_field_raw(age, "45", person="self") is None
        assert validate_field_raw(age, "abc", person="self") == "Enter a whole number"
        assert validate_field_raw(age, "", person="self", required=True) == "This field is required"

        ss = FIELDS_BY_KEY["ss_amount"]
        assert validate_field_raw(ss, "0", person="self") is None

        session = PlanningSession()
        timeline_idx = next(
            i
            for i in range(session.shell_state()["total_steps"])
            if session.get_guided_screen(i)["stepId"] == "timeline"
        )
        blank = blank_planning_snapshot()
        step_check = validate_guided_step(
            blank,
            "timeline",
            [{"key": "current_age", "value": "0", "person": "self"}],
            required=True,
        )
        assert not step_check["ok"]
        assert any(row["key"] == "current_age" for row in step_check["field_errors"])

        separate_blank = blank_planning_snapshot()
        separate_blank.household_mode = "separate"
        separate_blank.shared["marriage_year"] = ""
        timeline_ok = validate_guided_step(
            separate_blank,
            "timeline",
            [
                {"key": "current_age", "value": "55", "person": "self"},
                {"key": "life_expectancy", "value": "95"},
            ],
            required=True,
        )
        assert timeline_ok["ok"]
        assert not any(row["key"] == "retirement_age" for row in timeline_ok["field_errors"])

        joint_blank = blank_planning_snapshot()
        joint_blank.household_mode = "joint"
        joint_ages = [
            {"key": "current_age", "value": "55", "person": "self"},
            {"key": "current_age", "value": "53", "person": "wife"},
            {"key": "life_expectancy", "value": "95"},
        ]
        marriage_required = validate_guided_step(
            joint_blank,
            "timeline",
            joint_ages,
            required=True,
        )
        assert not marriage_required["ok"]
        assert any(row["key"] == "already_married" for row in marriage_required["field_errors"])

        already_married = validate_guided_step(
            joint_blank,
            "timeline",
            [*joint_ages, {"key": "already_married", "value": "yes"}],
            required=True,
        )
        assert already_married["ok"], already_married["errors"]

        from datetime import date as _date

        planned_year = str(_date.today().year + 1)
        marriage_ok = validate_guided_step(
            joint_blank,
            "timeline",
            [
                *joint_ages,
                {"key": "already_married", "value": "no"},
                {"key": "marriage_year", "value": planned_year},
            ],
            required=True,
        )
        assert marriage_ok["ok"], marriage_ok["errors"]

        past_marriage = validate_guided_step(
            joint_blank,
            "timeline",
            [
                *joint_ages,
                {"key": "already_married", "value": "no"},
                {"key": "marriage_year", "value": "2010"},
            ],
            required=True,
        )
        assert not past_marriage["ok"]
        assert any(row["key"] == "marriage_year" for row in past_marriage["field_errors"])

        session._persist = lambda: None
        session.go_to_step(timeline_idx)
        good = session.persist_step_values(
            [
                {"key": "current_age", "value": "55", "person": "self", "scope": "person"},
                {"key": "life_expectancy", "value": "95", "scope": "shared"},
                {"key": "already_married", "value": "yes", "scope": "shared"},
            ],
            validate=True,
        )
        assert good["ok"], good
        assert session.snapshot.shared["filing_status"] == "mfj"
        assert not str(session.snapshot.shared.get("marriage_year") or "").strip()

        income_idx = next(
            i
            for i in range(session.shell_state()["total_steps"])
            if session.get_guided_screen(i)["stepId"] == "income_self"
        )
        session.go_to_step(income_idx)
        bad_retire = session.persist_step_values(
            [
                {"key": "retirement_age", "value": "50", "person": "self", "scope": "person"},
            ],
            validate=True,
        )
        assert not bad_retire["ok"]
        assert any(
            row["key"] == "retirement_age" and "current age" in row["message"].lower()
            for row in bad_retire.get("field_errors", [])
        )

        bad_age = validate_updates(
            [{"key": "current_age", "value": "abc", "person": "self", "scope": "person"}],
            required=False,
        )
        assert bad_age and bad_age[0]["message"] == "Enter a whole number"

        from datetime import date

        import math

        from app.core.presentation.field_derived import (
            apply_derived_fields,
            birth_year_from_age,
            current_age_from_birth_year,
            derived_birth_year_for_person,
            derived_initial_assets_for_person,
        )
        from model.ss import full_retirement_age_years

        assert birth_year_from_age(40) == date.today().year - 40
        derive_blank = blank_planning_snapshot()
        derive_blank.persons["self"]["current_age"] = "55"
        apply_derived_fields(derive_blank)
        assert derive_blank.persons["self"]["birth_year"] == str(date.today().year - 55)
        assert derived_birth_year_for_person(
            derive_blank,
            "self",
            updates=[{"key": "current_age", "value": "60", "person": "self"}],
        ) == date.today().year - 60

        birth_first = blank_planning_snapshot()
        birth_first.persons["self"]["birth_year"] = "1980"
        apply_derived_fields(birth_first)
        assert birth_first.persons["self"]["current_age"] == str(current_age_from_birth_year(1980))

        buckets = blank_planning_snapshot()
        buckets.persons["self"]["initial_taxable_assets"] = "100000"
        buckets.persons["self"]["initial_traditional_ira"] = "200000"
        buckets.persons["self"]["initial_roth_ira"] = "50000"
        buckets.shared["roth_portfolio_pct"] = "0"
        apply_derived_fields(buckets)
        assert derived_initial_assets_for_person(buckets, "self") == 350000.0
        assert buckets.persons["self"]["initial_assets"] == "350000"
        # Roth % = 50k / 350k ≈ 14.2857%
        from app.core.presentation.field_derived import derived_roth_portfolio_pct

        assert abs(derived_roth_portfolio_pct(buckets) - (50_000 / 250_000)) < 1e-9
        assert abs(float(buckets.shared["roth_portfolio_pct"]) - (50_000 / 250_000 * 100)) < 1e-6

        medigap = blank_planning_snapshot()
        medigap.persons["self"]["medigap_plan"] = "plan_g"
        apply_derived_fields(medigap)
        assert medigap.persons["self"]["medigap_monthly"] == "164"

        aca = blank_planning_snapshot()
        aca.shared["aca_benchmark_monthly_age_21"] = "500"
        apply_derived_fields(aca)
        assert aca.shared["aca_slcsp_monthly_age_21"] == "500"
        assert aca.shared["aca_selected_plan_monthly_age_21"] == "500"

        ltc = blank_planning_snapshot()
        ltc.persons["self"]["ltc_mode"] = "scenario"
        ltc.persons["self"]["retirement_age"] = "62"
        apply_derived_fields(ltc)
        assert ltc.persons["self"]["ltc_start_age"] == "62"

        ss_age = blank_planning_snapshot()
        ss_age.persons["self"]["current_age"] = "66"
        apply_derived_fields(ss_age)
        birth_year = int(ss_age.persons["self"]["birth_year"])
        expected_fra = int(math.ceil(full_retirement_age_years(birth_year) - 1e-9))
        assert ss_age.persons["self"]["ss_start_age"] == str(expected_fra)

        return "age/rate validation + guided step gate + derived fields"

    _run_check(report, "GUI", "Field validation", check_field_validation)

    def check_micro_screens():
        from app.core.presentation import PlanningSession
        from app.core.presentation.micro_screens import (
            fields_for_micro_screen,
            micro_screen_count,
            validate_micro_screen_coverage,
        )

        for step_id in ("healthcare_self", "healthcare_wife", "tax_benefits", "tax_wife"):
            errors = validate_micro_screen_coverage(step_id)
            assert not errors, "; ".join(errors)
            assert micro_screen_count(step_id) >= 2

        session = PlanningSession()
        hc_index = next(
            i
            for i in range(session.shell_state()["total_steps"])
            if session.get_guided_screen(i)["stepId"] == "healthcare_self"
        )
        session.go_to_step(hc_index)
        first = session.get_guided_screen()
        assert first.get("microTotal", 1) >= 2
        assert first.get("microStep") == 1
        assert len(first.get("fields", [])) <= 8

        session.set_sub_step(1)
        second = session.get_guided_screen()
        assert second.get("microStep") == 2
        assert second["fields"]

        wizard = session.snapshot.wizard
        assert "sub_step_positions" in wizard.to_dict()
        restored = type(wizard).from_dict(wizard.to_dict())
        assert restored.sub_step_positions == wizard.sub_step_positions

        return (
            f"healthcare_self {micro_screen_count('healthcare_self')} parts, "
            f"tax_benefits {micro_screen_count('tax_benefits')} parts"
        )

    _run_check(report, "GUI", "Guided micro-screens", check_micro_screens)

    def check_expert_field_search():
        from pathlib import Path

        from app.core.presentation import PlanningSession
        from app.core.presentation.field_search import (
            filter_assumption_groups,
            filter_assumption_sections,
            matches_field_model,
        )
        from app.core.presentation.catalog_sections import assumption_sections
        from gui.shared.field_search import normalize_query
        from gui_web.api import WebAppApi

        assert Path("gui/shared/field_search.py").is_file()

        sections = assumption_sections(scope="shared")
        assert sections and len(sections[0]) == 3
        title, step_id, fields = sections[0]
        assert title and step_id and fields

        filtered = filter_assumption_sections(sections, "inflation")
        assert filtered and any(f.key == "inflation_rate" for _, _, fs in filtered for f in fs)
        assert matches_field_model(fields[0], fields[0].key)

        session = PlanningSession()
        screen = session.get_expert_screen("assumptions")
        assert screen.get("guided_step_id") == "timeline"
        groups = screen.get("groups") or []
        assert groups and groups[0].get("guided_step_id")
        assert groups[0]["fields"][0].get("searchText")

        narrowed = filter_assumption_groups(groups, "social security")
        assert narrowed and any("ss" in f["key"] for f in narrowed[0]["fields"])

        api = WebAppApi()
        res = api.jump_to_guided_step("market")
        assert res["ok"] and api.get_state()["presentation_mode"] == "guided"
        assert normalize_query("  Tax  ") == "tax"

        return f"{len(groups)} assumption groups, search OK"

    _run_check(report, "GUI", "Expert field search + guided jump", check_expert_field_search)

    def check_results_ux_polish():
        from app.core.presentation.data_sources_summary import build_data_sources_summary
        from app.core.presentation.simulation_results import results_tab_label

        assert results_tab_label("joint") == "Household"
        assert results_tab_label("self") == "You"
        assert results_tab_label("wife") == "Spouse"
        assert results_tab_label("scenarios") == "Scenarios"

        from app.core.presentation import PlanningSession

        session = PlanningSession()
        ds = build_data_sources_summary(session.snapshot)
        assert "summary" in ds
        state = session.shell_state()
        assert state["data_sources"]["summary"] == ds["summary"]

        js = (Path(__file__).resolve().parents[1] / "design" / "mockups" / "live-app.js").read_text(
            encoding="utf-8"
        )
        for token in ("dataSourcesHint", "data-sources-hint", "Your plan results", "simulationModeLabel"):
            assert token in js, f"live-app.js missing {token}"

        return f"tab labels OK, data sources: {ds['summary']}"

    _run_check(report, "GUI", "Results UX polish + data sources", check_results_ux_polish)

    def check_document_import_pipeline():
        from pathlib import Path

        from app.core.document_ingestion import ingest_document
        from app.core.document_ingestion.types import DocumentType
        from app.core.presentation import PlanningSession
        from app.core.planning_snapshot import PlanningSnapshot
        from gui_web.api import WebAppApi
        from providers.document_extractors.tax_return_json import extract_tax_return_suggestions

        example = (
            Path(__file__).resolve().parents[1]
            / "validation"
            / "examples"
            / "tax_return_2024_mfj_wages.json"
        )
        assert example.is_file()

        session = PlanningSession()
        result = ingest_document(session.snapshot, str(example))
        assert result["ok"], result.get("message")
        assert result["document_type"] == DocumentType.TAX_RETURN_JSON.value
        rows = result["rows"]
        assert rows and any(r.get("field_key") == "annual_earned_income" for r in rows)

        apply_rows = [{**r, "apply": True} for r in rows if r.get("field_key") == "annual_earned_income"]
        applied = session.apply_document_import(
            apply_rows,
            document_type=result["document_type"],
            document_label=result.get("document_label", ""),
        )
        assert applied["ok"] and applied["applied"] >= 1
        params = session.snapshot.to_simulation_params()
        assert params["persons"]["self"]["annual_earned_income"] == 100_000
        assert session.snapshot.import_history
        assert len(session.snapshot.import_history) == 1

        # Second tax-return apply replaces history (does not stack)
        apply_rows2 = [
            {
                **r,
                "apply": True,
                "raw_value": "200000",
                "suggested": "$200,000",
            }
            for r in rows
            if r.get("field_key") == "annual_earned_income"
        ]
        applied2 = session.apply_document_import(
            apply_rows2,
            document_type=result["document_type"],
            document_label="second_return.json",
        )
        assert applied2["ok"]
        assert applied2.get("replaced_prior_tax_returns", 0) >= 1
        assert len(session.snapshot.import_history) == 1
        assert session.snapshot.import_history[0]["label"] == "second_return.json"
        params2 = session.snapshot.to_simulation_params()
        assert params2["persons"]["self"]["annual_earned_income"] == 200_000

        restored = PlanningSnapshot.from_dict(session.snapshot.to_dict())
        assert restored.import_history == session.snapshot.import_history

        api = WebAppApi()
        assert hasattr(api, "import_document")
        assert hasattr(api, "apply_document_import")

        payload = __import__("json").loads(example.read_text(encoding="utf-8"))
        suggestions = extract_tax_return_suggestions(payload)
        assert len(suggestions) >= 3

        js = (Path(__file__).resolve().parents[1] / "design" / "mockups" / "live-app.js").read_text(
            encoding="utf-8"
        )
        for token in (
            "importBodyHtml",
            "showDocumentImportModal",
            "data-import-document",
            "apply_document_import",
            "Replace & apply",
        ):
            assert token in js, f"live-app.js missing {token}"

        workflow = session._workflow
        assert "import_documents" in workflow.step_ids()

        from providers.document_extractors.tax_return_pdf import parse_1040_text_to_payload

        sample_1040 = """
        Form 1040 2024 U.S. Individual Income Tax Return
        1z Wages, tips, other compensation . . . . . . . . . . . . . . . . 88000
        11 Adjusted gross income . . . . . . . . . . . . . . . . . . . . . 85000
        """
        pdf_payload = parse_1040_text_to_payload(sample_1040, label="sample.pdf")
        assert pdf_payload["income"]["wages"] == 88_000
        assert pdf_payload["form_1040"]["agi"] == 85_000
        # TurboTax-style junk numbers (copy # / box 1) must not become wages
        trap = (
            "Form 1040 2024\n"
            "1z Wages, salaries, tips, etc. Attach Form(s) W-2 1 185,000\n"
            "Adjusted Gross Income                   $    312,450.00\n"
        )
        trap_payload = parse_1040_text_to_payload(trap, label="trap.pdf")
        assert trap_payload["income"]["wages"] == 185_000, trap_payload
        assert trap_payload["form_1040"]["agi"] == 312_450, trap_payload
        # TurboTax summary + trailing amount column (labels separate from $)
        tt_style = """
        File by Mail Instructions for your 2025 Federal Tax Return
        BAA REV 03/10/26 TTW
        2025 | Adjusted Gross Income                   $    259,300.00
        Federal | Taxable Income                          $    202,438.00
        Tax | Total Tax                               $     42,162.00
        Return | Total Payments/Credits                  $     40,338.00
        Summary | Payment Due                             $      1,824.00
        | Effective Tax Rate                               15.47%
        Form1040 2025U.S. Individual Income Tax Return
        1 a Total amount from Form(s) W-2, box 1 1a
        z Add lines 1a through 1h 1z
        11a Subtract line 10 from line 9. This is your adjusted gross income 11a
        227,540.
        0.
        227,540.
        259,300.
        259,300.
        """
        tt_payload = parse_1040_text_to_payload(tt_style, label="tt.pdf")
        assert abs(tt_payload["form_1040"]["agi"] - 259_300) < 1, tt_payload
        assert abs(tt_payload["form_1040"]["taxable_income"] - 202_438) < 1, tt_payload
        assert abs(tt_payload["form_1040"]["total_tax"] - 42_162) < 1, tt_payload
        assert abs(tt_payload["income"]["wages"] - 227_540) < 1, tt_payload
        pdf_suggestions = extract_tax_return_suggestions(pdf_payload)
        assert any(s.field_key == "annual_earned_income" for s in pdf_suggestions)

        from app.core.document_ingestion.types import supported_types_payload

        pdf_type = next(t for t in supported_types_payload() if t["id"] == "pdf")
        assert pdf_type["ready"] is True

        return f"tax return → {applied['applied']} field(s), {len(suggestions)} suggestions"

    _run_check(report, "GUI", "Document import pipeline", check_document_import_pipeline)

    def check_guided_documents_panel():
        from pathlib import Path

        from app.core.document_ingestion.step_documents import build_documents_panel
        from app.core.lazy_mode.manifest import load_document_manifest, required_core_documents
        from app.core.presentation import PlanningSession
        from app.core.presentation.guided_screens import build_guided_screen
        from gui_web.api import WebAppApi

        manifest = load_document_manifest()
        assert any(doc.get("id") == "form_1040" for doc in manifest.get("documents", []))
        core = required_core_documents(joint=True)
        assert len(core) >= 3

        session = PlanningSession()
        # AI Mode is the document-first path (legacy "lazy" maps to "ai")
        res = session.set_presentation_mode("lazy")
        assert res["ok"] and res["mode"] == "ai"
        res = session.set_presentation_mode("ai")
        assert res["ok"] and res["mode"] == "ai"
        screen = session.get_ai_screen(0)
        assert screen.get("mode") == "ai" or screen.get("layout") in {"lazy_section", "review", "lazy"}

        session.set_presentation_mode("guided")
        income = build_guided_screen(session.snapshot, session._workflow, index=session._workflow.index_of("income_self"))
        panel = income.get("documents_panel") or {}
        assert panel.get("has_documents")
        doc_ids = {d.get("id") for d in panel.get("documents") or []}
        assert "form_1040" in doc_ids
        form1040 = next(d for d in panel["documents"] if d["id"] == "form_1040")
        assert form1040.get("fields_other_steps") is not None  # fills later steps too

        assets = build_documents_panel(session.snapshot, step_id="assets_self")
        assert any(d.get("id") == "plaid_accounts" for d in assets.get("documents") or [])

        market = build_documents_panel(session.snapshot, step_id="market")
        assert any(d.get("id") == "market_data" for d in market.get("documents") or [])

        api = WebAppApi()
        assert hasattr(api, "get_step_documents")
        assert hasattr(api, "upload_step_document")
        assert hasattr(api, "get_ai_screen")
        step_docs = api.get_step_documents("tax_benefits")
        assert step_docs.get("documents")
        ai_screen = api.get_ai_screen(0)
        assert ai_screen.get("layout") or ai_screen.get("section_id")

        js = (Path(__file__).resolve().parents[1] / "design" / "mockups" / "live-app.js").read_text(
            encoding="utf-8"
        )
        for token in (
            "data-docs-open",
            "showDocumentsPanelModal",
            "documents_panel",
            "upload_step_document",
            "data-mode=\"guided\"",
            "data-mode=\"ai\"",
            "showParserFailureModal",
            "renderLazy",
        ):
            assert token in js, f"live-app.js missing {token}"

        field_map = Path(__file__).resolve().parents[1] / "config" / "lazy_mode" / "field_document_map.json"
        assert field_map.is_file()
        ai_map = Path(__file__).resolve().parents[1] / "config" / "ai_mode" / "field_document_sources.json"
        assert ai_map.is_file()
        plan = Path(__file__).resolve().parents[1] / "docs" / "AI_MODE_EXECUTION_PLAN.md"
        assert plan.is_file()

        from providers.document_extractors.pay_stub_pdf import extract_pay_stub_suggestions
        from providers.local_doc_ai import host_label, _parse_json_array

        sample_stub = """
        Employee Pay Stub
        Pay Period: 03/01/2026 - 03/15/2026
        Earnings Statement
        Gross Pay YTD  95,000.00
        Pre-tax 401(k)     800.00   8,000.00
        Roth 401(k)        200.00   2,000.00
        Net Pay            3,100.00
        """
        stub_rows = extract_pay_stub_suggestions(sample_stub, label="sample_stub.pdf")
        roth = next((r for r in stub_rows if r.field_key == "contribution_roth_pct"), None)
        assert roth is not None and roth.ok
        # 2000 / (8000+2000) = 20%
        assert abs(float(roth.raw_value) - 20.0) < 0.1, roth
        contrib = next((r for r in stub_rows if r.field_key == "annual_contributions"), None)
        assert contrib is not None and contrib.ok
        assert "127.0.0.1" in host_label()
        assert _parse_json_array('[{"field_key":"x","value":1}]')[0]["value"] == 1

        # my Social Security statement chart (from real SSA-7005 layout; ages 62–70)
        from providers.document_extractors.statement_pdf import extract_ssa_suggestions

        sample_ssa = """
        Your Social Security Statement
        Your full retirement age is 67, based on your date of birth.
        Personalized Monthly Retirement Benefit Estimates
        (Depending on the Age You Start)
        Age Retirement Benefits Start
        Monthly Benefit Amount
        62 $2,915
        63 $3,118
        64 $3,340
        65 $3,628
        66 $3,917
        67 $4,208
        68 $4,387
        69 $4,737
        70 $5,253
        Medicare
        You have enough credits to qualify for Medicare at age 65.
        Spouse, if benefits start at full retirement age: $4,057
        Earnings Record
        2025 $176,100 $251,039
        """
        ssa_rows = extract_ssa_suggestions(sample_ssa, person="self")
        by_key = {r.field_key: r for r in ssa_rows if r.ok and r.field_key}
        assert "ss_pia_monthly" in by_key
        assert abs(float(by_key["ss_pia_monthly"].raw_value) - 4208) < 0.5
        assert "ss_amount" in by_key
        assert abs(float(by_key["ss_amount"].raw_value) - 50496) < 1.0
        assert by_key["ss_start_age"].raw_value in {"67", "67.0"} or float(by_key["ss_start_age"].raw_value) == 67
        assert by_key["ss_benefit_mode"].raw_value == "pia"

        panel_ids = {d.get("id") for d in panel.get("documents") or []}
        assert "pay_stub" in panel_ids
        tax_docs = build_documents_panel(session.snapshot, step_id="tax_benefits")
        tax_ids = {d.get("id") for d in tax_docs.get("documents") or []}
        assert "ssa_1099" in tax_ids

        return f"AI Mode + guided docs + pay stub + SSA chart; {len(panel.get('documents') or [])} income sources"

    _run_check(report, "GUI", "Guided documents panel", check_guided_documents_panel)

    def check_ux6_app_bundle_scaffold():
        from app.bundle_paths import is_frozen, mockups_dir, project_root

        setup = (PROJECT_ROOT / "setup.py").read_text(encoding="utf-8")
        for token in ("gui_web", "webview", "MOCKUPS_DIR", "AppIcon.icns", ".env.example"):
            assert token in setup, f"setup.py missing {token}"

        build_sh = (PROJECT_ROOT / "scripts" / "build_app.sh").read_text(encoding="utf-8")
        assert "py2app" in build_sh
        assert "Retirement Planner.app" in build_sh

        assert not is_frozen()
        root = project_root()
        assert (root / "design" / "mockups" / "live-app.html").is_file()
        assert mockups_dir().name == "mockups"
        bundle_paths = (PROJECT_ROOT / "app" / "bundle_paths.py").read_text(encoding="utf-8")
        assert 'root / "mockups"' in bundle_paths

        launcher = (PROJECT_ROOT / "gui_web" / "launcher.py").read_text(encoding="utf-8")
        assert "mockups_dir" in launcher
        assert "is_frozen" in launcher

        return "setup.py + bundle_paths wired for py2app"

    _run_check(report, "GUI", "UX-6 app bundle scaffold", check_ux6_app_bundle_scaffold)

    def check_session_autosave_roundtrip():
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from app.core.planning_snapshot import PlanningSnapshot
        from app.core.presentation import PlanningSession
        from app.core.session_store import (
            load_last_results,
            load_planning_snapshot,
            load_session_ui,
            save_last_results,
            save_planning_snapshot,
            save_session_ui,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.core.session_store.app_data_dir", return_value=data_dir):
                snapshot = PlanningSnapshot(household_mode="joint")
                snapshot.shared["inflation_rate"] = "0.03"
                snapshot.persons["self"]["initial_assets"] = "750000"
                save_planning_snapshot(snapshot)
                save_session_ui(expert_section="live_data", expert_assumptions_scope="wife")
                save_last_results({"mode": "deterministic", "tabs": []})

                loaded = load_planning_snapshot()
                assert loaded is not None
                assert loaded.shared["inflation_rate"] == "0.03"
                assert loaded.persons["self"]["initial_assets"] == "750000"
                ui = load_session_ui()
                assert ui["expert_section"] == "live_data"
                assert ui["expert_assumptions_scope"] == "wife"
                results = load_last_results()
                assert results and results.get("mode") == "deterministic"

            with patch("app.core.session_store.app_data_dir", return_value=data_dir):
                session = PlanningSession.from_preferences()
                assert session.snapshot.persons["self"]["initial_assets"] == "750000"
                assert session._expert_section == "live_data"
                assert session._last_results is not None
                session.go_to_step(2)
                session.save_plan()
                assert session._last_saved_at
                assert session.shell_state()["last_saved_at"]

        return "snapshot + ui + results roundtrip"

    _run_check(report, "GUI", "Session autosave roundtrip", check_session_autosave_roundtrip)

    def check_plan_save_and_new_plan():
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from app.core.planning_snapshot import PlanningSnapshot
        from app.core.presentation import PlanningSession
        from app.core.session_store import load_planning_snapshot, load_last_results

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.core.session_store.app_data_dir", return_value=data_dir):
                session = PlanningSession()
                session.snapshot.persons["self"]["initial_assets"] = "900000"
                session.save_plan()
                assert session._plan_restored
                assert session._last_saved_at
                assert session.shell_state()["plan_restored"]

                reloaded = PlanningSession.from_preferences()
                assert reloaded._plan_restored
                assert reloaded.snapshot.persons["self"]["initial_assets"] == "900000"

                reloaded._last_results = {"mode": "deterministic"}
                reloaded.save_plan()
                assert load_last_results() is not None

                saved = load_planning_snapshot()
                assert saved is not None
                assert saved.persons["self"]["initial_assets"] == "900000"

                fresh = PlanningSession.from_preferences()
                fresh.start_new_plan()
                assert fresh.snapshot.persons["self"]["initial_assets"] == "0"
                assert fresh._last_results is None
                assert not fresh._plan_restored
                assert load_planning_snapshot().persons["self"]["initial_assets"] == "900000"

                restarted = PlanningSession.from_preferences()
                assert restarted.snapshot.persons["self"]["initial_assets"] == "900000"

                from app.core.planning_snapshot import blank_planning_snapshot

                blank = blank_planning_snapshot()
                assert blank.persons["self"]["initial_assets"] == "0"

                api_src = (PROJECT_ROOT / "gui_web" / "api.py").read_text(encoding="utf-8")
                for token in (
                    "save_plan",
                    "start_new_plan",
                    "restart_app",
                    "pdf_redact_start",
                    "pdf_redact_status",
                    "pdf_redact_save",
                    "_coerce_bool",
                ):
                    assert token in api_src
                assert "use_ai" in api_src
                # Must not silently default AI-off requests into skip_ai via bool(false) drop
                assert "skip_ai = not want_ai" in api_src or "skip_ai = not want_ai" in api_src.replace(
                    " ", ""
                )

                js = (PROJECT_ROOT / "design" / "mockups" / "live-app.js").read_text(encoding="utf-8")
                for token in (
                    "savePlanNow",
                    "startNewPlan",
                    "restartApp",
                    "showPdfRedactorModal",
                    "runPdfRedactionWithProgress",
                    "setProgress",
                    "data-progress-bar",
                    "data-start-new-plan",
                    "data-save-plan",
                    "data-restart-app",
                    "data-pdf-redactor",
                ):
                    assert token in js, f"live-app.js missing {token}"

                from providers.pdf_redactor import (
                    capabilities,
                    estimate_eta_seconds,
                    format_eta,
                    redact_pdf,
                )

                caps = capabilities()
                assert "ready" in caps
                assert "ai_available" in caps
                assert "left" in format_eta(125) or "m" in format_eta(125)
                eta = estimate_eta_seconds(elapsed=10.0, percent=25.0, page=1, pages=4, completed_pages=1)
                assert eta is not None and eta > 0
                events: list[dict] = []
                # progress callback smoke on empty path fails cleanly
                bad = redact_pdf("/nonexistent.pdf", "/tmp/out.pdf", skip_ai=True, progress=events.append)
                assert bad.get("ok") is False

        return "save + restore + blank template without wiping saved"

    _run_check(report, "GUI", "Plan save and new plan", check_plan_save_and_new_plan)

    def check_ux7_polish():
        from app.core.presentation.guided_screens import JOURNEY_LABELS, _SIM_LABELS

        assert "Deterministic" in _SIM_LABELS
        assert JOURNEY_LABELS.get("review") == "Review & run"
        assert JOURNEY_LABELS.get("income_self") == "Income & savings"

        js = (PROJECT_ROOT / "design" / "mockups" / "live-app.js").read_text(encoding="utf-8")
        for token in (
            "guidedHeaderHtml",
            "appHeaderHtml",
            "expertPanelHtml",
            "expertFooterHtml",
            "header-right",
            "chrome-dock-compact",
            "dock-bar",
            "dock-progress",
            "data-start-new-plan",
            "Plan completeness",
            "data-save-plan",
            "question-panel",
            "question-panel-scroll",
            "question-panel-head",
            "question-panel-ai",
            "llmPanelInputHtml",
            "llmSelectionHostHtml",
            "showLlmFieldsPicker",
            "Fields For AI Estimates",
            "data-llm-fields-open",
            "Unfilled",
        ):
            assert token in js, f"live-app.js missing {token}"
        assert "stepIntroHtml(screen)" in js
        assert js.index("question-panel-head") < js.index("question-panel-scroll")
        assert "plan-strip" not in js.split("function footerHtml")[1].split("function expertShellHtml")[0]

        css = (PROJECT_ROOT / "design" / "mockups" / "liquid-glass.css").read_text(encoding="utf-8")
        assert ".header-right" in css
        assert ".chrome-dock-compact" in css
        assert ".dock-bar" in css
        assert ".question-panel-ai .question-panel-llm-input" in css
        assert ".question-panel-ai .llm-context-input" in css
        assert ".llm-checklist-modal" in css
        assert ".guided-main .question-panel.content-glass" in css
        assert "max-width: none" in css
        assert ".question-panel-head .step-intro" in css
        assert ".question-panel-head .micro-head" in css

        return "journey labels, sim copy, plan strip layout"

    _run_check(report, "GUI", "UX-7 polish", check_ux7_polish)

    def check_guided_expert_fork_isolation():
        import importlib
        import sys

        for mod in list(sys.modules):
            if mod.startswith("gui.guided") or mod.startswith("gui.expert"):
                del sys.modules[mod]
        guided = importlib.import_module("gui.guided.app")
        source = open(guided.__file__, encoding="utf-8").read()
        assert "gui.expert" not in source
        return "guided does not import expert"

    _run_check(report, "GUI", "Guided/expert fork isolation", check_guided_expert_fork_isolation)

    def check_llm_research_batch_controller():
        from unittest.mock import patch

        from app.core.controllers.llm_research_controller import (
            LLMResearchBatchRequest,
            LLMResearchController,
            LLMResearchFieldSelection,
            is_field_empty,
        )
        from app.core.planning_snapshot import PlanningSnapshot
        from config.model_fields import FIELDS_BY_KEY
        from providers.assumption_research import AssumptionResearchResult, AssumptionSuggestion

        snapshot = PlanningSnapshot()
        field = FIELDS_BY_KEY["annual_contributions"]
        assert is_field_empty(snapshot, field, person="self")

        controller = LLMResearchController()
        contexts = controller.build_contexts(
            LLMResearchBatchRequest(
                snapshot=snapshot,
                selections=[LLMResearchFieldSelection(field_key=field.key, person="self")],
                extra_prompt="Bay Area",
                provider="openai",
                model="gpt-4",
            )
        )
        assert len(contexts) == 1
        assert contexts[0].extra_prompt == "Bay Area"

        fake = AssumptionSuggestion(
            field_key=field.key,
            suggested_annual_usd=12_000.0,
            confidence="medium",
            projection_method="test",
            sources=["example"],
            explanation="ok",
            provider="openai",
            model="gpt-4",
            researched_at="2026-01-01T00:00:00+00:00",
        )
        fake_results = [
            AssumptionResearchResult(field_key=field.key, person="self", suggestion=fake),
        ]
        with patch(
            "app.core.controllers.llm_research_controller.research_assumptions_batch",
            return_value=fake_results,
        ):
            result = controller.run_batch(
                LLMResearchBatchRequest(
                    snapshot=snapshot,
                    selections=[LLMResearchFieldSelection(field_key=field.key, person="self")],
                    provider="openai",
                    model="gpt-4",
                ),
                api_key="test-key",
            )
        assert result.ok
        assert result.payload.succeeded == 1
        return "LLM batch controller OK"

    _run_check(report, "GUI", "LLM batch controller", check_llm_research_batch_controller)

    def check_live_data_panel_cards():
        from gui.shared.live_data_panel import PLAID_ACTIONS, live_data_card_specs

        specs = live_data_card_specs()
        assert len(specs) == 5
        ids = {spec.card_id for spec in specs}
        assert ids == {"accounts", "balances", "expenses", "contributions", "market"}
        assert "Import CSV" not in PLAID_ACTIONS
        assert "Refresh Market" not in PLAID_ACTIONS
        compact = live_data_card_specs(compact=True)
        assert len(compact) == 5
        return "5 live data cards OK"

    _run_check(report, "GUI", "Live data panel cards", check_live_data_panel_cards)

    def check_guided_catalog_completeness():
        from app.core.guided_catalog import FIELD_GUIDED_QUESTIONS, STEP_GUIDED_INTROS, guided_question_for_field
        from app.core.ui_groups import FIELD_UI_GROUP, all_user_facing_fields_mapped, fields_for_ui_group
        from config.model_fields import ALL_FIELDS, FIELDS_BY_KEY

        assert all_user_facing_fields_mapped()
        assert len(FIELD_UI_GROUP) >= 90
        assert len(FIELD_GUIDED_QUESTIONS) >= 80
        assert "income_self" in STEP_GUIDED_INTROS

        for key in FIELD_UI_GROUP:
            assert key in FIELDS_BY_KEY, f"unknown ui_group key {key}"

        timeline = fields_for_ui_group("timeline")
        assert any(field.key == "current_age" for field in timeline)

        spending = fields_for_ui_group("spending_self")
        assert any(field.key == "expense_phase_mode" for field in spending)

        tax = fields_for_ui_group("tax_benefits")
        assert any(field.key == "ss_start_age" for field in tax)

        wife_field = FIELDS_BY_KEY["retirement_age"]
        question = guided_question_for_field(wife_field, person="wife")
        assert "spouse" in question.lower()

        return f"{len(FIELD_UI_GROUP)} ui_groups, {len(FIELD_GUIDED_QUESTIONS)} guided questions"

    _run_check(report, "GUI", "Guided catalog completeness", check_guided_catalog_completeness)

    def check_guided_screen_recommendations():
        from datetime import date

        from app.core.planning_snapshot import PlanningSnapshot, blank_planning_snapshot
        from app.core.presentation import PlanningSession
        from app.core.presentation.field_editor import field_to_spec
        from app.core.presentation.guided_screens import build_guided_screen
        from app.core.presentation.plan_completeness import compute_completeness
        from app.core.presentation.simulation_results import results_headline
        from app.core.ui_groups import fields_for_ui_group
        from app.core.workflow_definition import WorkflowDefinition
        from config.model_fields import FIELDS_BY_KEY

        housing_keys = [field.key for field in fields_for_ui_group("housing")]
        assert housing_keys[:3] == [
            "mortgage_annual",
            "mortgage_payoff_year",
            "mortgage_payoff_month",
        ]
        assert "property_tax_annual" in housing_keys
        assert "mortgage_annual" not in {field.key for field in fields_for_ui_group("spending_self")}
        assert "mortgage_annual" not in {field.key for field in fields_for_ui_group("spending_wife")}

        snapshot = PlanningSnapshot()
        workflow = WorkflowDefinition(household_mode="joint", include_spouse=True)
        housing = build_guided_screen(snapshot, workflow, index=workflow.index_of("housing"))
        housing_text = " ".join(
            f"{field['label']} {field.get('caption', '')}" for field in housing["fields"]
        ).lower()
        assert "principal and interest" in housing_text
        assert "not escrow" in housing_text
        assert "property tax" in housing_text

        timeline = build_guided_screen(snapshot, workflow, index=workflow.index_of("timeline"))
        pairs = [(field["key"], field.get("person")) for field in timeline["fields"]]
        assert ("current_age", "wife") in pairs
        married = next(field for field in timeline["fields"] if field["key"] == "already_married")
        assert married["optionLabels"] == ["Yes", "Not yet"]
        year = next(field for field in timeline["fields"] if field["key"] == "marriage_year")
        assert "plan to marry" in year["label"].lower()
        assert year["showWhen"] == {"key": "already_married", "value": "no"}

        assets = build_guided_screen(snapshot, workflow, index=workflow.index_of("assets_self"))
        sample = next(field for field in assets["fields"] if field["key"] == "initial_assets")
        assert sample["example"] is True
        assert sample["caption"].startswith("Example")
        review = build_guided_screen(snapshot, workflow, index=workflow.index_of("review"))
        example_keys = {row["field_key"] for row in review["examples"]}
        assert {"current_age", "initial_assets", "base_expenses", "annual_earned_income", "ss_amount"} <= example_keys

        filing = field_to_spec(snapshot, FIELDS_BY_KEY["filing_status"], person=None, index=0)
        assert filing["options"][filing["optionLabels"].index("Married filing jointly")] == "mfj"
        phase = field_to_spec(snapshot, FIELDS_BY_KEY["expense_phase_mode"], person=None, index=0)
        assert "Change with age" in phase["optionLabels"]
        assert "age_banded" in phase["options"]

        blank = blank_planning_snapshot()
        blank.household_mode = "joint"
        missing = compute_completeness(blank, workflow)["missing"]
        assert any(row["field_key"] == "marriage_year" and row["label"] == "Are you married?" for row in missing)
        blank.shared["filing_status"] = "mfj"
        blank.shared["marriage_year"] = ""
        married_missing = compute_completeness(blank, workflow)["missing"]
        assert not any(row["field_key"] == "marriage_year" for row in married_missing)

        session = PlanningSession(blank_planning_snapshot())
        session.snapshot.household_mode = "joint"
        session._sync_workflow()
        session._persist = lambda: None
        session.go_to_step(session._workflow.index_of("timeline"))
        planned = session.persist_step_values(
            [
                {"key": "current_age", "value": "55", "person": "self", "scope": "person"},
                {"key": "current_age", "value": "53", "person": "wife", "scope": "person"},
                {"key": "life_expectancy", "value": "95", "scope": "shared"},
                {"key": "already_married", "value": "no", "scope": "shared"},
                {"key": "marriage_year", "value": str(date.today().year + 2), "scope": "shared"},
            ],
            validate=True,
        )
        assert planned["ok"], planned
        assert session.snapshot.shared["filing_status"] == "single"
        assert session.snapshot.shared["filing_status_before_marriage"] == "single"
        assert session.snapshot.shared["filing_status_after_marriage"] == "mfj"

        headline = results_headline(
            adviser={
                "ok": True,
                "goal_status": "Below target — 40% success rate vs 80% goal.",
                "success_rate": 0.4,
                "target_success_rate": 0.8,
            },
            tabs=[{"key": "joint", "monte_carlo": {"median_depletion_age": 82, "success_rate": 0.4}}],
        )
        assert headline["title"] == "You are not on track yet"
        assert headline["tone"] == "warn"
        assert "40 of 100" in headline["lines"][0]
        assert "age 82" in headline["lines"][1]

        return "spouse age, marriage, housing, examples, headline"

    _run_check(report, "GUI", "Guided screen recommendations", check_guided_screen_recommendations)

    def check_workflow_spouse_steps():
        from app.core.workflow_definition import WorkflowDefinition

        joint = WorkflowDefinition(household_mode="joint", include_spouse=True)
        separate = WorkflowDefinition(household_mode="separate", include_spouse=False)
        joint_ids = joint.step_ids()
        separate_ids = separate.step_ids()
        assert "income_wife" in joint_ids
        assert "tax_wife" in joint_ids
        assert "income_wife" not in separate_ids
        assert "tax_wife" not in separate_ids
        assert len(separate_ids) < len(joint_ids)
        return f"joint {len(joint_ids)} steps, separate {len(separate_ids)} steps"

    _run_check(report, "GUI", "Workflow spouse steps", check_workflow_spouse_steps)

    def check_guided_timeline_field_widgets():
        import tkinter as tk
        from tkinter import ttk

        from app.core.planning_snapshot import PlanningSnapshot
        from gui.shared.app_context import AppContext
        from gui.shared.catalog_sections import fields_for_wizard_step
        from gui.shared.field_panel import render_guided_field_rows

        root = tk.Tk()
        root.withdraw()
        try:
            ctx = AppContext(root, PlanningSnapshot())
            host = ttk.Frame(root)
            fields = fields_for_wizard_step("timeline")
            assert any(field.key == "current_age" for field in fields)
            assert sum(1 for field in fields if field.key == "life_expectancy") == 1
            render_guided_field_rows(host, fields, ctx.binder, person=None)

            def _input_widgets(widget):
                found = []
                if widget.winfo_class() in ("TEntry", "TCombobox"):
                    found.append(widget)
                for child in widget.winfo_children():
                    found.extend(_input_widgets(child))
                return found

            entries = _input_widgets(host)
            assert len(entries) >= len(fields)
            assert "current_age" in ctx.binder.person_widgets["self"]
        finally:
            root.destroy()
        return "timeline guided widgets OK"

    _run_check(report, "GUI", "Guided timeline widgets", check_guided_timeline_field_widgets)

    def check_guided_wife_tax_step():
        from app.core.ui_groups import fields_for_ui_group

        tax_fields = fields_for_ui_group("tax_benefits")
        self_tax = {f.key for f in tax_fields if f.scope == "person"}
        wife_tax = {f.key for f in fields_for_ui_group("tax_wife")}
        core = {"ss_start_age", "pension_amount", "qcd_annual", "roth_conversion_mode"}
        assert core <= self_tax
        assert core <= wife_tax
        assert len(wife_tax) >= len(core)
        return f"tax self={len(self_tax)} wife={len(wife_tax)} person fields"

    _run_check(report, "GUI", "Guided wife tax step", check_guided_wife_tax_step)

    def check_simulation_cancel():
        import threading

        from app.core.controllers.simulation_controller import SimulationController, SimulationRunRequest
        from app.core.planning_snapshot import PlanningSnapshot
        from simulation.monte_carlo import DEFAULT_NUM_PATHS

        assert DEFAULT_NUM_PATHS == 1000
        cancel = threading.Event()
        snapshot = PlanningSnapshot()
        snapshot.simulation.simulation_mode = "monte_carlo"
        snapshot.simulation.num_paths = 200
        controller = SimulationController()

        def on_progress(done, total):
            if done >= 10:
                cancel.set()

        result = controller.run(
            SimulationRunRequest(snapshot=snapshot),
            cancel_event=cancel,
            on_progress=on_progress,
        )
        assert not result.ok
        assert result.title == "Simulation Cancelled"
        return "MC cancel OK"

    _run_check(report, "GUI", "Simulation cancel", check_simulation_cancel)

    # --- Optional live integrations ---
    def check_fred_configured():
        from settings import get_settings

        s = get_settings()
        if not s.fred_configured:
            raise RuntimeError("SKIP")
        return "FRED_API_KEY present"

    _run_check(report, "Phase 2", "FRED configuration", check_fred_configured)
    if report.results[-1].status == "fail" and "SKIP" in report.results[-1].detail:
        report.results[-1].status = "skip"
        report.results[-1].detail = "FRED key not configured (market refresh uses fallback)"

    def check_cached_vault():
        from services.data_service import DataService

        service = DataService()
        balance_cache = service.load_cached_snapshot()
        market_cache = service.load_cached_market_snapshot()
        parts = []
        if balance_cache:
            parts.append(f"balances as_of={balance_cache.as_of[:10]}")
        if market_cache:
            parts.append(f"market as_of={market_cache.as_of[:10]}")
        if not parts:
            raise RuntimeError("SKIP")
        return "; ".join(parts)

    _run_check(report, "Phase 1", "Cached vault data", check_cached_vault)
    if report.results[-1].status == "fail" and "SKIP" in report.results[-1].detail:
        report.results[-1].status = "skip"
        report.results[-1].detail = "No cached vault data (run Refresh in app first)"

    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regression suite for Retirement Financial Modeling",
    )
    parser.add_argument(
        "--only",
        metavar="PHASES",
        help="Comma-separated phase substrings to run (e.g. 'CPA Round 2,Phase 9')",
    )
    parser.add_argument(
        "--skip",
        metavar="PHASES",
        help="Comma-separated phase substrings to skip (e.g. 'Phase 4,Stochastic')",
    )
    parser.add_argument(
        "--changed",
        action="store_true",
        help="Run checks for phases touched by git-changed files under this project",
    )
    parser.add_argument(
        "--list-phases",
        action="store_true",
        help="Print known phase labels and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.list_phases:
        for phase in KNOWN_PHASES:
            print(phase)
        return 0

    title = "Retirement Financial Modeling — Regression Pass"
    only = args.only
    skip = args.skip
    if args.changed:
        changed_files = _git_changed_project_files()
        if not changed_files:
            print("No git changes under Retirement_Financial_Modeling/ — running Core only.")
            only = "Core"
        else:
            phase_patterns = phases_for_changed_files(changed_files)
            only = ",".join(phase_patterns)
            title += " (git --changed)"
            print("Changed files:")
            for path in sorted(changed_files):
                print(f"  {path}")
            print(f"Selected phases: {only}")

    filt = configure_regression_filter(only=only, skip=skip)
    if filt.only_patterns or filt.skip_patterns:
        if filt.only_patterns:
            print(f"Filter — only: {', '.join(filt.only_patterns)}")
        if filt.skip_patterns:
            print(f"Filter — skip: {', '.join(filt.skip_patterns)}")

    print(title)
    print("-" * 60)

    report = run_regression()
    for r in report.results:
        icon = {"pass": "✓", "fail": "✗", "skip": "○"}[r.status]
        timing = f" ({r.duration_ms:.0f}ms)" if r.duration_ms else ""
        print(f"  {icon} [{r.phase}] {r.name}{timing}")
        if r.status != "pass":
            print(f"      → {r.detail}")

    print(report.summary())

    failed = sum(1 for r in report.results if r.status == "fail")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
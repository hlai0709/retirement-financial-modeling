"""Golden-file tax scenario validation (Phase 8)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from config.policy.tables import policy_for_projection
from model.aca import AcaPolicy, applicable_percentage, compute_aca_household_cost
from model.medicare import MedicarePolicy, compute_medicare_cost, irmaa_monthly_surcharges
from model.payroll import employee_fica
from model.rmd import compute_rmd
from model.ss import monthly_benefit_from_pia, pia_from_aime
from model.state_taxes import compute_state_income_tax, state_profile
from model.taxes import (
    compute_niit,
    compute_retirement_tax_detail,
    federal_income_tax,
    federal_tax_with_ltcg,
    tax_table_for_filing_status,
    taxable_social_security_irs,
)

VALIDATION_DIR = Path(__file__).resolve().parent
DEFAULT_SCENARIOS_PATH = VALIDATION_DIR / "golden_tax_scenarios.json"


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    passed: bool
    message: str


def _close(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def _run_ss_taxable(inputs: dict) -> float:
    return taxable_social_security_irs(
        float(inputs["ss_gross"]),
        other_income=float(inputs.get("other_income", 0)),
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        mfs_lived_apart=bool(inputs.get("mfs_lived_apart", False)),
    )


def _run_federal_income_tax(inputs: dict) -> float:
    table = tax_table_for_filing_status(
        str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )
    taxable_income = float(inputs["taxable_income"])
    seniors = int(inputs.get("seniors_65_count", 0))
    if seniors > 0:
        from model.taxes import additional_standard_deduction_65

        extra = additional_standard_deduction_65(
            str(inputs.get("filing_status", "mfj")),
            seniors_65_count=seniors,
            inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        )
        taxable_income = max(0.0, taxable_income - table.standard_deduction - extra)
    else:
        taxable_income = max(0.0, taxable_income - table.standard_deduction)
    return federal_income_tax(taxable_income, table)


def _run_federal_retirement_tax(inputs: dict) -> dict[str, float]:
    table = tax_table_for_filing_status(
        str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )
    detail = compute_retirement_tax_detail(
        ss_gross=float(inputs.get("ss_gross", 0)),
        pension_gross=float(inputs.get("pension_gross", 0)),
        rental_gross=float(inputs.get("rental_gross", 0)),
        earned_income=float(inputs.get("earned_income", 0)),
        withdrawal_gross=float(inputs.get("withdrawal_gross", 0)),
        roth_fraction=float(inputs.get("roth_fraction", 0)),
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        ss_tax_method=str(inputs.get("ss_tax_method", "irs_provisional")),
        ss_taxable_fraction=float(inputs.get("ss_taxable_fraction", 0.5)),
        tax_mode=str(inputs.get("tax_mode", "federal_brackets")),
        flat_rate=float(inputs.get("flat_rate", 0.22)),
        table=table,
        state_code=str(inputs.get("state_code", "CA")),
        state_tax_enabled=bool(inputs.get("state_tax_enabled", True)),
        state_tax_override_pct=inputs.get("state_tax_override_pct"),
        taxable_portfolio_fraction=float(inputs.get("taxable_portfolio_fraction", 0)),
        taxable_gain_fraction=float(inputs.get("taxable_gain_fraction", 0.5)),
        seniors_65_count=int(inputs.get("seniors_65_count", 0)),
        mfs_lived_apart=bool(inputs.get("mfs_lived_apart", False)),
        niit_enabled=bool(inputs.get("niit_enabled", True)),
        roth_conversion=float(inputs.get("roth_conversion", 0)),
    )
    return {
        "federal": detail.federal,
        "state": detail.state,
        "ss_taxable": detail.ss_taxable,
    }


def _run_ltcg_tax(inputs: dict) -> dict[str, float]:
    table = tax_table_for_filing_status(
        str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )
    total, ordinary, ltcg = federal_tax_with_ltcg(
        ordinary_income=float(inputs.get("ordinary_income", 0)),
        ltcg=float(inputs.get("ltcg", 0)),
        table=table,
        seniors_65_count=int(inputs.get("seniors_65_count", 0)),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        filing_status=str(inputs.get("filing_status", "mfj")),
    )
    return {"total": total, "ordinary": ordinary, "ltcg": ltcg}


def _run_niit(inputs: dict) -> float:
    return compute_niit(
        magi=float(inputs["magi"]),
        net_investment_income=float(inputs["net_investment_income"]),
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        enabled=bool(inputs.get("enabled", True)),
    )


def _run_state_tax(inputs: dict) -> float:
    profile = state_profile(str(inputs.get("state_code", "CA")))
    return compute_state_income_tax(
        float(inputs["gross_taxable"]),
        ss_taxable=float(inputs.get("ss_taxable", 0)),
        profile=profile,
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        override_flat_rate=inputs.get("override_flat_rate"),
    )


def _run_aca_net(inputs: dict) -> dict[str, float]:
    policy = AcaPolicy(
        mode=str(inputs.get("mode", "aca_subsidized")),
        benchmark_monthly_age_21=float(inputs.get("benchmark_monthly_age_21", 475)),
        selected_plan_monthly_age_21=inputs.get("selected_plan_monthly_age_21"),
        slcsp_monthly_age_21=inputs.get("slcsp_monthly_age_21"),
    )
    cost = compute_aca_household_cost(
        list(inputs["enrollee_ages"]),
        magi=float(inputs["magi"]),
        policy=policy,
        filing_status=str(inputs.get("filing_status", "mfj")),
        household_size=int(inputs.get("household_size", 2)),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )
    return {
        "net": cost.net,
        "subsidy": cost.subsidy,
        "gross": cost.gross,
        "fpl_pct": cost.fpl_pct,
    }


def _run_medicare_total(inputs: dict) -> dict[str, float]:
    policy = MedicarePolicy(
        enabled=bool(inputs.get("enabled", True)),
        start_age=int(inputs.get("start_age", 65)),
    )
    ages = {str(k): int(v) for k, v in inputs["ages"].items()}
    cost = compute_medicare_cost(
        ages=ages,
        policy=policy,
        magi=float(inputs.get("magi", 0)),
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
        persons=inputs.get("persons"),
    )
    return {"total": cost.total, "irmaa": cost.irmaa, "base": cost.base}


def _run_medicare_irmaa_monthly(inputs: dict) -> dict[str, float]:
    part_b, part_d = irmaa_monthly_surcharges(
        float(inputs["magi"]),
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )
    return {"part_b": part_b, "part_d": part_d}


def _run_rmd(inputs: dict) -> float:
    return compute_rmd(
        float(inputs["traditional_balance"]),
        int(inputs["age"]),
        rmd_start_age=int(inputs.get("rmd_start_age", 73)),
    )


def _run_fica(inputs: dict) -> dict[str, float]:
    ss_tax, medicare_tax = employee_fica(
        float(inputs["wages"]),
        filing_status=str(inputs.get("filing_status", "mfj")),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )
    return {"ss_tax": ss_tax, "medicare_tax": medicare_tax, "total": ss_tax + medicare_tax}


def _run_ss_pia(inputs: dict) -> float:
    return pia_from_aime(
        float(inputs["aime"]),
        inflation_factor=float(inputs.get("inflation_factor", 1.0)),
    )


def _run_ss_monthly_benefit(inputs: dict) -> float:
    return monthly_benefit_from_pia(
        float(inputs["pia_monthly"]),
        claiming_age=int(inputs["claiming_age"]),
        birth_year=int(inputs.get("birth_year", 1960)),
    )


def _run_aca_applicable_pct(inputs: dict) -> float:
    return applicable_percentage(float(inputs["fpl_percent"]))


def _run_policy_field(inputs: dict) -> float:
    policy = policy_for_projection(inflation_factor=float(inputs.get("inflation_factor", 1.0)))
    field = str(inputs["field"])
    return float(getattr(policy, field))


HANDLERS: dict[str, Callable[[dict], float | dict[str, float]]] = {
    "ss_taxable": _run_ss_taxable,
    "federal_income_tax": _run_federal_income_tax,
    "federal_retirement_tax": _run_federal_retirement_tax,
    "ltcg_tax": _run_ltcg_tax,
    "niit": _run_niit,
    "state_tax": _run_state_tax,
    "aca_net": _run_aca_net,
    "medicare_total": _run_medicare_total,
    "medicare_irmaa_monthly": _run_medicare_irmaa_monthly,
    "rmd": _run_rmd,
    "fica": _run_fica,
    "ss_pia": _run_ss_pia,
    "ss_monthly_benefit": _run_ss_monthly_benefit,
    "aca_applicable_pct": _run_aca_applicable_pct,
    "policy_field": _run_policy_field,
}


def _evaluate_scenario(scenario: dict[str, Any]) -> ScenarioResult:
    scenario_id = str(scenario["id"])
    category = str(scenario.get("category", "unknown"))
    scenario_type = str(scenario["type"])
    tolerance = float(scenario.get("tolerance", 1.0))
    handler = HANDLERS.get(scenario_type)
    if handler is None:
        return ScenarioResult(scenario_id, category, False, f"Unknown scenario type: {scenario_type}")

    try:
        actual = handler(scenario["inputs"])
    except Exception as exc:
        return ScenarioResult(scenario_id, category, False, f"Execution error: {exc}")

    expected = scenario["expect"]
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)):
            return ScenarioResult(scenario_id, category, False, "Expected scalar but got mapping")
        if not _close(float(actual), float(expected), tolerance):
            return ScenarioResult(
                scenario_id,
                category,
                False,
                f"expected {expected}, got {actual}",
            )
        return ScenarioResult(scenario_id, category, True, f"value={actual}")

    if not isinstance(actual, dict):
        return ScenarioResult(scenario_id, category, False, "Expected mapping but got scalar")
    for key, target in expected.items():
        if key not in actual:
            return ScenarioResult(scenario_id, category, False, f"Missing output key: {key}")
        if not _close(float(actual[key]), float(target), tolerance):
            return ScenarioResult(
                scenario_id,
                category,
                False,
                f"{key}: expected {target}, got {actual[key]}",
            )
    return ScenarioResult(scenario_id, category, True, ", ".join(f"{k}={actual[k]}" for k in expected))


def load_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    scenario_path = path or DEFAULT_SCENARIOS_PATH
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    return list(payload["scenarios"])


def run_golden_scenarios(path: Path | None = None) -> tuple[list[ScenarioResult], dict[str, int]]:
    scenarios = load_scenarios(path)
    results = [_evaluate_scenario(scenario) for scenario in scenarios]
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    return results, {"total": len(results), "passed": passed, "failed": failed}


def summarize_results(results: list[ScenarioResult]) -> str:
    lines = []
    for result in results:
        mark = "✓" if result.passed else "✗"
        lines.append(f"  {mark} [{result.category}] {result.scenario_id}: {result.message}")
    return "\n".join(lines)
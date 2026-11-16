"""Validate model tax output against a user-supplied Form 1040 JSON snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.policy.live import activate_policy_year, get_active_policy_meta
from model.cpa_tax import ItemizedInputs, itemized_inputs_from_params
from model.payroll import compute_working_year_tax_detail
from model.taxes import compute_retirement_tax_detail, tax_table_for_filing_status

VALIDATION_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_PATH = VALIDATION_DIR / "tax_return_template.json"


@dataclass
class LineComparison:
    line: str
    expected: float | None
    actual: float | None
    tolerance: float
    passed: bool
    message: str


@dataclass
class TaxReturnValidationResult:
    label: str
    tax_year: int
    passed: bool
    comparisons: list[LineComparison] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy_label: str = ""

    def summary_text(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"{status}: {self.label} (tax year {self.tax_year})"]
        if self.policy_label:
            lines.append(f"Policy: {self.policy_label}")
        for comp in self.comparisons:
            mark = "ok" if comp.passed else "FAIL"
            exp = "n/a" if comp.expected is None else f"${comp.expected:,.0f}"
            act = "n/a" if comp.actual is None else f"${comp.actual:,.0f}"
            lines.append(f"  [{mark}] {comp.line}: expected {exp}, model {act} — {comp.message}")
        for warning in self.warnings:
            lines.append(f"  [warn] {warning}")
        return "\n".join(lines)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _compare(
    line: str,
    expected: float | None,
    actual: float | None,
    tolerance: float,
) -> LineComparison:
    if expected is None:
        return LineComparison(
            line=line,
            expected=None,
            actual=actual,
            tolerance=tolerance,
            passed=True,
            message="not checked (no expected value)",
        )
    if actual is None:
        return LineComparison(
            line=line,
            expected=expected,
            actual=None,
            tolerance=tolerance,
            passed=False,
            message="model did not produce a value",
        )
    delta = abs(actual - expected)
    passed = delta <= tolerance
    return LineComparison(
        line=line,
        expected=expected,
        actual=actual,
        tolerance=tolerance,
        passed=passed,
        message=f"delta ${delta:,.0f} (tol ${tolerance:,.0f})",
    )


def _income_components(payload: dict[str, Any]) -> dict[str, float]:
    income = payload.get("income", {})
    return {
        "wages": float(income.get("wages", 0) or 0),
        "taxable_interest": float(income.get("taxable_interest", 0) or 0),
        "ordinary_dividends": float(income.get("ordinary_dividends", 0) or 0),
        "ira_distributions": float(income.get("ira_distributions", 0) or 0),
        "pension": float(income.get("pension", 0) or 0),
        "social_security_gross": float(income.get("social_security_gross", 0) or 0),
        "capital_gain": float(income.get("capital_gain", 0) or 0),
        "rental_income": float(income.get("rental_income", 0) or 0),
    }


def _model_params_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    itemized = payload.get("itemized", {})
    params = {
        "planning_precision": payload.get("planning_precision", "standard"),
        "itemized_salt_annual": itemized.get("salt", 0),
        "itemized_mortgage_interest": itemized.get("mortgage_interest", 0),
        "itemized_charitable": itemized.get("charitable", 0),
        "itemized_medical": itemized.get("medical", 0),
    }
    return params


def _ensure_policy_year(tax_year: int, *, auto_activate: bool) -> list[str]:
    warnings: list[str] = []
    meta = get_active_policy_meta()
    if meta is not None and meta.tax_year == tax_year:
        return warnings
    if not auto_activate:
        active = meta.tax_year if meta else "embedded 2024"
        warnings.append(
            f"Active policy year is {active}; return is {tax_year}. "
            "Run: python tools/fetch_policy_tables.py --activate "
            f"{tax_year}"
        )
        return warnings
    try:
        activate_policy_year(tax_year)
        warnings.append(f"Auto-activated IRS policy year {tax_year}")
    except FileNotFoundError:
        warnings.append(f"No official policy file for {tax_year}; using embedded baseline")
    return warnings


def validate_tax_return(
    payload: dict[str, Any],
    *,
    auto_activate_policy: bool = True,
) -> TaxReturnValidationResult:
    """Compare model-computed taxes to Form 1040 values in ``payload``."""
    label = str(payload.get("label", "Tax return"))
    tax_year = int(payload.get("tax_year", 2024))
    filing_status = str(payload.get("filing_status", "mfj"))
    state_code = str(payload.get("state_of_residence", "CA"))
    tax_mode = str(payload.get("tax_mode", "federal_brackets"))
    state_enabled = bool(payload.get("state_tax_enabled", True))
    state_override = _float_or_none(payload.get("state_tax_override_pct"))
    seniors = int(payload.get("seniors_65_count", 0) or 0)
    precision = str(payload.get("planning_precision", "standard"))
    form = payload.get("form_1040", {})
    tol = payload.get("tolerance", {})

    warnings = _ensure_policy_year(tax_year, auto_activate=auto_activate_policy)
    from config.policy.live import active_policy_label

    policy_label = active_policy_label()
    income = _income_components(payload)
    params = _model_params_from_payload(payload)
    itemized = itemized_inputs_from_params(params)

    wages = income["wages"]
    other_ordinary = (
        income["taxable_interest"]
        + income["ordinary_dividends"]
        + income["ira_distributions"]
        + income["pension"]
        + income["rental_income"]
    )
    withdrawal_gross = income["ira_distributions"]
    ltcg = max(0.0, income["capital_gain"])
    earned_income = wages + income["taxable_interest"] + income["ordinary_dividends"]

    comparisons: list[LineComparison] = []

    if wages > 0 and other_ordinary == 0 and income["social_security_gross"] == 0 and ltcg == 0:
        work_detail = compute_working_year_tax_detail(
            wages,
            filing_status=filing_status,
            inflation_factor=1.0,
            tax_mode=tax_mode,
            flat_rate=0.0,
            state_code=state_code,
            state_tax_enabled=state_enabled,
            state_tax_override_pct=state_override,
            planning_precision=precision,
            itemized=itemized,
        )
        federal = work_detail.federal
        state = work_detail.state
        model_agi = wages
        table = tax_table_for_filing_status(filing_status, inflation_factor=1.0)
        model_taxable = max(0.0, wages - table.standard_deduction)
        comparisons.extend(
            [
                _compare(
                    "AGI (line 11)",
                    _float_or_none(form.get("agi")),
                    model_agi,
                    float(tol.get("agi", 5)),
                ),
                _compare(
                    "Taxable income (line 15)",
                    _float_or_none(form.get("taxable_income")),
                    model_taxable,
                    float(tol.get("taxable_income", 5)),
                ),
                _compare(
                    "Federal income tax (line 16)",
                    _float_or_none(form.get("income_tax")),
                    federal,
                    float(tol.get("federal_tax", 75)),
                ),
                _compare(
                    "Total tax (line 24)",
                    _float_or_none(form.get("total_tax")),
                    federal,
                    float(tol.get("federal_tax", 75)),
                ),
            ]
        )
        expected_state = _float_or_none(form.get("state_income_tax"))
        if expected_state is not None:
            comparisons.append(
                _compare(
                    "State income tax",
                    expected_state,
                    state,
                    float(tol.get("state_tax", 100)),
                )
            )
    else:
        table = tax_table_for_filing_status(filing_status, inflation_factor=1.0)
        detail = compute_retirement_tax_detail(
            ss_gross=income["social_security_gross"],
            pension_gross=income["pension"],
            rental_gross=income["rental_income"],
            earned_income=earned_income,
            withdrawal_gross=withdrawal_gross,
            roth_fraction=0.0,
            filing_status=filing_status,
            inflation_factor=1.0,
            ss_tax_method="irs",
            ss_taxable_fraction=0.0,
            tax_mode=tax_mode,
            flat_rate=0.0,
            table=table,
            state_code=state_code,
            state_tax_enabled=state_enabled,
            state_tax_override_pct=state_override,
            taxable_portfolio_fraction=0.0 if ltcg > 0 else 1.0,
            taxable_gain_fraction=0.5,
            seniors_65_count=seniors,
            planning_precision=precision,
            itemized=itemized,
        )
        model_agi = (
            earned_income
            + income["pension"]
            + income["rental_income"]
            + withdrawal_gross
            + ltcg
            + detail.ss_taxable
        )
        from model.taxes import additional_standard_deduction_65

        extra_sd = additional_standard_deduction_65(
            filing_status,
            seniors_65_count=seniors,
            inflation_factor=1.0,
        )
        deduction = (
            detail.federal_deduction
            if detail.federal_deduction > 0
            else table.standard_deduction + extra_sd
        )
        model_taxable = max(0.0, model_agi - deduction)
        comparisons.extend(
            [
                _compare(
                    "AGI (line 11)",
                    _float_or_none(form.get("agi")),
                    model_agi,
                    float(tol.get("agi", 5)),
                ),
                _compare(
                    "Taxable income (line 15)",
                    _float_or_none(form.get("taxable_income")),
                    model_taxable,
                    float(tol.get("taxable_income", 5)),
                ),
                _compare(
                    "Federal income tax (line 16)",
                    _float_or_none(form.get("income_tax")),
                    detail.federal,
                    float(tol.get("federal_tax", 75)),
                ),
                _compare(
                    "Total tax (line 24)",
                    _float_or_none(form.get("total_tax")),
                    detail.federal,
                    float(tol.get("federal_tax", 75)),
                ),
            ]
        )
        expected_state = _float_or_none(form.get("state_income_tax"))
        if expected_state is not None:
            comparisons.append(
                _compare(
                    "State income tax",
                    expected_state,
                    detail.state,
                    float(tol.get("state_tax", 100)),
                )
            )

    passed = all(c.passed for c in comparisons)
    return TaxReturnValidationResult(
        label=label,
        tax_year=tax_year,
        passed=passed,
        comparisons=comparisons,
        warnings=warnings,
        policy_label=policy_label,
    )


def load_tax_return_file(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_tax_return_file(
    path: Path | str,
    *,
    auto_activate_policy: bool = True,
) -> TaxReturnValidationResult:
    payload = load_tax_return_file(path)
    return validate_tax_return(payload, auto_activate_policy=auto_activate_policy)


def write_template(path: Path | str | None = None) -> Path:
    destination = Path(path) if path else DEFAULT_TEMPLATE_PATH
    destination.write_text(
        DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return destination
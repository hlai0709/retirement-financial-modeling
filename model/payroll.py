"""Payroll taxes (FICA) and earned-income tax for working years."""

from __future__ import annotations

from dataclasses import dataclass

from config.policy.tables import policy_for_projection
from model.cpa_tax import (
    ItemizedInputs,
    federal_tax_cpa_precision,
    has_itemized_amounts,
    normalize_planning_precision,
)
from model.taxes import (
    TaxTable,
    federal_income_tax,
    normalize_filing_status,
    tax_table_for_filing_status,
)
from model.state_taxes import compute_state_income_tax, state_profile

SS_EMPLOYEE_RATE = 0.062
MEDICARE_EMPLOYEE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009

SS_WAGE_BASE_2024 = 168_600.0
ADDITIONAL_MEDICARE_SINGLE_2024 = 200_000.0
ADDITIONAL_MEDICARE_MFJ_2024 = 250_000.0


@dataclass(frozen=True)
class WorkingYearTaxDetail:
    federal: float
    state: float
    fica: float
    deduction_method: str = "standard"
    federal_deduction: float = 0.0
    amt_surcharge: float = 0.0


def employee_fica(
    wages: float,
    *,
    filing_status: str,
    inflation_factor: float = 1.0,
) -> tuple[float, float]:
    """Return (Social Security tax, Medicare tax incl. additional)."""
    if wages <= 0:
        return 0.0, 0.0
    policy = policy_for_projection(inflation_factor=inflation_factor)
    ss_base = SS_WAGE_BASE_2024 * inflation_factor
    ss_tax = min(wages, ss_base) * SS_EMPLOYEE_RATE
    medicare = wages * MEDICARE_EMPLOYEE_RATE
    status = normalize_filing_status(filing_status)
    threshold = (
        ADDITIONAL_MEDICARE_MFJ_2024 * inflation_factor
        if status == "mfj"
        else ADDITIONAL_MEDICARE_SINGLE_2024 * inflation_factor
    )
    if wages > threshold:
        medicare += (wages - threshold) * ADDITIONAL_MEDICARE_RATE
    return ss_tax, medicare


def compute_working_year_tax_detail(
    wages: float,
    *,
    filing_status: str,
    inflation_factor: float,
    tax_mode: str,
    flat_rate: float,
    state_code: str,
    state_tax_enabled: bool,
    state_tax_override_pct: float | None,
    planning_precision: str = "standard",
    itemized: ItemizedInputs | None = None,
    seniors_65_count: int = 0,
    tax_year: int = 2024,
) -> WorkingYearTaxDetail:
    """Return federal, state, and FICA taxes on W-2 wages (CPA-aware when requested)."""
    if wages <= 0:
        return WorkingYearTaxDetail(0.0, 0.0, 0.0)

    precision = normalize_planning_precision(planning_precision)
    itemized_inputs = itemized or ItemizedInputs()
    table = tax_table_for_filing_status(filing_status, inflation_factor=inflation_factor)
    deduction_method = "standard"
    federal_deduction = 0.0
    amt_surcharge = 0.0

    if tax_mode == "federal_brackets":
        if precision == "cpa":
            cpa = federal_tax_cpa_precision(
                ordinary_income=wages,
                ltcg=0.0,
                table=table,
                filing_status=filing_status,
                seniors_65_count=seniors_65_count,
                inflation_factor=inflation_factor,
                itemized=itemized_inputs,
                agi=wages,
                tax_year=tax_year,
            )
            federal = cpa.total_federal
            deduction_method = cpa.deduction_method
            federal_deduction = cpa.deduction_used
            amt_surcharge = cpa.amt_surcharge
        elif has_itemized_amounts(itemized_inputs):
            chosen = federal_tax_cpa_precision(
                ordinary_income=wages,
                ltcg=0.0,
                table=table,
                filing_status=filing_status,
                seniors_65_count=seniors_65_count,
                inflation_factor=inflation_factor,
                itemized=itemized_inputs,
                agi=wages,
                tax_year=tax_year,
            )
            federal = chosen.total_federal - chosen.amt_surcharge
            deduction_method = chosen.deduction_method
            federal_deduction = chosen.deduction_used
        else:
            federal = federal_income_tax(max(0.0, wages - table.standard_deduction), table)
    else:
        federal = wages * flat_rate

    state = 0.0
    if state_tax_enabled:
        profile = state_profile(state_code, strict=(precision == "cpa"))
        if profile is not None:
            state = compute_state_income_tax(
                wages,
                ss_taxable=0.0,
                profile=profile,
                filing_status=filing_status,
                inflation_factor=inflation_factor,
                override_flat_rate=state_tax_override_pct,
            )

    ss_fica, medicare_fica = employee_fica(
        wages,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
    )
    return WorkingYearTaxDetail(
        federal=federal,
        state=state,
        fica=ss_fica + medicare_fica,
        deduction_method=deduction_method,
        federal_deduction=federal_deduction,
        amt_surcharge=amt_surcharge,
    )
"""Core year-by-year retirement projection math (household and individual)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.model_fields import FIELDS_BY_KEY, RETIREMENT_EXPENSE_ADDITIVE_KEYS
from model.filing import YearFilingContext, apply_survivor_filing, asset_shares
from model.aca import (
    AcaCost,
    AcaPolicy,
    aca_tax_household_size,
    compute_aca_household_cost,
)
from model.accounts import (
    VALID_WITHDRAWAL_ORDERS,
    AccountBalances,
    HouseholdLedger,
    allocate_withdrawal,
    apply_contributions,
    apply_returns,
    apply_withdrawal,
    roth_fraction_for_tax,
)
from model.magi import (
    build_magi_worksheet,
    build_magi_worksheet_from_accounts,
    magi_and_taxable_ss,
)
from model.employer_health import household_employer_health
from model.expenses import (
    ExpensePolicy,
    living_person_names,
    person_retirement_living_expense,
)
from model.ltc import person_ltc_annual
from model.medicare import MedicareCost, MedicarePolicy, compute_medicare_cost
from model.payroll import WorkingYearTaxDetail, compute_working_year_tax_detail, employee_fica
from model.qcd import household_qcd_total, person_qcd_amount
from model.roth_conversion import (
    apply_roth_conversion_to_ledger,
    person_wants_bracket_fill,
    resolve_person_conversions,
    solve_bracket_fill_conversion,
    target_bracket_rate_from_params,
)
from model.state_residence import YearStateContext
from model.rmd import (
    compute_rmd,
    household_rmd_age,
    person_rmd_start_age,
)
from model.cpa_tax import ItemizedInputs, normalize_planning_precision
from model.taxes import (
    VALID_FILING_STATUSES,
    VALID_SS_TAX_METHODS,
    normalize_filing_status,
    seniors_65_count_for_filing,
    solve_withdrawal_for_expenses,
    tax_table_for_filing_status,
)

TSLA_BASELINE_PRICE = 426.0
VALID_TAX_CALCULATIONS = frozenset({"flat", "federal_brackets"})
SP500_REBALANCE_RETURN = 0.07
MAX_SS_TAXABLE_FRACTION = 0.85


@dataclass(frozen=True)
class TaxPolicy:
    pre_retire_rate: float
    post_retire_rate: float
    roth_portfolio_pct: float
    contribution_roth_pct: float
    ss_taxable_fraction: float
    ss_tax_method: str = "irs_provisional"
    rmd_start_age: int = 73
    tax_calculation: str = "federal_brackets"
    filing_status: str = "mfj"
    marriage_year: int | None = None
    filing_status_before_marriage: str = "head_of_household"
    filing_status_after_marriage: str = "mfj"
    state_residence: str = "CA"
    state_move_year: int | None = None
    state_after_move: str = "CA"
    state_tax_enabled: bool = True
    state_tax_override_pct: float | None = None
    taxable_gain_fraction: float = 0.5
    niit_enabled: bool = True
    mfs_lived_apart: bool = False
    use_birth_year_rmd: bool = True
    withdrawal_order: str = "tax_efficient"
    working_year_tax_enabled: bool = True
    roth_conversion_target_bracket: float = 0.22
    planning_precision: str = "standard"
    itemized: ItemizedInputs = field(default_factory=ItemizedInputs)


@dataclass
class PersonFlows:
    contributions: float
    earned_income: float
    expenses: float
    additives: dict[str, float]
    rental: float
    ss: float
    pension: float
    ss_active: bool = False
    pension_active: bool = False

    @classmethod
    def from_person(
        cls,
        person: dict[str, Any],
        *,
        current_year: int | None = None,
    ) -> PersonFlows:
        from model.ss import person_annual_ss_benefit

        anchor_year = int(current_year or person.get("current_year") or 2026)
        return cls(
            contributions=float(person.get("annual_contributions", 0) or 0),
            earned_income=float(person.get("annual_earned_income", 0) or 0),
            expenses=float(person.get("base_expenses", 0) or 0),
            additives={
                key: float(person.get(key, 0) or 0) for key in RETIREMENT_EXPENSE_ADDITIVE_KEYS
            },
            rental=float(person.get("rental_income_start", 0) or 0),
            ss=person_annual_ss_benefit(person, current_year=anchor_year),
            pension=float(person.get("pension_amount", 0) or 0),
        )


@dataclass
class YearResult:
    contributions: float
    returns: float
    withdrawals_gross: float
    rmd: float
    earned_income: float
    expenses: float
    rental_income: float
    pension: float
    ss_gross: float
    ss_pia_monthly: float
    ss_fra_years: float
    ss_taxable: float
    other_income: float
    taxes: float
    federal_taxes: float
    state_taxes: float
    assets_end: float
    additive_totals: dict[str, float]
    phase: str
    tax_mode: str
    filing_layout: str
    filing_status_label: str
    tax_state_label: str
    medicare_total: float
    medicare_irmaa: float
    medicare_phase: str
    health_pre_medicare: float
    health_post_medicare: float
    aca_gross: float
    aca_subsidy: float
    aca_net: float
    aca_slcsp: float
    employer_health: float
    medicare_medigap: float
    magi: float
    assets_taxable: float
    assets_traditional: float
    assets_roth: float
    fica_taxes: float
    qcd: float
    roth_conversion: float
    ltc_cost: float
    expense_phase: str
    notes: list[str]
    planning_precision: str = "standard"
    deduction_method: str = "standard"
    amt_surcharge: float = 0.0
    federal_deduction: float = 0.0


def tax_policy_from_params(params: dict[str, Any]) -> TaxPolicy:
    from model.filing import marriage_filing_defaults
    from model.state_residence import state_residence_defaults
    from model.state_taxes import normalize_state_code

    ss_frac = float(params.get("ss_taxable_fraction", 0.50) or 0)
    ss_frac = max(0.0, min(ss_frac, MAX_SS_TAXABLE_FRACTION))
    tax_calc = str(params.get("tax_calculation", "federal_brackets") or "federal_brackets").strip().lower()
    if tax_calc not in VALID_TAX_CALCULATIONS:
        tax_calc = "federal_brackets"
    filing = normalize_filing_status(str(params.get("filing_status", "mfj") or "mfj"))
    if filing not in VALID_FILING_STATUSES:
        filing = "mfj"
    ss_method = str(params.get("ss_tax_method", "irs_provisional") or "irs_provisional").strip().lower()
    if ss_method not in VALID_SS_TAX_METHODS:
        ss_method = "irs_provisional"
    marriage_year, filing_before, filing_after = marriage_filing_defaults(params)
    state_residence, state_move_year, state_after_move = state_residence_defaults(params)
    state_override = params.get("state_tax_override_pct")
    state_override_pct = None if state_override in (None, "") else float(state_override)
    state_tax_enabled = str(params.get("state_tax_enabled", "True")).lower() != "false"
    return TaxPolicy(
        pre_retire_rate=float(params.get("pre_retire_tax_rate", 0.35) or 0),
        post_retire_rate=float(params.get("post_retire_tax_rate", 0.20) or 0),
        roth_portfolio_pct=float(params.get("roth_portfolio_pct", 0) or 0),
        contribution_roth_pct=float(params.get("contribution_roth_pct", 0) or 0),
        ss_taxable_fraction=ss_frac,
        ss_tax_method=ss_method,
        rmd_start_age=int(params.get("rmd_start_age", 73) or 73),
        tax_calculation=tax_calc,
        filing_status=filing,
        marriage_year=marriage_year,
        filing_status_before_marriage=filing_before,
        filing_status_after_marriage=filing_after,
        state_residence=normalize_state_code(state_residence),
        state_move_year=state_move_year,
        state_after_move=normalize_state_code(state_after_move),
        state_tax_enabled=state_tax_enabled,
        state_tax_override_pct=state_override_pct,
        taxable_gain_fraction=min(
            1.0,
            max(0.0, float(params.get("taxable_gain_fraction", 50) or 50) / 100.0),
        ),
        niit_enabled=str(params.get("niit_enabled", "True")).lower() != "false",
        mfs_lived_apart=str(params.get("mfs_lived_apart", "False")).lower() == "true",
        use_birth_year_rmd=str(params.get("use_birth_year_rmd", "True")).lower() != "false",
        withdrawal_order=_valid_withdrawal_order(params.get("withdrawal_order", "tax_efficient")),
        working_year_tax_enabled=str(params.get("working_year_tax_enabled", "True")).lower()
        != "false",
        roth_conversion_target_bracket=target_bracket_rate_from_params(params),
        planning_precision=normalize_planning_precision(params.get("planning_precision")),
        itemized=ItemizedInputs(
            salt=float(params.get("itemized_salt_annual", 0) or 0),
            mortgage_interest=float(params.get("itemized_mortgage_interest", 0) or 0),
            charitable=float(params.get("itemized_charitable", 0) or 0),
            medical=float(params.get("itemized_medical", 0) or 0),
        ),
    )


def _valid_withdrawal_order(value: object) -> str:
    order = str(value or "tax_efficient").strip().lower()
    if order not in VALID_WITHDRAWAL_ORDERS:
        return "tax_efficient"
    return order


def split_contributions(
    total: float, *, roth_pct: float, pre_tax_rate: float
) -> tuple[float, float, float]:
    """Return (total deposited, traditional pre-tax portion, Roth after-tax portion)."""
    if total <= 0:
        return 0.0, 0.0, 0.0
    roth_share = max(0.0, min(roth_pct, 1.0))
    roth_budget = total * roth_share
    traditional = total - roth_budget
    roth_deposit = roth_budget * (1 - pre_tax_rate)
    deposited = traditional + roth_deposit
    return deposited, traditional, roth_deposit


def update_roth_fraction(
    assets_before: float,
    roth_fraction: float,
    *,
    deposit: float,
    roth_deposit: float,
) -> float:
    if assets_before + deposit <= 0:
        return roth_fraction
    roth_balance = assets_before * roth_fraction + roth_deposit
    return roth_balance / (assets_before + deposit)


def investment_returns(assets_start: float, contribution_deposit: float, rate: float) -> float:
    """Returns on beginning balance plus half-year credit for mid-year contributions."""
    return assets_start * rate + contribution_deposit * rate * 0.5


def after_tax_withdrawal_factor(roth_fraction: float, post_tax_rate: float) -> float:
    taxable_fraction = max(0.0, 1.0 - roth_fraction)
    return roth_fraction + taxable_fraction * (1.0 - post_tax_rate)


def gross_withdrawal_for_spending_need(
    net_need: float,
    *,
    roth_fraction: float,
    post_tax_rate: float,
) -> tuple[float, float]:
    if net_need <= 0:
        return 0.0, 0.0
    factor = after_tax_withdrawal_factor(roth_fraction, post_tax_rate)
    if factor <= 0:
        return net_need, 0.0
    gross = net_need / factor
    taxable = gross * max(0.0, 1.0 - roth_fraction)
    return gross, taxable * post_tax_rate


def benefit_income_taxes(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    tax: TaxPolicy,
) -> float:
    taxable = ss_gross * tax.ss_taxable_fraction + pension_gross + rental_gross
    return taxable * tax.post_retire_rate


def retirement_additive_amounts(
    flows: PersonFlows,
    *,
    age: int,
    medicare_start_age: int,
    person: dict[str, Any] | None = None,
    retirement_age: int = 65,
    ltc_inflation_factor: float = 1.0,
) -> dict[str, float]:
    """Age-aware retirement expense add-ons (pre-/post-Medicare health split)."""
    amounts: dict[str, float] = {}
    for key in RETIREMENT_EXPENSE_ADDITIVE_KEYS:
        if key == "long_term_care_annual":
            if person is not None:
                amounts[key] = person_ltc_annual(
                    person,
                    age=age,
                    retirement_age=retirement_age,
                    ltc_inflation_factor=ltc_inflation_factor,
                )
            else:
                amounts[key] = 0.0
            continue
        value = flows.additives[key]
        role = FIELDS_BY_KEY[key].model_role
        if role == "health_pre_medicare":
            include = age < medicare_start_age
        elif role == "health_post_medicare":
            include = age >= medicare_start_age
        else:
            include = True
        amounts[key] = value if include else 0.0
    return amounts


def retirement_expense_for_person(
    flows: PersonFlows,
    *,
    age: int,
    year: int,
    medicare_start_age: int,
    aca_mode: str = "manual",
    person: dict[str, Any] | None = None,
    inflation_factor: float = 1.0,
    ltc_inflation_factor: float = 1.0,
    expense_policy: ExpensePolicy | None = None,
) -> float:
    policy = expense_policy or ExpensePolicy()
    retirement_age = int(person.get("retirement_age", 65) or 65) if person else 65
    living, _ = person_retirement_living_expense(
        flows.expenses,
        person or {},
        age=age,
        year=year,
        retirement_age=retirement_age,
        policy=policy,
        inflation_factor=inflation_factor,
    )
    additives = retirement_additive_amounts(
        flows,
        age=age,
        medicare_start_age=medicare_start_age,
        person=person,
        retirement_age=retirement_age,
        ltc_inflation_factor=ltc_inflation_factor,
    )
    if age < medicare_start_age and person is not None:
        from model.employer_health import person_employer_health_annual

        if person_employer_health_annual(
            person,
            age=age,
            retirement_age=retirement_age,
            medicare_start_age=medicare_start_age,
            inflation_factor=inflation_factor,
        ) > 0:
            additives["health_insurance_pre_medicare"] = 0.0
        elif aca_mode == "aca_subsidized":
            additives["health_insurance_pre_medicare"] = 0.0
    elif aca_mode == "aca_subsidized":
        additives["health_insurance_pre_medicare"] = 0.0
    return living + sum(additives.values())


def aca_enrollee_ages(
    *,
    retired: list[str],
    ages: dict[str, int],
    medicare_start_age: int,
    persons: dict[str, dict[str, Any]] | None = None,
    inflation_factor: float = 1.0,
) -> list[int]:
    from model.employer_health import person_employer_health_annual

    enrollees: list[int] = []
    for name in retired:
        if ages[name] >= medicare_start_age:
            continue
        if persons is not None:
            person = persons[name]
            if person_employer_health_annual(
                person,
                age=ages[name],
                retirement_age=int(person.get("retirement_age", 65) or 65),
                medicare_start_age=medicare_start_age,
                inflation_factor=inflation_factor,
            ) > 0:
                continue
        enrollees.append(ages[name])
    return enrollees


def medicare_coverage_phase(
    *,
    retired: list[str],
    ages: dict[str, int],
    medicare_start_age: int,
) -> str:
    if not retired:
        return "working"
    pre = [name for name in retired if ages[name] < medicare_start_age]
    post = [name for name in retired if ages[name] >= medicare_start_age]
    if pre and post:
        return "mixed_pre_and_medicare"
    if pre:
        return "pre_medicare_gap"
    return "medicare_enrolled"


def activate_benefits(
    persons: dict[str, dict[str, Any]],
    flows: dict[str, PersonFlows],
    ages: dict[str, int],
) -> None:
    for name, person in persons.items():
        if ages[name] >= int(person.get("ss_start_age", 67)):
            flows[name].ss_active = True
        if ages[name] >= int(person.get("pension_start_age", 65)):
            flows[name].pension_active = True


def inflate_flows(flows: PersonFlows, *, year_inflation: float, rental_growth: float) -> None:
    flows.contributions *= 1 + year_inflation
    flows.earned_income *= 1 + year_inflation
    flows.expenses *= 1 + year_inflation
    for key in flows.additives:
        if key == "long_term_care_annual":
            continue
        flows.additives[key] *= 1 + year_inflation
    flows.rental *= 1 + rental_growth
    if flows.ss_active:
        flows.ss *= 1 + year_inflation
    if flows.pension_active:
        flows.pension *= 1 + year_inflation


def _apply_working_year_taxes(
    *,
    earned_income: float,
    tax: TaxPolicy,
    filing_status: str,
    inflation_factor: float,
    state_code: str,
    seniors_65_count: int = 0,
) -> WorkingYearTaxDetail:
    if earned_income <= 0 or not tax.working_year_tax_enabled:
        return WorkingYearTaxDetail(0.0, 0.0, 0.0)
    return compute_working_year_tax_detail(
        earned_income,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        tax_mode=tax.tax_calculation,
        flat_rate=tax.pre_retire_rate if tax.tax_calculation == "flat" else tax.post_retire_rate,
        state_code=state_code,
        state_tax_enabled=tax.state_tax_enabled,
        state_tax_override_pct=tax.state_tax_override_pct,
        planning_precision=tax.planning_precision,
        itemized=tax.itemized,
        seniors_65_count=seniors_65_count,
    )


def _fica_on_wages(
    wages: float,
    *,
    filing_status: str,
    inflation_factor: float,
) -> float:
    if wages <= 0:
        return 0.0
    ss_fica, medicare_fica = employee_fica(
        wages,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
    )
    return ss_fica + medicare_fica


def _working_payroll_taxes(
    *,
    persons: dict[str, dict[str, Any]],
    flows: dict[str, PersonFlows],
    ages: dict[str, int],
    working: list[str],
    retired: list[str],
    tax: TaxPolicy,
    filing: YearFilingContext,
    state: YearStateContext,
    inflation_factor: float,
) -> WorkingYearTaxDetail:
    """Add W-2 income tax (accumulation) and/or FICA (partial retirement) on earned wages."""
    if not tax.working_year_tax_enabled:
        return WorkingYearTaxDetail(0.0, 0.0, 0.0)

    earned_income = sum(flows[name].earned_income for name in working)
    if earned_income <= 0:
        return WorkingYearTaxDetail(0.0, 0.0, 0.0)

    if not retired:
        if filing.layout == "joint_return":
            assert filing.joint_filing_status is not None
            seniors = seniors_65_count_for_filing(ages, filing_status=filing.joint_filing_status)
            return _apply_working_year_taxes(
                earned_income=earned_income,
                tax=tax,
                filing_status=filing.joint_filing_status,
                inflation_factor=inflation_factor,
                state_code=state.code,
                seniors_65_count=seniors,
            )
        federal = state_tax = fica = 0.0
        deduction_method = "standard"
        federal_deduction = 0.0
        amt_surcharge = 0.0
        for name in working:
            person_filing = filing.person_filing_status[name]
            seniors = 1 if ages[name] >= 65 else 0
            detail = _apply_working_year_taxes(
                earned_income=flows[name].earned_income,
                tax=tax,
                filing_status=person_filing,
                inflation_factor=inflation_factor,
                state_code=state.code,
                seniors_65_count=seniors,
            )
            federal += detail.federal
            state_tax += detail.state
            fica += detail.fica
            if detail.federal > 0:
                deduction_method = detail.deduction_method
                federal_deduction += detail.federal_deduction
                amt_surcharge += detail.amt_surcharge
        return WorkingYearTaxDetail(
            federal=federal,
            state=state_tax,
            fica=fica,
            deduction_method=deduction_method,
            federal_deduction=federal_deduction,
            amt_surcharge=amt_surcharge,
        )

    fica = 0.0
    if filing.layout == "joint_return":
        assert filing.joint_filing_status is not None
        fica = _fica_on_wages(
            earned_income,
            filing_status=filing.joint_filing_status,
            inflation_factor=inflation_factor,
        )
    else:
        for name in working:
            fica += _fica_on_wages(
                flows[name].earned_income,
                filing_status=filing.person_filing_status[name],
                inflation_factor=inflation_factor,
            )
    return WorkingYearTaxDetail(0.0, 0.0, fica)


def _taxable_portfolio_fraction(ledger: HouseholdLedger, *, household_mode: bool) -> float:
    balances = _ledger_balances(ledger, household_mode=household_mode)
    if balances.total <= 0:
        return 0.0
    return balances.taxable / balances.total


def _rmd_start_ages(
    persons: dict[str, dict[str, Any]],
    *,
    anchor_year: int,
    tax: TaxPolicy,
) -> dict[str, int]:
    return {
        name: person_rmd_start_age(
            person,
            current_year=anchor_year,
            default_start_age=tax.rmd_start_age,
            use_birth_year=tax.use_birth_year_rmd,
        )
        for name, person in persons.items()
    }


def _ledger_balances(ledger: HouseholdLedger, *, household_mode: bool) -> AccountBalances:
    if household_mode:
        return ledger.by_person.get("__household__", AccountBalances())
    return ledger.household_total


def _compute_magi(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    withdrawal_gross: float,
    ledger: HouseholdLedger,
    household_mode: bool,
    filing_status: str,
    inflation_factor: float,
    tax: TaxPolicy,
    tax_exempt_interest: float = 0.0,
    rmd_floor: float = 0.0,
    qcd_amount: float = 0.0,
    roth_conversion: float = 0.0,
) -> tuple[float, float]:
    balances = _ledger_balances(ledger, household_mode=household_mode)
    allocation = allocate_withdrawal(
        balances,
        withdrawal_gross,
        order=tax.withdrawal_order,
        rmd_floor=rmd_floor,
        qcd=qcd_amount,
    )
    gain_frac = max(0.0, min(tax.taxable_gain_fraction, 1.0))
    trad_for_magi = max(0.0, allocation.from_traditional - qcd_amount)
    if balances.taxable > 0 or allocation.from_taxable > 0:
        worksheet = build_magi_worksheet_from_accounts(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental_gross,
            earned_income=earned_income,
            withdrawal_gross=withdrawal_gross,
            withdrawal_traditional=trad_for_magi,
            withdrawal_taxable=allocation.from_taxable * gain_frac,
            tax_exempt_interest=tax_exempt_interest,
            roth_conversion=roth_conversion,
        )
    else:
        worksheet = build_magi_worksheet(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental_gross,
            earned_income=earned_income,
            withdrawal_gross=withdrawal_gross,
            roth_fraction=balances.roth_fraction(),
            tax_exempt_interest=tax_exempt_interest,
            roth_conversion=roth_conversion,
        )
    return magi_and_taxable_ss(
        worksheet,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        ss_tax_method=tax.ss_tax_method,
        ss_taxable_fraction=tax.ss_taxable_fraction,
        mfs_lived_apart=tax.mfs_lived_apart,
    )


def _solve_with_healthcare(
    *,
    living_expenses: float,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    ledger: HouseholdLedger,
    household_mode: bool,
    filing_status: str,
    inflation_factor: float,
    tax: TaxPolicy,
    tax_table,
    rmd_floor: float,
    state_code: str,
    medicare: MedicarePolicy,
    medicare_ages: dict[str, int],
    irmaa_magi: float | None,
    aca: AcaPolicy,
    aca_enrollee_ages_list: list[int],
    aca_household_size: int,
    medicare_ages_for_deduction: dict[str, int],
    qcd_amount: float = 0.0,
    persons: dict[str, dict[str, Any]] | None = None,
    roth_conversion: float = 0.0,
) -> tuple[float, float, float, float, float, MedicareCost, AcaCost, float]:
    """Solve withdrawal with Medicare, ACA subsidies, and IRMAA (iterative)."""
    medicare_extra = 0.0
    aca_extra = 0.0
    medicare_cost = MedicareCost(0.0, 0.0, 0.0, 0.0, 0)
    aca_cost = AcaCost(0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0)
    withdrawal_gross = 0.0
    federal_taxes = 0.0
    state_taxes = 0.0
    ss_taxable = 0.0
    total_taxes = 0.0
    magi = 0.0

    seniors_65 = seniors_65_count_for_filing(
        medicare_ages_for_deduction,
        filing_status=filing_status,
    )
    taxable_frac = _taxable_portfolio_fraction(ledger, household_mode=household_mode)
    balances_for_tax = _ledger_balances(ledger, household_mode=household_mode)

    for _ in range(8):
        roth_fraction = roth_fraction_for_tax(ledger, household_mode=household_mode)
        withdrawal_gross, federal_taxes, state_taxes, ss_taxable, total_taxes = (
            solve_withdrawal_for_expenses(
                living_expenses + medicare_extra + aca_extra,
                ss_gross=ss_gross,
                pension_gross=pension_gross,
                rental_gross=rental_gross,
                earned_income=earned_income,
                roth_fraction=roth_fraction,
                filing_status=filing_status,
                inflation_factor=inflation_factor,
                ss_tax_method=tax.ss_tax_method,
                ss_taxable_fraction=tax.ss_taxable_fraction,
                tax_mode=tax.tax_calculation,
                flat_rate=tax.post_retire_rate,
                table=tax_table,
                rmd_floor=rmd_floor,
                state_code=state_code,
                state_tax_enabled=tax.state_tax_enabled,
                state_tax_override_pct=tax.state_tax_override_pct,
                taxable_portfolio_fraction=taxable_frac,
                taxable_gain_fraction=tax.taxable_gain_fraction,
                seniors_65_count=seniors_65,
                mfs_lived_apart=tax.mfs_lived_apart,
                niit_enabled=tax.niit_enabled,
                account_balances=balances_for_tax,
                withdrawal_order=tax.withdrawal_order,
                qcd_amount=qcd_amount,
                roth_conversion=roth_conversion,
                planning_precision=tax.planning_precision,
                itemized=tax.itemized,
            )
        )
        magi, ss_taxable = _compute_magi(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental_gross,
            earned_income=earned_income,
            withdrawal_gross=withdrawal_gross,
            ledger=ledger,
            household_mode=household_mode,
            filing_status=filing_status,
            inflation_factor=inflation_factor,
            tax=tax,
            rmd_floor=rmd_floor,
            qcd_amount=qcd_amount,
            roth_conversion=roth_conversion,
        )
        magi_for_irmaa = irmaa_magi if irmaa_magi is not None else magi
        medicare_cost = compute_medicare_cost(
            ages=medicare_ages,
            policy=medicare,
            magi=magi_for_irmaa,
            filing_status=filing_status,
            inflation_factor=inflation_factor,
            persons=persons,
        )
        if aca.mode == "aca_subsidized" and aca_enrollee_ages_list:
            aca_cost = compute_aca_household_cost(
                aca_enrollee_ages_list,
                magi=magi,
                policy=aca,
                filing_status=filing_status,
                household_size=aca_household_size,
                inflation_factor=inflation_factor,
            )
            new_aca_extra = aca_cost.net
        else:
            aca_cost = AcaCost(0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0)
            new_aca_extra = 0.0
        if (
            abs(medicare_cost.total - medicare_extra) < 25
            and abs(new_aca_extra - aca_extra) < 25
        ):
            break
        medicare_extra = medicare_cost.total
        aca_extra = new_aca_extra

    return (
        withdrawal_gross,
        federal_taxes,
        state_taxes,
        ss_taxable,
        total_taxes,
        medicare_cost,
        aca_cost,
        magi,
    )


def _cap_conversion_amounts(
    person_amounts: dict[str, float],
    *,
    max_traditional: float,
) -> dict[str, float]:
    total = sum(person_amounts.values())
    if total <= 0 or max_traditional <= 0:
        return {name: 0.0 for name in person_amounts}
    if total <= max_traditional:
        return dict(person_amounts)
    scale = max_traditional / total
    return {name: amount * scale for name, amount in person_amounts.items()}


def _plan_roth_conversion(
    *,
    persons: dict[str, dict[str, Any]],
    ages: dict[str, int],
    ledger: HouseholdLedger,
    household_mode: bool,
    tax: TaxPolicy,
    inflation_factor: float,
    filing_status: str,
    state_code: str,
    pension_gross: float,
    ss_gross: float,
    rental_gross: float,
    earned_income: float,
    baseline_withdrawal: float,
    rmd_floor: float,
    qcd_amount: float,
    medicare_ages: dict[str, int],
) -> tuple[float, dict[str, float]]:
    balances = _ledger_balances(ledger, household_mode=household_mode)
    person_amounts = resolve_person_conversions(
        persons,
        ages=ages,
        inflation_factor=inflation_factor,
    )
    person_amounts = _cap_conversion_amounts(
        person_amounts,
        max_traditional=balances.traditional,
    )
    fixed_total = sum(person_amounts.values())
    bracket_names = [
        name
        for name, person in persons.items()
        if person_wants_bracket_fill(person, age=ages[name])
    ]
    if bracket_names and tax.tax_calculation == "federal_brackets":
        remaining = max(0.0, balances.traditional - fixed_total)
        if remaining > 0:
            tax_table = tax_table_for_filing_status(
                filing_status,
                inflation_factor=inflation_factor,
            )
            seniors = seniors_65_count_for_filing(
                medicare_ages,
                filing_status=filing_status,
            )
            taxable_frac = _taxable_portfolio_fraction(ledger, household_mode=household_mode)
            bracket_fill = solve_bracket_fill_conversion(
                target_bracket_rate=tax.roth_conversion_target_bracket,
                ss_gross=ss_gross,
                pension_gross=pension_gross,
                rental_gross=rental_gross,
                earned_income=earned_income,
                withdrawal_gross=baseline_withdrawal,
                roth_fraction=roth_fraction_for_tax(ledger, household_mode=household_mode),
                filing_status=filing_status,
                inflation_factor=inflation_factor,
                ss_tax_method=tax.ss_tax_method,
                ss_taxable_fraction=tax.ss_taxable_fraction,
                tax_mode=tax.tax_calculation,
                flat_rate=tax.post_retire_rate,
                table=tax_table,
                state_code=state_code,
                state_tax_enabled=tax.state_tax_enabled,
                state_tax_override_pct=tax.state_tax_override_pct,
                taxable_portfolio_fraction=taxable_frac,
                taxable_gain_fraction=tax.taxable_gain_fraction,
                seniors_65_count=seniors,
                mfs_lived_apart=tax.mfs_lived_apart,
                niit_enabled=tax.niit_enabled,
                account_balances=balances,
                withdrawal_order=tax.withdrawal_order,
                rmd_floor=rmd_floor,
                qcd_amount=qcd_amount,
                max_traditional=remaining,
            )
            if bracket_fill > 0:
                share = bracket_fill / len(bracket_names)
                for name in bracket_names:
                    person_amounts[name] = person_amounts.get(name, 0.0) + share
    person_amounts = _cap_conversion_amounts(
        person_amounts,
        max_traditional=balances.traditional,
    )
    return sum(person_amounts.values()), person_amounts


def _solve_cash_flow_taxes(
    *,
    ledger: HouseholdLedger,
    household_mode: bool,
    persons: dict[str, dict[str, Any]],
    flows: dict[str, PersonFlows],
    ages: dict[str, int],
    working: list[str],
    retired: list[str],
    year: int,
    tax: TaxPolicy,
    inflation_factor: float,
    ltc_inflation_factor: float,
    expense_policy: ExpensePolicy,
    filing: YearFilingContext,
    state: YearStateContext,
    medicare: MedicarePolicy,
    aca: AcaPolicy,
    irmaa_magi: float | None,
    anchor_year: int,
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    MedicareCost,
    AcaCost,
    float,
    dict[str, float],
    float,
    float,
    dict[str, float],
]:
    """Return cash-flow tax solve results including Medicare, ACA, MAGI, QCD, and Roth conversion."""
    withdrawal_gross = 0.0
    person_withdrawals: dict[str, float] = {}
    federal_taxes = 0.0
    state_taxes = 0.0
    total_taxes = 0.0
    ss_taxable = 0.0
    rmd_floor = 0.0
    qcd_total = 0.0
    medicare_cost = MedicareCost(0.0, 0.0, 0.0, 0.0, 0)
    aca_cost = AcaCost(0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0)
    magi = 0.0
    rmd_start_ages = _rmd_start_ages(persons, anchor_year=anchor_year, tax=tax)

    if filing.layout == "joint_return":
        pension_gross = sum(flows[name].pension for name in persons if flows[name].pension_active)
        ss_gross = sum(flows[name].ss for name in persons if flows[name].ss_active)
        rental = sum(flows[name].rental for name in persons)
        earned_income = sum(flows[name].earned_income for name in working)
        living_expenses = sum(
            retirement_expense_for_person(
                flows[name],
                age=ages[name],
                year=year,
                medicare_start_age=medicare.start_age,
                aca_mode=aca.mode,
                person=persons[name],
                inflation_factor=inflation_factor,
                ltc_inflation_factor=ltc_inflation_factor,
                expense_policy=expense_policy,
            )
            for name in retired
        )
        joint_aca_ages = aca_enrollee_ages(
            retired=retired,
            ages=ages,
            medicare_start_age=medicare.start_age,
            persons=persons,
            inflation_factor=inflation_factor,
        )
        aca_household_size = aca_tax_household_size(
            filing_layout=filing.layout,
            persons_count=len(persons),
            enrollee_count=len(joint_aca_ages),
        )

        traditional_balance = _ledger_balances(ledger, household_mode=household_mode).traditional
        rmd_age = household_rmd_age(ages, rmd_start_ages)
        if rmd_age is not None:
            oldest = max(
                (name for name in ages if ages[name] >= rmd_start_ages[name]),
                key=lambda n: ages[n],
            )
            rmd_floor = compute_rmd(
                traditional_balance,
                rmd_age,
                rmd_start_age=rmd_start_ages[oldest],
            )

        assert filing.joint_filing_status is not None
        qcd_total = household_qcd_total(
            persons,
            ages=ages,
            inflation_factor=inflation_factor,
        )
        tax_table = tax_table_for_filing_status(
            filing.joint_filing_status,
            inflation_factor=inflation_factor,
        )
        (
            baseline_withdrawal,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
        ) = _solve_with_healthcare(
            living_expenses=living_expenses,
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental,
            earned_income=earned_income,
            ledger=ledger,
            household_mode=household_mode,
            filing_status=filing.joint_filing_status,
            inflation_factor=inflation_factor,
            tax=tax,
            tax_table=tax_table,
            rmd_floor=rmd_floor,
            state_code=state.code,
            medicare=medicare,
            medicare_ages=ages,
            irmaa_magi=irmaa_magi,
            aca=aca,
            aca_enrollee_ages_list=joint_aca_ages,
            aca_household_size=aca_household_size,
            medicare_ages_for_deduction=ages,
            qcd_amount=qcd_total,
            persons=persons,
            roth_conversion=0.0,
        )
        roth_conversion_total, person_conversions = _plan_roth_conversion(
            persons=persons,
            ages=ages,
            ledger=ledger,
            household_mode=household_mode,
            tax=tax,
            inflation_factor=inflation_factor,
            filing_status=filing.joint_filing_status,
            state_code=state.code,
            pension_gross=pension_gross,
            ss_gross=ss_gross,
            rental_gross=rental,
            earned_income=earned_income,
            baseline_withdrawal=baseline_withdrawal,
            rmd_floor=rmd_floor,
            qcd_amount=qcd_total,
            medicare_ages=ages,
        )
        (
            withdrawal_gross,
            federal_taxes,
            state_taxes,
            ss_taxable,
            total_taxes,
            medicare_cost,
            aca_cost,
            magi,
        ) = _solve_with_healthcare(
            living_expenses=living_expenses,
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental,
            earned_income=earned_income,
            ledger=ledger,
            household_mode=household_mode,
            filing_status=filing.joint_filing_status,
            inflation_factor=inflation_factor,
            tax=tax,
            tax_table=tax_table,
            rmd_floor=rmd_floor,
            state_code=state.code,
            medicare=medicare,
            medicare_ages=ages,
            irmaa_magi=irmaa_magi,
            aca=aca,
            aca_enrollee_ages_list=joint_aca_ages,
            aca_household_size=aca_household_size,
            medicare_ages_for_deduction=ages,
            qcd_amount=qcd_total,
            persons=persons,
            roth_conversion=roth_conversion_total,
        )
        return (
            withdrawal_gross,
            federal_taxes,
            state_taxes,
            ss_taxable,
            rmd_floor,
            total_taxes,
            medicare_cost,
            aca_cost,
            magi,
            person_withdrawals,
            qcd_total,
            roth_conversion_total,
            person_conversions,
        )

    roth_conversion_total = 0.0
    person_conversions: dict[str, float] = {}

    for name in persons:
        person_working = name in working
        person_retired = name in retired
        person_flows = flows[name]
        pension_gross = person_flows.pension if person_flows.pension_active else 0.0
        ss_gross = person_flows.ss if person_flows.ss_active else 0.0
        rental = person_flows.rental
        earned_income = person_flows.earned_income if person_working else 0.0
        living_expenses = (
            retirement_expense_for_person(
                person_flows,
                age=ages[name],
                year=year,
                medicare_start_age=medicare.start_age,
                aca_mode=aca.mode,
                person=persons[name],
                inflation_factor=inflation_factor,
                ltc_inflation_factor=ltc_inflation_factor,
                expense_policy=expense_policy,
            )
            if person_retired
            else 0.0
        )
        person_aca_ages = aca_enrollee_ages(
            retired=[name] if person_retired else [],
            ages=ages,
            medicare_start_age=medicare.start_age,
            persons=persons,
            inflation_factor=inflation_factor,
        )
        person_aca_household = aca_tax_household_size(
            filing_layout=filing.layout,
            persons_count=1,
            enrollee_count=len(person_aca_ages),
        )

        person_balances = ledger.person_share(name, persons)
        person_rmd = 0.0
        person_start = rmd_start_ages[name]
        if ages[name] >= person_start:
            person_rmd = compute_rmd(
                person_balances.traditional,
                ages[name],
                rmd_start_age=person_start,
            )
        rmd_floor += person_rmd

        person_filing = filing.person_filing_status[name]
        person_qcd = person_qcd_amount(
            persons[name],
            age=ages[name],
            inflation_factor=inflation_factor,
        )
        qcd_total += person_qcd
        tax_table = tax_table_for_filing_status(person_filing, inflation_factor=inflation_factor)
        person_medicare_ages = {name: ages[name]}
        person_ledger = HouseholdLedger(by_person={name: person_balances.copy()})
        (
            baseline_withdrawal,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
        ) = _solve_with_healthcare(
            living_expenses=living_expenses,
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental,
            earned_income=earned_income,
            ledger=person_ledger,
            household_mode=False,
            filing_status=person_filing,
            inflation_factor=inflation_factor,
            tax=tax,
            tax_table=tax_table,
            rmd_floor=person_rmd,
            state_code=state.code,
            medicare=medicare,
            medicare_ages=person_medicare_ages,
            irmaa_magi=irmaa_magi,
            aca=aca,
            aca_enrollee_ages_list=person_aca_ages,
            aca_household_size=person_aca_household,
            medicare_ages_for_deduction={name: ages[name]},
            qcd_amount=person_qcd,
            persons={name: persons[name]},
            roth_conversion=0.0,
        )
        person_roth, person_conv_map = _plan_roth_conversion(
            persons={name: persons[name]},
            ages={name: ages[name]},
            ledger=person_ledger,
            household_mode=False,
            tax=tax,
            inflation_factor=inflation_factor,
            filing_status=person_filing,
            state_code=state.code,
            pension_gross=pension_gross,
            ss_gross=ss_gross,
            rental_gross=rental,
            earned_income=earned_income,
            baseline_withdrawal=baseline_withdrawal,
            rmd_floor=person_rmd,
            qcd_amount=person_qcd,
            medicare_ages=person_medicare_ages,
        )
        person_conversions[name] = person_conv_map.get(name, 0.0)
        roth_conversion_total += person_roth
        (
            person_withdrawal,
            person_federal,
            person_state,
            person_ss_taxable,
            person_total,
            person_medicare,
            person_aca,
            person_magi,
        ) = _solve_with_healthcare(
            living_expenses=living_expenses,
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental,
            earned_income=earned_income,
            ledger=person_ledger,
            household_mode=False,
            filing_status=person_filing,
            inflation_factor=inflation_factor,
            tax=tax,
            tax_table=tax_table,
            rmd_floor=person_rmd,
            state_code=state.code,
            medicare=medicare,
            medicare_ages=person_medicare_ages,
            irmaa_magi=irmaa_magi,
            aca=aca,
            aca_enrollee_ages_list=person_aca_ages,
            aca_household_size=person_aca_household,
            medicare_ages_for_deduction={name: ages[name]},
            qcd_amount=person_qcd,
            persons={name: persons[name]},
            roth_conversion=person_roth,
        )
        person_withdrawals[name] = person_withdrawal
        withdrawal_gross += person_withdrawal
        federal_taxes += person_federal
        state_taxes += person_state
        total_taxes += person_total
        ss_taxable += person_ss_taxable
        medicare_cost = MedicareCost(
            total=medicare_cost.total + person_medicare.total,
            base=medicare_cost.base + person_medicare.base,
            irmaa=medicare_cost.irmaa + person_medicare.irmaa,
            medigap=medicare_cost.medigap + person_medicare.medigap,
            eligible_count=medicare_cost.eligible_count + person_medicare.eligible_count,
        )
        aca_cost = AcaCost(
            gross=aca_cost.gross + person_aca.gross,
            subsidy=aca_cost.subsidy + person_aca.subsidy,
            net=aca_cost.net + person_aca.net,
            enrollee_count=aca_cost.enrollee_count + person_aca.enrollee_count,
            fpl_pct=person_aca.fpl_pct,
            applicable_pct=person_aca.applicable_pct,
            slcsp_benchmark=aca_cost.slcsp_benchmark + person_aca.slcsp_benchmark,
        )
        magi += person_magi

    return (
        withdrawal_gross,
        federal_taxes,
        state_taxes,
        ss_taxable,
        rmd_floor,
        total_taxes,
        medicare_cost,
        aca_cost,
        magi,
        person_withdrawals,
        qcd_total,
        roth_conversion_total,
        person_conversions,
    )


def apply_market_events(
    assets: float,
    *,
    year: int,
    doomsday_year: int | None,
    doomsday_pct: float,
    rebalance: bool,
    tsla_target_year: int | None,
    tsla_target_price: float | None,
    tsla_current_price: float,
    current_return: float,
) -> tuple[float, float, list[str]]:
    notes: list[str] = []
    if doomsday_year and year == doomsday_year:
        assets *= 1 - doomsday_pct
        notes.append(f"Doomsday drop {doomsday_pct * 100:.0f}% applied")
        if rebalance:
            current_return = SP500_REBALANCE_RETURN

    if tsla_target_year and year == tsla_target_year and tsla_target_price:
        aggressive_portion = 0.7 * assets
        baseline = tsla_current_price if tsla_current_price > 0 else TSLA_BASELINE_PRICE
        target_growth = (tsla_target_price / baseline) - 1
        assets += aggressive_portion * target_growth
        notes.append(
            f"TSLA target growth to ${tsla_target_price:,.0f} (baseline ${baseline:,.2f})"
        )

    return assets, current_return, notes


def _apply_market_events_to_ledger(
    ledger: HouseholdLedger,
    *,
    household_mode: bool,
    year: int,
    doomsday_year: int | None,
    doomsday_pct: float,
    rebalance: bool,
    tsla_target_year: int | None,
    tsla_target_price: float | None,
    tsla_current_price: float,
    current_return: float,
) -> tuple[float, list[str]]:
    balances = _ledger_balances(ledger, household_mode=household_mode)
    total, current_return, notes = apply_market_events(
        balances.total,
        year=year,
        doomsday_year=doomsday_year,
        doomsday_pct=doomsday_pct,
        rebalance=rebalance,
        tsla_target_year=tsla_target_year,
        tsla_target_price=tsla_target_price,
        tsla_current_price=tsla_current_price,
        current_return=current_return,
    )
    if balances.total > 0 and abs(total - balances.total) > 0.01:
        factor = total / balances.total
        balances.taxable *= factor
        balances.traditional *= factor
        balances.roth *= factor
    return current_return, notes


def simulate_year(
    *,
    ledger: HouseholdLedger,
    household_mode: bool,
    persons: dict[str, dict[str, Any]],
    flows: dict[str, PersonFlows],
    ages: dict[str, int],
    year: int,
    year_return: float,
    tax: TaxPolicy,
    filing: YearFilingContext,
    state: YearStateContext,
    medicare: MedicarePolicy,
    aca: AcaPolicy | None = None,
    irmaa_magi: float | None = None,
    inflation_factor: float = 1.0,
    doomsday_year: int | None,
    doomsday_pct: float,
    rebalance: bool,
    tsla_target_year: int | None,
    tsla_target_price: float | None,
    tsla_current_price: float,
    current_return: float,
    anchor_year: int | None = None,
    expense_policy: ExpensePolicy | None = None,
    ltc_inflation_factor: float = 1.0,
    household_life_expectancy: int = 95,
    include_tax_metadata: bool = True,
) -> tuple[HouseholdLedger, YearResult]:
    if aca is None:
        aca = AcaPolicy()
    if expense_policy is None:
        expense_policy = ExpensePolicy()
    if anchor_year is None:
        anchor_year = year
    notes: list[str] = []
    balances_start = _ledger_balances(ledger, household_mode=household_mode)
    assets_start = balances_start.total

    living = living_person_names(
        persons,
        ages,
        shared_life_expectancy=household_life_expectancy,
    )
    deceased = [name for name in persons if name not in living]
    if deceased:
        notes.append(f"Deceased: {', '.join(deceased)}")
    filing = apply_survivor_filing(
        filing,
        household_mode=household_mode,
        living_count=len(living),
        survivor_status=expense_policy.survivor_filing_status,
        living_names=living,
    )

    current_return, event_notes = _apply_market_events_to_ledger(
        ledger,
        household_mode=household_mode,
        year=year,
        doomsday_year=doomsday_year,
        doomsday_pct=doomsday_pct,
        rebalance=rebalance,
        tsla_target_year=tsla_target_year,
        tsla_target_price=tsla_target_price,
        tsla_current_price=tsla_current_price,
        current_return=current_return,
    )
    notes.extend(event_notes)

    working = [
        name
        for name in living
        if ages[name] < int(persons[name].get("retirement_age", 65))
    ]
    retired = [
        name
        for name in living
        if ages[name] >= int(persons[name].get("retirement_age", 65))
    ]

    if not retired:
        phase = "accumulation"
    elif working:
        phase = "partial_retirement"
    else:
        phase = "full_retirement"

    contribution_deposit = 0.0
    traditional_contrib = 0.0
    roth_contrib = 0.0
    person_contributions: dict[str, tuple[float, float, float]] = {}
    for name in working:
        deposited, traditional, roth = split_contributions(
            flows[name].contributions,
            roth_pct=tax.contribution_roth_pct,
            pre_tax_rate=tax.pre_retire_rate,
        )
        contribution_deposit += deposited
        traditional_contrib += traditional
        roth_contrib += roth
        person_contributions[name] = (deposited, traditional, roth)

    activate_benefits(persons, flows, ages)

    pension_gross = sum(
        flows[name].pension for name in living if flows[name].pension_active
    )
    ss_gross = sum(flows[name].ss for name in living if flows[name].ss_active)
    rental = sum(flows[name].rental for name in living)
    gross_benefits = pension_gross + ss_gross + rental

    expense_phases: list[str] = []
    retirement_expenses = 0.0
    for name in retired:
        retirement_expenses += retirement_expense_for_person(
            flows[name],
            age=ages[name],
            year=year,
            medicare_start_age=medicare.start_age,
            aca_mode=aca.mode,
            person=persons[name],
            inflation_factor=inflation_factor,
            ltc_inflation_factor=ltc_inflation_factor,
            expense_policy=expense_policy,
        )
        _, phase_label = person_retirement_living_expense(
            flows[name].expenses,
            persons[name],
            age=ages[name],
            year=year,
            retirement_age=int(persons[name].get("retirement_age", 65) or 65),
            policy=expense_policy,
            inflation_factor=inflation_factor,
        )
        expense_phases.append(phase_label)
    if len(set(expense_phases)) == 1:
        expense_phase = expense_phases[0] if expense_phases else "flat"
    elif expense_phases:
        expense_phase = "mixed"
    else:
        expense_phase = "flat"

    employer_health = household_employer_health(
        {name: persons[name] for name in living},
        ages={name: ages[name] for name in living},
        medicare_start_age=medicare.start_age,
        inflation_factor=inflation_factor,
    )
    additive_totals = {
        key: sum(
            retirement_additive_amounts(
                flows[name],
                age=ages[name],
                medicare_start_age=medicare.start_age,
                person=persons[name],
                retirement_age=int(persons[name].get("retirement_age", 65) or 65),
                ltc_inflation_factor=ltc_inflation_factor,
            )[key]
            for name in retired
        )
        for key in RETIREMENT_EXPENSE_ADDITIVE_KEYS
    }
    ltc_cost = additive_totals.get("long_term_care_annual", 0.0)
    health_post_medicare = additive_totals.get("health_insurance_post_retire", 0.0)
    coverage_phase = medicare_coverage_phase(
        retired=retired,
        ages=ages,
        medicare_start_age=medicare.start_age,
    )

    earned_income = sum(flows[name].earned_income for name in working)

    from model.ss import ss_benefit_detail

    ss_pia_monthly = 0.0
    ss_fra_years = 0.0
    ss_detail_notes: list[str] = []
    for name, person in persons.items():
        detail = ss_benefit_detail(
            person,
            current_year=anchor_year or year,
            inflation_factor=inflation_factor,
        )
        if detail["mode"] != "manual":
            ss_pia_monthly += float(detail["pia_monthly"])
            ss_fra_years = max(ss_fra_years, float(detail["fra_years"]))
            ss_detail_notes.append(
                f"{name}: PIA ${detail['pia_monthly']:,.0f}/mo at FRA "
                f"{detail['fra_years']:.2f}, claim age {detail['claiming_age']} "
                f"→ ${detail['annual_benefit']:,.0f}/yr"
            )

    (
        withdrawal_gross,
        federal_taxes,
        state_taxes,
        ss_taxable,
        rmd_floor,
        total_taxes,
        medicare_cost,
        aca_cost,
        magi,
        person_withdrawals,
        qcd_total,
        roth_conversion_total,
        person_conversions,
    ) = _solve_cash_flow_taxes(
        ledger=ledger,
        household_mode=household_mode,
        persons=persons,
        flows=flows,
        ages=ages,
        working=working,
        retired=retired,
        year=year,
        tax=tax,
        inflation_factor=inflation_factor,
        ltc_inflation_factor=ltc_inflation_factor,
        expense_policy=expense_policy,
        filing=filing,
        state=state,
        medicare=medicare,
        aca=aca,
        irmaa_magi=irmaa_magi,
        anchor_year=anchor_year,
    )
    work_taxes = _working_payroll_taxes(
        persons=persons,
        flows=flows,
        ages=ages,
        working=working,
        retired=retired,
        tax=tax,
        filing=filing,
        state=state,
        inflation_factor=inflation_factor,
    )
    federal_taxes += work_taxes.federal
    state_taxes += work_taxes.state
    fica_taxes = work_taxes.fica
    total_taxes += work_taxes.federal + work_taxes.state + work_taxes.fica
    if aca.mode == "aca_subsidized":
        additive_totals["health_insurance_pre_medicare"] = aca_cost.net
    health_pre_medicare = additive_totals.get("health_insurance_pre_medicare", 0.0)
    rmd_start_ages = _rmd_start_ages(persons, anchor_year=anchor_year, tax=tax)
    rmd_age = household_rmd_age(ages, rmd_start_ages)

    apply_contributions(
        ledger,
        household_mode=household_mode,
        person_contributions=person_contributions,
    )
    returns = apply_returns(
        ledger,
        household_mode=household_mode,
        return_rate=year_return,
        contribution_deposit=contribution_deposit,
    )
    roth_conversion_applied = apply_roth_conversion_to_ledger(
        ledger,
        household_mode=household_mode,
        person_amounts=person_conversions,
    )
    if filing.layout == "joint_return":
        apply_withdrawal(
            ledger,
            household_mode=household_mode,
            gross=withdrawal_gross,
            order=tax.withdrawal_order,
            rmd_floor=rmd_floor,
            qcd=qcd_total,
        )
    else:
        for name, person_withdrawal in person_withdrawals.items():
            person_rmd = 0.0
            person_start = rmd_start_ages[name]
            if ages[name] >= person_start:
                person_balances = ledger.person_share(name, persons)
                person_rmd = compute_rmd(
                    person_balances.traditional,
                    ages[name],
                    rmd_start_age=person_start,
                )
            person_qcd = person_qcd_amount(
                persons[name],
                age=ages[name],
                inflation_factor=inflation_factor,
            )
            apply_withdrawal(
                ledger,
                household_mode=False,
                gross=person_withdrawal,
                person_name=name,
                order=tax.withdrawal_order,
                rmd_floor=person_rmd,
                qcd=person_qcd,
            )

    balances_end = _ledger_balances(ledger, household_mode=household_mode)
    assets_end = balances_end.total
    roth_fraction = balances_end.roth_fraction()

    notes.append(
        f"Phase={phase}; tax={tax.tax_calculation}; filing={filing.label} ({filing.layout}); "
        f"returns on start balance + 50% of contributions; Roth mix={roth_fraction:.1%}"
    )
    if balances_end.taxable > 0:
        notes.append(
            f"Account mix end: taxable ${balances_end.taxable:,.0f}, "
            f"traditional ${balances_end.traditional:,.0f}, Roth ${balances_end.roth:,.0f}"
        )
    if tax.marriage_year and year == tax.marriage_year:
        notes.append(
            f"Marriage year {tax.marriage_year}: filing transitions to {tax.filing_status_after_marriage}"
        )
    if tax.state_move_year and year == tax.state_move_year:
        notes.append(
            f"State move year {tax.state_move_year}: residence {tax.state_residence} → {tax.state_after_move}"
        )
    if state_taxes > 0 or tax.state_tax_enabled:
        notes.append(f"State tax ({state.label}): ${state_taxes:,.0f}")
    if earned_income > 0:
        notes.append(f"Earned income covers working-year living costs: ${earned_income:,.0f}")
    if fica_taxes > 0:
        notes.append(f"Payroll taxes (FICA): ${fica_taxes:,.0f}")
    if qcd_total > 0:
        notes.append(f"Qualified charitable distributions (QCD): ${qcd_total:,.0f}")
    if roth_conversion_applied > 0:
        notes.append(
            f"Roth conversion: ${roth_conversion_applied:,.0f} "
            f"(traditional → Roth, taxable ordinary income)"
        )
    if tax.withdrawal_order != "proportional" and withdrawal_gross > 0:
        notes.append(f"Withdrawal order: {tax.withdrawal_order} (taxable → traditional → Roth)")
    if rmd_floor > 0:
        notes.append(f"RMD floor (age {rmd_age}): ${rmd_floor:,.0f} from traditional balance")
    if ss_detail_notes:
        notes.append("SS calculated: " + "; ".join(ss_detail_notes))
    if ss_gross > 0 and ss_taxable > 0:
        notes.append(
            f"SS taxation ({tax.ss_tax_method}): ${ss_taxable:,.0f} of ${ss_gross:,.0f} benefits taxable"
        )
    if traditional_contrib or roth_contrib:
        notes.append(
            f"Contributions: ${traditional_contrib:,.0f} pre-tax + ${roth_contrib:,.0f} Roth after-tax"
        )
    if employer_health > 0:
        notes.append(f"Employer/COBRA health (employee share): ${employer_health:,.0f}")
    if coverage_phase == "pre_medicare_gap" and (health_pre_medicare > 0 or aca_cost.net > 0):
        if aca.mode == "aca_subsidized" and aca_cost.net > 0:
            notes.append(
                f"Pre-Medicare ACA ({aca_cost.enrollee_count} enrolled): "
                f"net ${aca_cost.net:,.0f} (plan ${aca_cost.gross:,.0f}, "
                f"SLCSP ${aca_cost.slcsp_benchmark:,.0f}, "
                f"subsidy ${aca_cost.subsidy:,.0f}, {aca_cost.fpl_pct:.0f}% FPL)"
            )
        elif health_pre_medicare > 0:
            notes.append(
                f"Pre-Medicare gap (retired, age < {medicare.start_age}): "
                f"private/ACA coverage ${health_pre_medicare:,.0f}; Medicare premiums $0"
            )
    elif coverage_phase == "mixed_pre_and_medicare":
        aca_note = (
            f"ACA net ${aca_cost.net:,.0f} (subsidy ${aca_cost.subsidy:,.0f})"
            if aca.mode == "aca_subsidized" and aca_cost.net > 0
            else f"pre-Medicare gap ${health_pre_medicare:,.0f}"
        )
        notes.append(
            f"Mixed household: {aca_note}, "
            f"Medicare supplemental ${health_post_medicare:,.0f}"
        )
    if medicare_cost.total > 0:
        medigap_note = (
            f", Medigap ${medicare_cost.medigap:,.0f}"
            if medicare_cost.medigap > 0
            else ""
        )
        notes.append(
            f"Medicare ({medicare_cost.eligible_count} enrolled): "
            f"${medicare_cost.total:,.0f} "
            f"(base ${medicare_cost.base:,.0f}, IRMAA ${medicare_cost.irmaa:,.0f}{medigap_note})"
        )
    elif coverage_phase == "medicare_enrolled" and medicare.enabled:
        notes.append(f"Medicare eligible (age ≥ {medicare.start_age}) but premiums $0 (disabled or no enrollment)")
    if medicare.irmaa_lookback_years > 0 and irmaa_magi is not None:
        notes.append(f"IRMAA uses MAGI from {medicare.irmaa_lookback_years} year(s) prior: ${irmaa_magi:,.0f}")
    if state.fallback and tax.planning_precision == "standard":
        from model.state_taxes import state_fallback_message

        fallback_msg = state_fallback_message(state.code)
        if fallback_msg:
            notes.append(fallback_msg)

    deduction_method = "standard"
    amt_surcharge = 0.0
    federal_deduction = 0.0
    if tax.planning_precision == "cpa" and work_taxes.federal > 0:
        deduction_method = work_taxes.deduction_method
        federal_deduction = work_taxes.federal_deduction
        amt_surcharge = work_taxes.amt_surcharge
        notes.append(
            f"CPA working-year: {deduction_method} deduction ${federal_deduction:,.0f}"
        )
        if amt_surcharge > 0:
            notes.append(f"AMT surcharge (W-2) ${amt_surcharge:,.0f}")

    if include_tax_metadata and tax.planning_precision == "cpa" and phase != "accumulation":
        from model.taxes import compute_retirement_tax_detail, tax_table_for_filing_status

        if filing.layout == "joint_return":
            cpa_filing_status = filing.joint_filing_status or tax.filing_status
        else:
            cpa_filing_status = next(iter(filing.person_filing_status.values()))
        cpa_table = tax_table_for_filing_status(
            cpa_filing_status,
            inflation_factor=inflation_factor,
        )
        cpa_seniors = seniors_65_count_for_filing(ages, filing_status=cpa_filing_status)
        cpa_detail = compute_retirement_tax_detail(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental,
            earned_income=earned_income,
            withdrawal_gross=withdrawal_gross,
            roth_fraction=roth_fraction_for_tax(ledger, household_mode=household_mode),
            filing_status=cpa_filing_status,
            inflation_factor=inflation_factor,
            ss_tax_method=tax.ss_tax_method,
            ss_taxable_fraction=tax.ss_taxable_fraction,
            tax_mode=tax.tax_calculation,
            flat_rate=tax.post_retire_rate,
            table=cpa_table,
            state_code=state.code,
            state_tax_enabled=tax.state_tax_enabled,
            state_tax_override_pct=tax.state_tax_override_pct,
            taxable_portfolio_fraction=_taxable_portfolio_fraction(
                ledger,
                household_mode=household_mode,
            ),
            taxable_gain_fraction=tax.taxable_gain_fraction,
            seniors_65_count=cpa_seniors,
            mfs_lived_apart=tax.mfs_lived_apart,
            niit_enabled=tax.niit_enabled,
            qcd_exclusion=qcd_total,
            roth_conversion=roth_conversion_applied,
            planning_precision="cpa",
            itemized=tax.itemized,
        )
        deduction_method = cpa_detail.deduction_method
        amt_surcharge = cpa_detail.amt_surcharge
        federal_deduction = cpa_detail.federal_deduction
        notes.append(
            f"CPA precision: {deduction_method} deduction ${federal_deduction:,.0f}"
        )
        if amt_surcharge > 0:
            notes.append(f"AMT surcharge ${amt_surcharge:,.0f}")

    return ledger, YearResult(
        contributions=contribution_deposit,
        returns=returns,
        withdrawals_gross=withdrawal_gross,
        rmd=rmd_floor,
        earned_income=earned_income,
        expenses=retirement_expenses,
        rental_income=rental,
        pension=pension_gross,
        ss_gross=ss_gross,
        ss_pia_monthly=ss_pia_monthly,
        ss_fra_years=ss_fra_years,
        ss_taxable=ss_taxable,
        other_income=gross_benefits,
        taxes=total_taxes,
        federal_taxes=federal_taxes,
        state_taxes=state_taxes,
        assets_end=assets_end,
        additive_totals=additive_totals,
        phase=phase,
        tax_mode=tax.tax_calculation,
        filing_layout=filing.layout,
        filing_status_label=filing.label,
        tax_state_label=state.label,
        medicare_total=medicare_cost.total,
        medicare_irmaa=medicare_cost.irmaa,
        medicare_phase=coverage_phase,
        health_pre_medicare=health_pre_medicare,
        health_post_medicare=health_post_medicare,
        aca_gross=aca_cost.gross,
        aca_subsidy=aca_cost.subsidy,
        aca_net=aca_cost.net,
        aca_slcsp=aca_cost.slcsp_benchmark,
        employer_health=employer_health,
        medicare_medigap=medicare_cost.medigap,
        magi=magi,
        assets_taxable=balances_end.taxable,
        assets_traditional=balances_end.traditional,
        assets_roth=balances_end.roth,
        fica_taxes=fica_taxes,
        qcd=qcd_total,
        roth_conversion=roth_conversion_applied,
        ltc_cost=ltc_cost,
        expense_phase=expense_phase,
        planning_precision=tax.planning_precision,
        deduction_method=deduction_method,
        amt_surcharge=amt_surcharge,
        federal_deduction=federal_deduction,
        notes=notes,
    )


def additive_columns(additive_totals: dict[str, float]) -> dict[str, float]:
    return {
        FIELDS_BY_KEY[key].label.replace(" ", "_"): value
        for key, value in additive_totals.items()
    }
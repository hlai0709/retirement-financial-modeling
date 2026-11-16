"""Federal income tax brackets and Social Security taxation for retirement modeling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.policy.tables import (
    MFS_SS_BASE_LIVED_APART_2024,
    MFS_SS_TIER2_LIVED_APART_2024,
    MFJ_ADDITIONAL_SD_65_2024,
    NIIT_RATE,
    NIIT_MFJ_THRESHOLD_2024,
    NIIT_MFS_THRESHOLD_2024,
    NIIT_SINGLE_THRESHOLD_2024,
    SINGLE_ADDITIONAL_SD_65_2024,
    policy_for_projection,
)

VALID_SS_TAX_METHODS = frozenset({"irs_provisional", "flat_fraction"})

if TYPE_CHECKING:
    from model.cpa_tax import ItemizedInputs

# 2024 inflation-adjusted brackets (reasonable baseline for 2026 planning)
MFJ_STANDARD_DEDUCTION_2024 = 29_200.0
SINGLE_STANDARD_DEDUCTION_2024 = 14_600.0
HOH_STANDARD_DEDUCTION_2024 = 21_900.0

# (cumulative taxable income ceiling, marginal rate)
MFJ_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (23_200, 0.10),
    (94_300, 0.12),
    (201_050, 0.22),
    (383_900, 0.24),
    (487_450, 0.32),
    (731_200, 0.35),
    (float("inf"), 0.37),
)

# IRS Pub. 915 combined-income thresholds (2024 base year)
MFJ_SS_BASE_2024 = 32_000.0
MFJ_SS_TIER2_2024 = 44_000.0
SINGLE_SS_BASE_2024 = 25_000.0
SINGLE_SS_TIER2_2024 = 34_000.0

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

VALID_FILING_STATUSES = frozenset({
    "mfj",
    "single",
    "head_of_household",
    "hoh",
    "mfs",
    "married_filing_separately",
})


def normalize_filing_status(status: str) -> str:
    normalized = (status or "mfj").strip().lower()
    if normalized == "hoh":
        return "head_of_household"
    if normalized == "married_filing_separately":
        return "mfs"
    return normalized


@dataclass(frozen=True)
class TaxTable:
    standard_deduction: float
    brackets: tuple[tuple[float, float], ...]
    ltcg_tiers: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class RetirementTaxDetail:
    federal: float
    state: float
    ss_taxable: float
    deduction_method: str = "standard"
    amt_surcharge: float = 0.0
    federal_deduction: float = 0.0


@dataclass(frozen=True)
class WithdrawalIncomeSplit:
    ordinary: float
    ltcg: float
    roth: float

    @property
    def gross(self) -> float:
        return self.ordinary + self.ltcg + self.roth


def _inflate_brackets(
    brackets: tuple[tuple[float, float], ...],
    inflation_factor: float,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (ceiling * inflation_factor if ceiling != float("inf") else float("inf"), rate)
        for ceiling, rate in brackets
    )


def tax_table_for_filing_status(status: str, *, inflation_factor: float = 1.0) -> TaxTable:
    policy = policy_for_projection(inflation_factor=inflation_factor)
    status = normalize_filing_status(status)
    if status in {"single", "mfs"}:
        return TaxTable(
            standard_deduction=policy.single_standard_deduction,
            brackets=policy.single_brackets,
            ltcg_tiers=policy.single_ltcg_tiers,
        )
    if status == "head_of_household":
        return TaxTable(
            standard_deduction=policy.hoh_standard_deduction,
            brackets=policy.hoh_brackets,
            ltcg_tiers=policy.hoh_ltcg_tiers,
        )
    return TaxTable(
        standard_deduction=policy.mfj_standard_deduction,
        brackets=policy.mfj_brackets,
        ltcg_tiers=policy.mfj_ltcg_tiers,
    )


def additional_standard_deduction_65(
    filing_status: str,
    *,
    seniors_65_count: int,
    inflation_factor: float = 1.0,
) -> float:
    """Extra standard deduction for taxpayers age 65+ (2024 Schedule A rules)."""
    if seniors_65_count <= 0:
        return 0.0
    policy = policy_for_projection(inflation_factor=inflation_factor)
    status = normalize_filing_status(filing_status)
    if status == "mfj":
        return policy.mfj_additional_sd_65 * min(seniors_65_count, 2)
    return policy.single_additional_sd_65 * min(seniors_65_count, 1)


def seniors_65_count_for_filing(
    ages: dict[str, int],
    *,
    filing_status: str,
) -> int:
    eligible = sum(1 for age in ages.values() if age >= 65)
    status = normalize_filing_status(filing_status)
    if status == "mfj":
        return eligible
    return min(eligible, 1)


def income_split_from_allocation(
    allocation,
    *,
    taxable_gain_fraction: float,
    qcd_exclusion: float = 0.0,
) -> WithdrawalIncomeSplit:
    """Tax characterization from an explicit account withdrawal allocation."""
    gain_frac = max(0.0, min(taxable_gain_fraction, 1.0))
    ltcg = allocation.from_taxable * gain_frac
    trad_taxable = max(0.0, allocation.from_traditional - qcd_exclusion)
    return WithdrawalIncomeSplit(ordinary=trad_taxable, ltcg=ltcg, roth=allocation.from_roth)


def withdrawal_income_split(
    withdrawal_gross: float,
    *,
    roth_fraction: float,
    taxable_portfolio_fraction: float = 0.0,
    taxable_gain_fraction: float = 0.5,
) -> WithdrawalIncomeSplit:
    """Split gross withdrawal into ordinary, LTCG, and tax-free Roth portions."""
    if withdrawal_gross <= 0:
        return WithdrawalIncomeSplit(0.0, 0.0, 0.0)
    roth_share = max(0.0, min(roth_fraction, 1.0))
    taxable_share = max(0.0, min(taxable_portfolio_fraction, 1.0 - roth_share))
    traditional_share = max(0.0, 1.0 - roth_share - taxable_share)
    gain_frac = max(0.0, min(taxable_gain_fraction, 1.0))
    from_roth = withdrawal_gross * roth_share
    from_taxable = withdrawal_gross * taxable_share
    from_traditional = withdrawal_gross * traditional_share
    ltcg = from_taxable * gain_frac
    ordinary = from_traditional + from_taxable * (1.0 - gain_frac)
    return WithdrawalIncomeSplit(ordinary=ordinary, ltcg=ltcg, roth=from_roth)


def ss_provisional_thresholds(
    filing_status: str,
    *,
    inflation_factor: float = 1.0,
    mfs_lived_apart: bool = False,
) -> tuple[float, float]:
    """Return (base_threshold, tier2_threshold) for IRS combined-income tiers."""
    status = normalize_filing_status(filing_status)
    if status == "mfs":
        if mfs_lived_apart:
            return (
                MFS_SS_BASE_LIVED_APART_2024 * inflation_factor,
                MFS_SS_TIER2_LIVED_APART_2024 * inflation_factor,
            )
        return (0.0, 0.0)
    if status in {"single", "head_of_household"}:
        return (
            SINGLE_SS_BASE_2024 * inflation_factor,
            SINGLE_SS_TIER2_2024 * inflation_factor,
        )
    return (
        MFJ_SS_BASE_2024 * inflation_factor,
        MFJ_SS_TIER2_2024 * inflation_factor,
    )


def taxable_social_security_irs(
    ss_gross: float,
    *,
    other_income: float,
    filing_status: str,
    inflation_factor: float = 1.0,
    mfs_lived_apart: bool = False,
) -> float:
    """
    IRS provisional-income worksheet (Pub. 915).

    Combined income = other taxable income + 50% of Social Security benefits.
    """
    if ss_gross <= 0:
        return 0.0

    status = normalize_filing_status(filing_status)
    if status == "mfs" and not mfs_lived_apart:
        combined = other_income + 0.5 * ss_gross
        if combined <= 0:
            return 0.0
        return min(0.85 * ss_gross, 0.85 * combined)

    base, tier2 = ss_provisional_thresholds(
        filing_status,
        inflation_factor=inflation_factor,
        mfs_lived_apart=mfs_lived_apart,
    )
    combined = other_income + 0.5 * ss_gross
    tier2_cap = 0.5 * (tier2 - base)

    if combined <= base:
        return 0.0
    if combined <= tier2:
        return min(0.5 * ss_gross, 0.5 * (combined - base))

    tier2_amount = min(0.5 * ss_gross, tier2_cap)
    return min(0.85 * ss_gross, 0.85 * (combined - tier2) + tier2_amount)


def federal_income_tax(taxable_income: float, table: TaxTable) -> float:
    """Progressive tax on taxable income after standard deduction."""
    if taxable_income <= 0:
        return 0.0
    tax = 0.0
    lower = 0.0
    for ceiling, rate in table.brackets:
        if taxable_income <= lower:
            break
        upper = min(taxable_income, ceiling)
        tax += (upper - lower) * rate
        lower = ceiling
    return tax


def federal_ltcg_tax(
    ltcg: float,
    *,
    ordinary_taxable_income: float,
    ltcg_tiers: tuple[tuple[float, float], ...],
) -> float:
    """Preferential LTCG rates stacked on ordinary taxable income."""
    if ltcg <= 0 or not ltcg_tiers:
        return 0.0
    tax = 0.0
    remaining = ltcg
    stack = max(0.0, ordinary_taxable_income)
    for ceiling, rate in ltcg_tiers:
        room = max(0.0, ceiling - stack)
        if room <= 0:
            stack = max(stack, ceiling)
            continue
        chunk = min(remaining, room)
        tax += chunk * rate
        remaining -= chunk
        stack += chunk
        if remaining <= 0:
            return tax
    if remaining > 0:
        tax += remaining * ltcg_tiers[-1][1]
    return tax


def federal_tax_with_ltcg(
    *,
    ordinary_income: float,
    ltcg: float,
    table: TaxTable,
    seniors_65_count: int = 0,
    inflation_factor: float = 1.0,
    filing_status: str = "mfj",
) -> tuple[float, float, float]:
    """Return (total federal, ordinary portion, ltcg portion)."""
    extra_sd = additional_standard_deduction_65(
        filing_status,
        seniors_65_count=seniors_65_count,
        inflation_factor=inflation_factor,
    )
    total_deduction = table.standard_deduction + extra_sd
    ordinary_taxable = max(0.0, ordinary_income - total_deduction)
    if ltcg <= 0:
        return federal_income_tax(ordinary_taxable, table), federal_income_tax(
            ordinary_taxable, table
        ), 0.0
    if ordinary_taxable <= 0:
        ord_tax = 0.0
        ltcg_tax = federal_ltcg_tax(
            ltcg,
            ordinary_taxable_income=0.0,
            ltcg_tiers=table.ltcg_tiers,
        )
        return ord_tax + ltcg_tax, ord_tax, ltcg_tax
    ord_tax = federal_income_tax(ordinary_taxable, table)
    ltcg_tax = federal_ltcg_tax(
        ltcg,
        ordinary_taxable_income=ordinary_taxable,
        ltcg_tiers=table.ltcg_tiers,
    )
    return ord_tax + ltcg_tax, ord_tax, ltcg_tax


def compute_niit(
    *,
    magi: float,
    net_investment_income: float,
    filing_status: str,
    inflation_factor: float = 1.0,
    enabled: bool = True,
) -> float:
    if not enabled or net_investment_income <= 0:
        return 0.0
    policy = policy_for_projection(inflation_factor=inflation_factor)
    status = normalize_filing_status(filing_status)
    if status == "mfj":
        threshold = policy.niit_mfj_threshold
    elif status == "mfs":
        threshold = policy.niit_mfs_threshold
    else:
        threshold = policy.niit_single_threshold
    excess = max(0.0, magi - threshold)
    base = min(net_investment_income, excess)
    return base * NIIT_RATE


def other_taxable_income(
    *,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    withdrawal_gross: float,
    roth_fraction: float,
) -> float:
    traditional_withdrawal = withdrawal_gross * max(0.0, 1.0 - roth_fraction)
    return pension_gross + rental_gross + earned_income + traditional_withdrawal


def taxable_social_security(
    ss_gross: float,
    *,
    other_income: float,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
    mfs_lived_apart: bool = False,
) -> float:
    method = (ss_tax_method or "irs_provisional").strip().lower()
    if method == "flat_fraction":
        return ss_gross * ss_taxable_fraction
    return taxable_social_security_irs(
        ss_gross,
        other_income=other_income,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        mfs_lived_apart=mfs_lived_apart,
    )


def gross_taxable_income(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    withdrawal_gross: float,
    roth_fraction: float,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
) -> tuple[float, float]:
    """Return (total taxable income, taxable Social Security portion)."""
    other = other_taxable_income(
        pension_gross=pension_gross,
        rental_gross=rental_gross,
        earned_income=earned_income,
        withdrawal_gross=withdrawal_gross,
        roth_fraction=roth_fraction,
    )
    ss_taxable = taxable_social_security(
        ss_gross,
        other_income=other,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        ss_tax_method=ss_tax_method,
        ss_taxable_fraction=ss_taxable_fraction,
    )
    return other + ss_taxable, ss_taxable


def compute_retirement_tax(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float = 0.0,
    withdrawal_gross: float,
    roth_fraction: float,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
    tax_mode: str,
    flat_rate: float,
    table: TaxTable,
    state_code: str = "CA",
    state_tax_enabled: bool = True,
    state_tax_override_pct: float | None = None,
) -> float:
    detail = compute_retirement_tax_detail(
        ss_gross=ss_gross,
        pension_gross=pension_gross,
        rental_gross=rental_gross,
        earned_income=earned_income,
        withdrawal_gross=withdrawal_gross,
        roth_fraction=roth_fraction,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        ss_tax_method=ss_tax_method,
        ss_taxable_fraction=ss_taxable_fraction,
        tax_mode=tax_mode,
        flat_rate=flat_rate,
        table=table,
        state_code=state_code,
        state_tax_enabled=state_tax_enabled,
        state_tax_override_pct=state_tax_override_pct,
    )
    return detail.federal + detail.state


def compute_retirement_tax_detail(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float = 0.0,
    withdrawal_gross: float,
    roth_fraction: float,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
    tax_mode: str,
    flat_rate: float,
    table: TaxTable,
    state_code: str = "CA",
    state_tax_enabled: bool = True,
    state_tax_override_pct: float | None = None,
    taxable_portfolio_fraction: float = 0.0,
    taxable_gain_fraction: float = 0.5,
    seniors_65_count: int = 0,
    mfs_lived_apart: bool = False,
    niit_enabled: bool = True,
    withdrawal_allocation=None,
    qcd_exclusion: float = 0.0,
    roth_conversion: float = 0.0,
    planning_precision: str = "standard",
    itemized: ItemizedInputs | None = None,
) -> RetirementTaxDetail:
    """Return federal (incl. NIIT/AMT), state, and taxable Social Security detail."""
    from model.state_taxes import compute_state_income_tax, state_profile

    from model.cpa_tax import ItemizedInputs, federal_tax_cpa_precision, normalize_planning_precision

    precision = normalize_planning_precision(planning_precision)
    itemized_inputs = itemized or ItemizedInputs()

    if withdrawal_allocation is not None:
        split = income_split_from_allocation(
            withdrawal_allocation,
            taxable_gain_fraction=taxable_gain_fraction,
            qcd_exclusion=qcd_exclusion,
        )
    else:
        split = withdrawal_income_split(
            withdrawal_gross,
            roth_fraction=roth_fraction,
            taxable_portfolio_fraction=taxable_portfolio_fraction,
            taxable_gain_fraction=taxable_gain_fraction,
        )
    other_ordinary = (
        pension_gross + rental_gross + earned_income + split.ordinary + roth_conversion
    )
    ss_taxable = taxable_social_security(
        ss_gross,
        other_income=other_ordinary,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        ss_tax_method=ss_tax_method,
        ss_taxable_fraction=ss_taxable_fraction,
        mfs_lived_apart=mfs_lived_apart,
    )
    ordinary_income = other_ordinary + ss_taxable
    gross_taxable = ordinary_income + split.ltcg

    deduction_method = "standard"
    amt_surcharge = 0.0
    federal_deduction = 0.0

    if tax_mode == "federal_brackets":
        if precision == "cpa":
            agi = (
                earned_income
                + pension_gross
                + rental_gross
                + split.ordinary
                + split.ltcg
                + ss_taxable
                + roth_conversion
            )
            cpa = federal_tax_cpa_precision(
                ordinary_income=ordinary_income,
                ltcg=split.ltcg,
                table=table,
                filing_status=filing_status,
                seniors_65_count=seniors_65_count,
                inflation_factor=inflation_factor,
                itemized=itemized_inputs,
                agi=agi,
            )
            federal = cpa.total_federal
            deduction_method = cpa.deduction_method
            amt_surcharge = cpa.amt_surcharge
            federal_deduction = cpa.deduction_used
        else:
            federal, _, _ = federal_tax_with_ltcg(
                ordinary_income=ordinary_income,
                ltcg=split.ltcg,
                table=table,
                seniors_65_count=seniors_65_count,
                inflation_factor=inflation_factor,
                filing_status=filing_status,
            )
    else:
        federal = gross_taxable * flat_rate

    magi = (
        earned_income
        + pension_gross
        + rental_gross
        + split.ordinary
        + split.ltcg
        + ss_taxable
        + roth_conversion
    )
    net_investment_income = split.ltcg + rental_gross
    federal += compute_niit(
        magi=magi,
        net_investment_income=net_investment_income,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        enabled=niit_enabled,
    )

    state = 0.0
    if state_tax_enabled:
        profile = state_profile(state_code, strict=(precision == "cpa"))
        if profile is not None:
            state = compute_state_income_tax(
                gross_taxable,
                ss_taxable=ss_taxable,
                profile=profile,
                filing_status=filing_status,
                inflation_factor=inflation_factor,
                override_flat_rate=state_tax_override_pct,
            )
        elif state_tax_override_pct is not None:
            from model.state_taxes import StateTaxProfile

            state = compute_state_income_tax(
                gross_taxable,
                ss_taxable=ss_taxable,
                profile=StateTaxProfile(code="NONE", name="Override", kind="none"),
                filing_status=filing_status,
                inflation_factor=inflation_factor,
                override_flat_rate=state_tax_override_pct,
            )
    return RetirementTaxDetail(
        federal=federal,
        state=state,
        ss_taxable=ss_taxable,
        deduction_method=deduction_method,
        amt_surcharge=amt_surcharge,
        federal_deduction=federal_deduction,
    )


def spendable_cash(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    withdrawal_gross: float,
    tax: float,
) -> float:
    """Cash available for spending after paying income taxes from income sources."""
    return ss_gross + pension_gross + rental_gross + withdrawal_gross - tax


def solve_withdrawal_for_expenses(
    expenses: float,
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float = 0.0,
    roth_fraction: float,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
    tax_mode: str,
    flat_rate: float,
    table: TaxTable,
    rmd_floor: float,
    state_code: str = "CA",
    state_tax_enabled: bool = True,
    state_tax_override_pct: float | None = None,
    taxable_portfolio_fraction: float = 0.0,
    taxable_gain_fraction: float = 0.5,
    seniors_65_count: int = 0,
    mfs_lived_apart: bool = False,
    niit_enabled: bool = True,
    account_balances=None,
    withdrawal_order: str = "tax_efficient",
    qcd_amount: float = 0.0,
    roth_conversion: float = 0.0,
    planning_precision: str = "standard",
    itemized: ItemizedInputs | None = None,
) -> tuple[float, float, float, float, float]:
    """Find gross portfolio withdrawal covering expenses and RMD floor."""
    if expenses <= 0 and rmd_floor <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    gross_benefits = ss_gross + pension_gross + rental_gross

    def taxes_for(withdrawal: float) -> tuple[float, float, float]:
        allocation = None
        if account_balances is not None:
            from model.accounts import allocate_withdrawal

            allocation = allocate_withdrawal(
                account_balances,
                withdrawal,
                order=withdrawal_order,
                rmd_floor=rmd_floor,
                qcd=qcd_amount,
            )
        detail = compute_retirement_tax_detail(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental_gross,
            earned_income=earned_income,
            withdrawal_gross=withdrawal,
            roth_fraction=roth_fraction,
            filing_status=filing_status,
            inflation_factor=inflation_factor,
            ss_tax_method=ss_tax_method,
            ss_taxable_fraction=ss_taxable_fraction,
            tax_mode=tax_mode,
            flat_rate=flat_rate,
            table=table,
            state_code=state_code,
            state_tax_enabled=state_tax_enabled,
            state_tax_override_pct=state_tax_override_pct,
            taxable_portfolio_fraction=taxable_portfolio_fraction,
            taxable_gain_fraction=taxable_gain_fraction,
            seniors_65_count=seniors_65_count,
            mfs_lived_apart=mfs_lived_apart,
            niit_enabled=niit_enabled,
            withdrawal_allocation=allocation,
            qcd_exclusion=qcd_amount,
            roth_conversion=roth_conversion,
            planning_precision=planning_precision,
            itemized=itemized,
        )
        return detail.federal, detail.state, detail.ss_taxable

    def spendable(withdrawal: float) -> float:
        federal, state, _ = taxes_for(withdrawal)
        return spendable_cash(
            ss_gross=ss_gross,
            pension_gross=pension_gross,
            rental_gross=rental_gross,
            withdrawal_gross=withdrawal,
            tax=federal + state,
        )

    if spendable(rmd_floor) >= expenses:
        federal, state, ss_taxable = taxes_for(rmd_floor)
        return rmd_floor, federal, state, ss_taxable, federal + state

    lo = rmd_floor
    hi = max(expenses + gross_benefits, rmd_floor) * 2.5
    for _ in range(40):
        mid = (lo + hi) / 2
        if spendable(mid) >= expenses:
            hi = mid
        else:
            lo = mid

    withdrawal = hi
    federal, state, ss_taxable = taxes_for(withdrawal)
    return withdrawal, federal, state, ss_taxable, federal + state
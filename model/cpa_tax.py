"""CPA-precision federal tax: itemized vs standard deduction and AMT."""

from __future__ import annotations

from dataclasses import dataclass

from config.policy.tables import (
    AMT_EXEMPTION_PHASEOUT_RATE,
    AMT_RATE_HIGH,
    AMT_RATE_LOW,
    MEDICAL_AGI_FLOOR_PCT,
    SALT_DEDUCTION_CAP_2024,
    amt_constants_for_projection,
)
from model.taxes import (
    TaxTable,
    _taxable_after_deduction,
    additional_standard_deduction_65,
    federal_income_tax,
    federal_ltcg_tax,
    normalize_filing_status,
)

VALID_PLANNING_PRECISION = frozenset({"standard", "cpa"})


@dataclass(frozen=True)
class ItemizedInputs:
    salt: float = 0.0
    mortgage_interest: float = 0.0
    charitable: float = 0.0
    medical: float = 0.0
    mortgage_interest_second: float = 0.0


@dataclass(frozen=True)
class CpaFederalTaxResult:
    total_federal: float
    ordinary_tax: float
    ltcg_tax: float
    amt_surcharge: float
    deduction_used: float
    deduction_method: str


def has_itemized_amounts(itemized: ItemizedInputs) -> bool:
    return any(
        value > 0
        for value in (
            itemized.salt,
            itemized.mortgage_interest,
            itemized.mortgage_interest_second,
            itemized.charitable,
            itemized.medical,
        )
    )


def normalize_planning_precision(value: object) -> str:
    mode = str(value or "standard").strip().lower()
    return mode if mode in VALID_PLANNING_PRECISION else "standard"


def itemized_inputs_from_params(params: dict) -> ItemizedInputs:
    return ItemizedInputs(
        salt=float(params.get("itemized_salt_annual", 0) or 0),
        mortgage_interest=float(params.get("itemized_mortgage_interest", 0) or 0),
        charitable=float(params.get("itemized_charitable", 0) or 0),
        medical=float(params.get("itemized_medical", 0) or 0),
        mortgage_interest_second=float(params.get("itemized_mortgage_interest_second", 0) or 0),
    )


def salt_deduction_cap(
    *,
    tax_year: int,
    filing_status: str,
    magi: float,
) -> float:
    """SALT cap for a calendar year.

    TCJA is $10,000 ($5,000 married filing separately). OBBBA raises that to
    $40,000 ($20,000 MFS) for 2025, plus 1% a year through 2029, then back to
    $10,000 in 2030. Above the MAGI threshold the cap falls 30 cents per dollar,
    but not below the $10,000 / $5,000 floor.
    """
    status = normalize_filing_status(filing_status)
    mfs = status == "mfs"
    floor = 5_000.0 if mfs else SALT_DEDUCTION_CAP_2024
    if tax_year < 2025 or tax_year >= 2030:
        return floor
    growth = 1.01 ** (tax_year - 2025)
    cap = (20_000.0 if mfs else 40_000.0) * growth
    threshold = (250_000.0 if mfs else 500_000.0) * growth
    if magi > threshold:
        cap = max(floor, cap - 0.30 * (magi - threshold))
    return cap


def compute_itemized_deduction(
    inputs: ItemizedInputs,
    *,
    agi: float,
    filing_status: str = "mfj",
    tax_year: int = 2024,
) -> tuple[float, float]:
    """Return (total itemized deduction, SALT amount included after cap)."""
    cap = salt_deduction_cap(tax_year=tax_year, filing_status=filing_status, magi=agi)
    salt = min(max(0.0, inputs.salt), cap)
    mortgage = max(0.0, inputs.mortgage_interest) + max(0.0, inputs.mortgage_interest_second)
    charitable = max(0.0, inputs.charitable)
    medical = max(0.0, inputs.medical - agi * MEDICAL_AGI_FLOOR_PCT)
    return salt + mortgage + charitable + medical, salt


def _loan_payoff_limit(person: dict, annual_key: str, year_key: str):
    """Return a payoff year, ``"open"`` when the loan has no end, or None if absent."""
    payment = float(person.get(annual_key, 0) or 0)
    raw = person.get(year_key)
    if payment <= 0 and raw in (None, ""):
        return None
    if raw in (None, ""):
        return "open" if payment > 0 else None
    try:
        payoff = int(float(str(raw).replace(",", "").replace("$", "")))
    except (TypeError, ValueError):
        return "open" if payment > 0 else None
    if 1900 <= payoff <= 2100:
        return payoff
    return "open" if payment > 0 else None


def _interest_remaining(amount: float, limits: list, year: int) -> float:
    """Keep interest through the latest known payoff. A blank payoff leaves it on."""
    if amount <= 0:
        return 0.0
    if not limits or any(limit == "open" for limit in limits):
        return amount
    years = [limit for limit in limits if isinstance(limit, int)]
    if not years or year <= max(years):
        return amount
    return 0.0


def itemized_for_year(
    itemized: ItemizedInputs,
    persons: dict,
    year: int,
) -> ItemizedInputs:
    """Stop each loan's interest after that loan's payoff year.

    A single combined interest amount, with no second-loan figure, still lasts
    until the later payoff. A payment with a blank payoff year does not turn
    its interest off. Interest parked on the second field when there is no
    second loan ends with the first loan.
    """
    from dataclasses import replace

    if itemized.mortgage_interest <= 0 and itemized.mortgage_interest_second <= 0:
        return itemized
    first_limits = []
    second_limits = []
    for person in persons.values():
        first = _loan_payoff_limit(person, "mortgage_annual", "mortgage_payoff_year")
        second = _loan_payoff_limit(
            person, "mortgage_second_annual", "mortgage_second_payoff_year"
        )
        if first is not None:
            first_limits.append(first)
        if second is not None:
            second_limits.append(second)
    if itemized.mortgage_interest_second > 0:
        first_amount = _interest_remaining(itemized.mortgage_interest, first_limits, year)
        # No second loan means there is no payoff of its own. Use the first.
        second_scope = second_limits or first_limits
        second_amount = _interest_remaining(
            itemized.mortgage_interest_second, second_scope, year
        )
    else:
        first_amount = _interest_remaining(
            itemized.mortgage_interest, first_limits + second_limits, year
        )
        second_amount = 0.0
    if (
        first_amount == itemized.mortgage_interest
        and second_amount == itemized.mortgage_interest_second
    ):
        return itemized
    return replace(
        itemized,
        mortgage_interest=first_amount,
        mortgage_interest_second=second_amount,
    )


def _amt_exemption_and_phaseout(
    filing_status: str,
    *,
    inflation_factor: float,
) -> tuple[float, float, float]:
    status = normalize_filing_status(filing_status)
    amt = amt_constants_for_projection(inflation_factor=inflation_factor)
    if status == "mfj":
        return (
            amt["exemption_mfj"],
            amt["phaseout_start_mfj"],
            amt["bracket_26_mfj"],
        )
    if status == "mfs":
        return (
            amt["exemption_mfs"],
            amt["phaseout_start_mfs"],
            amt["bracket_26_mfs"],
        )
    return (
        amt["exemption_single"],
        amt["phaseout_start_single"],
        amt["bracket_26_single"],
    )


def compute_amti(
    *,
    ordinary_income: float,
    ltcg: float,
    itemized_deduction: float = 0.0,
    salt_deducted: float = 0.0,
    deduction_method: str,
) -> float:
    """AMTI before the AMT exemption.

    The standard deduction is not subtracted. Itemized deductions other than
    SALT (mortgage interest, charity, deductible medical) do reduce AMTI.
    """
    income = max(0.0, ordinary_income) + max(0.0, ltcg)
    if deduction_method != "itemized":
        return income
    non_salt = max(0.0, itemized_deduction - max(0.0, salt_deducted))
    return max(0.0, income - non_salt)


def compute_amt_liability(
    amti: float,
    *,
    filing_status: str,
    inflation_factor: float = 1.0,
) -> float:
    exemption, phaseout_start, bracket_26 = _amt_exemption_and_phaseout(
        filing_status,
        inflation_factor=inflation_factor,
    )
    if amti > phaseout_start:
        exemption = max(0.0, exemption - AMT_EXEMPTION_PHASEOUT_RATE * (amti - phaseout_start))
    amt_taxable = max(0.0, amti - exemption)
    if amt_taxable <= 0:
        return 0.0
    low_band = min(amt_taxable, bracket_26)
    high_band = max(0.0, amt_taxable - bracket_26)
    return low_band * AMT_RATE_LOW + high_band * AMT_RATE_HIGH


def federal_tax_cpa_precision(
    *,
    ordinary_income: float,
    ltcg: float,
    table: TaxTable,
    filing_status: str,
    seniors_65_count: int = 0,
    inflation_factor: float = 1.0,
    itemized: ItemizedInputs,
    agi: float,
    tax_year: int = 2024,
) -> CpaFederalTaxResult:
    """Federal tax with optimal deduction and AMT (max of regular vs AMT)."""
    extra_sd = additional_standard_deduction_65(
        filing_status,
        seniors_65_count=seniors_65_count,
        inflation_factor=inflation_factor,
    )
    standard_deduction = table.standard_deduction + extra_sd
    itemized_deduction, salt_used = compute_itemized_deduction(
        itemized,
        agi=agi,
        filing_status=filing_status,
        tax_year=tax_year,
    )

    if itemized_deduction > standard_deduction:
        deduction = itemized_deduction
        method = "itemized"
    else:
        deduction = standard_deduction
        method = "standard"
        salt_used = 0.0

    ordinary_taxable, ltcg_taxable = _taxable_after_deduction(
        ordinary_income,
        ltcg,
        deduction,
    )
    ord_tax = federal_income_tax(ordinary_taxable, table) if ordinary_taxable > 0 else 0.0
    if ltcg_taxable <= 0:
        ltcg_tax = 0.0
    else:
        ltcg_tax = federal_ltcg_tax(
            ltcg_taxable,
            ordinary_taxable_income=ordinary_taxable,
            ltcg_tiers=table.ltcg_tiers,
        )

    regular_federal = ord_tax + ltcg_tax
    amti = compute_amti(
        ordinary_income=ordinary_income,
        ltcg=ltcg,
        itemized_deduction=itemized_deduction,
        salt_deducted=salt_used,
        deduction_method=method,
    )
    amt_liability = compute_amt_liability(
        amti,
        filing_status=filing_status,
        inflation_factor=inflation_factor,
    )
    total_federal = max(regular_federal, amt_liability)
    amt_surcharge = max(0.0, total_federal - regular_federal)

    return CpaFederalTaxResult(
        total_federal=total_federal,
        ordinary_tax=ord_tax,
        ltcg_tax=ltcg_tax,
        amt_surcharge=amt_surcharge,
        deduction_used=deduction,
        deduction_method=method,
    )
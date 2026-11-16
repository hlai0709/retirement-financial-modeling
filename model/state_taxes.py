"""State income tax profiles for relocation and residence planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from model.taxes import TaxTable, _inflate_brackets, federal_income_tax, normalize_filing_status

StateTaxKind = Literal["none", "flat", "brackets"]
StateSsRule = Literal["federal_taxable", "exempt"]

# California 2024 taxable-income brackets (FTB; inflated at runtime)
CA_SINGLE_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (10_412, 0.01),
    (24_710, 0.02),
    (38_937, 0.04),
    (54_081, 0.06),
    (68_350, 0.08),
    (349_417, 0.093),
    (419_300, 0.103),
    (698_834, 0.113),
    (1_000_000, 0.123),
    (float("inf"), 0.133),
)

CA_MFJ_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (20_824, 0.01),
    (49_420, 0.02),
    (77_874, 0.04),
    (108_162, 0.06),
    (136_700, 0.08),
    (698_834, 0.093),
    (838_600, 0.103),
    (1_000_000, 0.113),
    (1_390_776, 0.123),
    (float("inf"), 0.133),
)

# Simplified Oregon 2024 brackets (single / MFJ)
OR_SINGLE_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (4_300, 0.0475),
    (10_750, 0.0675),
    (125_000, 0.0875),
    (float("inf"), 0.099),
)

OR_MFJ_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (8_600, 0.0475),
    (21_500, 0.0675),
    (250_000, 0.0875),
    (float("inf"), 0.099),
)

# Simplified New York 2024 brackets (single / MFJ)
NY_SINGLE_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (8_500, 0.04),
    (11_700, 0.045),
    (13_900, 0.0525),
    (80_650, 0.055),
    (215_400, 0.06),
    (1_077_550, 0.0685),
    (float("inf"), 0.0965),
)

NY_MFJ_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (17_150, 0.04),
    (23_600, 0.045),
    (27_900, 0.0525),
    (161_550, 0.055),
    (323_200, 0.06),
    (2_155_350, 0.0685),
    (float("inf"), 0.0965),
)

# Simplified New Jersey 2024 brackets (planning approximation)
NJ_SINGLE_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (20_000, 0.014),
    (35_000, 0.0175),
    (40_000, 0.035),
    (75_000, 0.05525),
    (500_000, 0.0637),
    (1_000_000, 0.0897),
    (float("inf"), 0.1075),
)

NJ_MFJ_BRACKETS_2024: tuple[tuple[float, float], ...] = (
    (20_000, 0.014),
    (50_000, 0.0175),
    (70_000, 0.0245),
    (80_000, 0.035),
    (150_000, 0.05525),
    (500_000, 0.0637),
    (1_000_000, 0.0897),
    (float("inf"), 0.1075),
)


@dataclass(frozen=True)
class StateTaxProfile:
    code: str
    name: str
    kind: StateTaxKind
    flat_rate: float = 0.0
    ss_rule: StateSsRule = "federal_taxable"
    standard_deduction_single_2024: float = 0.0
    standard_deduction_mfj_2024: float = 0.0
    standard_deduction_hoh_2024: float = 0.0
    single_brackets_2024: tuple[tuple[float, float], ...] = ()
    mfj_brackets_2024: tuple[tuple[float, float], ...] = ()


STATE_ALIASES: dict[str, str] = {
    "california": "CA",
    "ca": "CA",
    "texas": "TX",
    "tx": "TX",
    "florida": "FL",
    "fl": "FL",
    "nevada": "NV",
    "nv": "NV",
    "washington": "WA",
    "wa": "WA",
    "arizona": "AZ",
    "az": "AZ",
    "oregon": "OR",
    "or": "OR",
    "new_york": "NY",
    "newyork": "NY",
    "ny": "NY",
    "massachusetts": "MA",
    "ma": "MA",
    "new_jersey": "NJ",
    "newjersey": "NJ",
    "nj": "NJ",
    "colorado": "CO",
    "co": "CO",
    "illinois": "IL",
    "il": "IL",
    "pennsylvania": "PA",
    "pa": "PA",
    "north_carolina": "NC",
    "nc": "NC",
    "tennessee": "TN",
    "tn": "TN",
    "wyoming": "WY",
    "wy": "WY",
    "south_dakota": "SD",
    "sd": "SD",
    "alaska": "AK",
    "ak": "AK",
    "none": "NONE",
    "no_tax": "NONE",
}


def _none(code: str, name: str) -> StateTaxProfile:
    return StateTaxProfile(code, name, "none", ss_rule="exempt")


def _flat(
    code: str,
    name: str,
    rate: float,
    *,
    ss_rule: StateSsRule = "federal_taxable",
    deduction_single: float = 0.0,
    deduction_mfj: float = 0.0,
) -> StateTaxProfile:
    return StateTaxProfile(
        code,
        name,
        "flat",
        flat_rate=rate,
        ss_rule=ss_rule,
        standard_deduction_single_2024=deduction_single,
        standard_deduction_mfj_2024=deduction_mfj,
        standard_deduction_hoh_2024=deduction_single,
    )


def _brackets(
    code: str,
    name: str,
    *,
    ss_rule: StateSsRule = "federal_taxable",
    deduction_single: float,
    deduction_mfj: float,
    single_brackets: tuple[tuple[float, float], ...],
    mfj_brackets: tuple[tuple[float, float], ...],
) -> StateTaxProfile:
    return StateTaxProfile(
        code,
        name,
        "brackets",
        ss_rule=ss_rule,
        standard_deduction_single_2024=deduction_single,
        standard_deduction_mfj_2024=deduction_mfj,
        standard_deduction_hoh_2024=deduction_single,
        single_brackets_2024=single_brackets,
        mfj_brackets_2024=mfj_brackets,
    )


STATE_PROFILES: dict[str, StateTaxProfile] = {
    "CA": _brackets(
        "CA",
        "California",
        deduction_single=5_363,
        deduction_mfj=10_726,
        single_brackets=CA_SINGLE_BRACKETS_2024,
        mfj_brackets=CA_MFJ_BRACKETS_2024,
    ),
    "NONE": _none("NONE", "No State Income Tax"),
    "TX": _none("TX", "Texas"),
    "FL": _none("FL", "Florida"),
    "NV": _none("NV", "Nevada"),
    "WA": _none("WA", "Washington"),
    "WY": _none("WY", "Wyoming"),
    "SD": _none("SD", "South Dakota"),
    "AK": _none("AK", "Alaska"),
    "TN": _none("TN", "Tennessee"),
    "AZ": _flat("AZ", "Arizona", 0.025, deduction_single=14_600, deduction_mfj=29_200),
    "CO": _flat("CO", "Colorado", 0.044, deduction_single=14_600, deduction_mfj=29_200),
    "IL": _flat("IL", "Illinois", 0.0495, deduction_single=2_775, deduction_mfj=5_550),
    "PA": _flat("PA", "Pennsylvania", 0.0307),
    "NC": _flat("NC", "North Carolina", 0.045, deduction_single=12_750, deduction_mfj=25_500),
    "OR": _brackets(
        "OR",
        "Oregon",
        deduction_single=2_745,
        deduction_mfj=5_490,
        single_brackets=OR_SINGLE_BRACKETS_2024,
        mfj_brackets=OR_MFJ_BRACKETS_2024,
    ),
    "NY": _brackets(
        "NY",
        "New York",
        deduction_single=8_000,
        deduction_mfj=16_050,
        single_brackets=NY_SINGLE_BRACKETS_2024,
        mfj_brackets=NY_MFJ_BRACKETS_2024,
    ),
    "MA": _flat(
        "MA",
        "Massachusetts",
        0.05,
        deduction_single=4_400,
        deduction_mfj=8_800,
    ),
    "NJ": _brackets(
        "NJ",
        "New Jersey",
        deduction_single=0,
        deduction_mfj=0,
        single_brackets=NJ_SINGLE_BRACKETS_2024,
        mfj_brackets=NJ_MFJ_BRACKETS_2024,
    ),
}

SUPPORTED_STATE_CODES: tuple[str, ...] = tuple(sorted(STATE_PROFILES))


def canonical_state_code(raw: str) -> str:
    """Normalize user input to a state code without substituting an unknown state."""
    text = (raw or "CA").strip().lower().replace(" ", "_")
    if not text:
        return "CA"
    if text in STATE_ALIASES:
        return STATE_ALIASES[text]
    upper = text.upper()
    if upper in STATE_PROFILES:
        return upper
    if len(upper) == 2 and upper.isalpha():
        return upper
    return upper


def normalize_state_code(raw: str) -> str:
    """Alias for :func:`canonical_state_code` (preserves unknown codes like VT)."""
    return canonical_state_code(raw)


def is_supported_state(code: str) -> bool:
    """Return True when ``code`` maps to a modeled state profile."""
    canonical = canonical_state_code(code)
    return canonical in STATE_PROFILES


def state_uses_fallback(code: str) -> bool:
    """True when ``code`` is not modeled and estimates will use California."""
    canonical = canonical_state_code(code)
    return canonical not in STATE_PROFILES and canonical != "NONE"


def state_fallback_message(code: str) -> str | None:
    canonical = canonical_state_code(code)
    if not state_uses_fallback(canonical):
        return None
    return (
        f"State {canonical!r} is not in the catalog; "
        "using California brackets for planning estimates. "
        "Set State Tax Override % or switch to CPA precision with a modeled state."
    )


def state_profile(code: str, *, strict: bool = False) -> StateTaxProfile | None:
    canonical = canonical_state_code(code)
    if canonical in STATE_PROFILES:
        return STATE_PROFILES[canonical]
    if strict:
        return None
    return STATE_PROFILES["CA"]


def state_standard_deduction(
    profile: StateTaxProfile,
    filing_status: str,
    *,
    inflation_factor: float,
) -> float:
    status = normalize_filing_status(filing_status)
    if status == "mfj":
        return profile.standard_deduction_mfj_2024 * inflation_factor
    if status == "head_of_household":
        return profile.standard_deduction_hoh_2024 * inflation_factor
    return profile.standard_deduction_single_2024 * inflation_factor


def state_tax_table(
    profile: StateTaxProfile,
    filing_status: str,
    *,
    inflation_factor: float,
) -> TaxTable | None:
    if profile.kind != "brackets":
        return None
    status = normalize_filing_status(filing_status)
    brackets = (
        profile.mfj_brackets_2024
        if status == "mfj"
        else profile.single_brackets_2024
    )
    return TaxTable(
        standard_deduction=state_standard_deduction(
            profile, filing_status, inflation_factor=inflation_factor
        ),
        brackets=_inflate_brackets(brackets, inflation_factor),
    )


def state_taxable_gross(
    gross_taxable: float,
    *,
    ss_taxable: float,
    profile: StateTaxProfile,
) -> float:
    if profile.ss_rule == "exempt":
        return max(0.0, gross_taxable - ss_taxable)
    return gross_taxable


def compute_state_income_tax(
    gross_taxable: float,
    *,
    ss_taxable: float,
    profile: StateTaxProfile,
    filing_status: str,
    inflation_factor: float,
    override_flat_rate: float | None = None,
) -> float:
    if override_flat_rate is not None and override_flat_rate >= 0:
        taxable = state_taxable_gross(
            gross_taxable, ss_taxable=ss_taxable, profile=profile
        )
        return max(0.0, taxable * override_flat_rate)

    if profile.kind == "none":
        return 0.0

    taxable = state_taxable_gross(
        gross_taxable, ss_taxable=ss_taxable, profile=profile
    )
    deduction = state_standard_deduction(
        profile, filing_status, inflation_factor=inflation_factor
    )
    taxable_after_deduction = max(0.0, taxable - deduction)

    if profile.kind == "flat":
        return taxable_after_deduction * profile.flat_rate

    table = state_tax_table(profile, filing_status, inflation_factor=inflation_factor)
    assert table is not None
    return federal_income_tax(taxable_after_deduction, table)


def supported_states_label() -> str:
    return ", ".join(SUPPORTED_STATE_CODES)
"""Best-effort extractors for SSA, 1099, brokerage, and 401(k) PDF text."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from app.core.presentation.field_editor import resolve_field
from config.field_values import format_field_value
from providers.document_extractors.pdf_text import extract_pdf_text

_AMOUNT = r"(\$?\s*-?\d[\d,]*\.?\d*)"


def _parse_amount(raw: str) -> float | None:
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned in {"-", "—"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _label_amount(text: str, *labels: str) -> float | None:
    for label in labels:
        pattern = rf"{re.escape(label)}[^\n]{{0,80}}?{_AMOUNT}"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = _parse_amount(match.group(1))
            if amount is not None:
                return amount
    return None


def _money_suggestion(
    field_key: str,
    value: float,
    *,
    person: str | None,
    quote: str,
    confidence: str = "medium",
) -> ExtractedFieldSuggestion | None:
    field = resolve_field(field_key, person=person)
    if field is None:
        return None
    field_person = person if field.scope == "person" else None
    raw = format_field_value(field, value)
    return ExtractedFieldSuggestion(
        field_key=field_key,
        person=field_person,
        label=field.label,
        raw_value=raw,
        display_value=f"${value:,.0f}",
        confidence=confidence,
        source_quote=quote,
        document_type=DocumentType.PDF.value,
    )


_SSA_AGE_BENEFIT = re.compile(
    r"(?:^|\n)\s*(?P<age>6[2-9]|70)\s+\$?\s*(?P<amt>\d{1,3}(?:,\d{3})+|\d{3,5})\b",
    re.MULTILINE,
)
# Prefer explicit "full retirement age is 67" over redacted "Your f t age is 67"
_SSA_FRA_EXPLICIT = re.compile(
    r"full\s+retirement\s+age\s+is\s+(?P<age>6[5-9]|70)\b",
    re.IGNORECASE,
)
_SSA_FRA_REDACTED = re.compile(
    r"(?:f\s*t\s+age|retirement\s+age)\s+is\s+(?P<age>6[5-9]|70)\b"
    r"|age\s+is\s+(?P<age2>6[5-9]|70)\s*,?\s*based\s+on\s+your\s+date",
    re.IGNORECASE,
)
_SSA_CHART_START = re.compile(
    r"Personalized\s+Monthly\s+Retirement\s+Benefit\s+Estimates"
    r"|Monthly\s+Benefit\s+Amount",
    re.IGNORECASE,
)
_SSA_CHART_END = re.compile(
    r"\n\s*Medicare\b|\n\s*Earnings\s+Record\b|\n\s*Disability\s+Benefits\b",
    re.IGNORECASE,
)


def _ssa_retirement_chart_slice(text: str) -> str:
    """Limit chart parsing to the age/benefit table (not survivors / earnings years)."""
    start = _SSA_CHART_START.search(text)
    if not start:
        return text
    body = text[start.start() :]
    end = _SSA_CHART_END.search(body)
    if end:
        body = body[: end.start()]
    return body


def _ssa_monthly_by_age(text: str) -> dict[int, float]:
    """Parse my Social Security 'Monthly Benefit Amount' chart (ages 62–70)."""
    chart = _ssa_retirement_chart_slice(text)
    by_age: dict[int, float] = {}
    for match in _SSA_AGE_BENEFIT.finditer(chart):
        age = int(match.group("age"))
        amount = _parse_amount(match.group("amt"))
        if amount is None:
            continue
        # Chart amounts are monthly (typically ~$1k–$6k on modern statements)
        if 500 <= amount <= 15_000:
            by_age[age] = amount
    # Fallback: whole document if section headers were redacted/missing
    if len(by_age) < 3:
        for match in _SSA_AGE_BENEFIT.finditer(text):
            age = int(match.group("age"))
            amount = _parse_amount(match.group("amt"))
            if amount is not None and 500 <= amount <= 15_000:
                by_age.setdefault(age, amount)
    return by_age


def _ssa_full_retirement_age(text: str) -> int | None:
    for pattern in (_SSA_FRA_EXPLICIT, _SSA_FRA_REDACTED):
        match = pattern.search(text)
        if not match:
            continue
        raw = match.groupdict().get("age") or match.groupdict().get("age2")
        try:
            age = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if 65 <= age <= 70:
            return age
    return None


def extract_ssa_suggestions(text: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
    """SSA-1099 boxes and/or my Social Security statement retirement chart."""
    out: list[ExtractedFieldSuggestion] = []
    keys_seen: set[str] = set()

    def _add(suggestion: ExtractedFieldSuggestion | None) -> None:
        if suggestion is None or not suggestion.field_key:
            return
        if suggestion.field_key in keys_seen:
            return
        keys_seen.add(suggestion.field_key)
        out.append(suggestion)

    # --- my Social Security statement: age → monthly benefit table ---
    # PIA = monthly benefit at full retirement age (FRA). Engine applies early/late
    # claiming factors when ss_start_age differs from FRA.
    by_age = _ssa_monthly_by_age(text)
    fra = _ssa_full_retirement_age(text)
    if by_age:
        # Prefer FRA row (usually 66–67); else age 67; else highest age on chart
        pick_age = fra if fra in by_age else (67 if 67 in by_age else max(by_age))
        monthly = by_age[pick_age]
        annual = round(monthly * 12.0, 2)
        chart_note = ""
        if 62 in by_age and 70 in by_age:
            chart_note = f" (chart also lists 62=${by_age[62]:,.0f}/mo … 70=${by_age[70]:,.0f}/mo)"
        _add(
            _money_suggestion(
                "ss_pia_monthly",
                monthly,
                person=person,
                quote=(
                    f"Statement monthly benefit at FRA age {pick_age}: "
                    f"${monthly:,.0f}{chart_note}"
                ),
                confidence="high",
            )
        )
        _add(
            _money_suggestion(
                "ss_amount",
                annual,
                person=person,
                quote=(
                    f"Age {pick_age} monthly ${monthly:,.0f} × 12 = ${annual:,.0f}/yr "
                    f"(annual at claim age {pick_age}; change SS start age to claim earlier/later)"
                ),
                confidence="high",
            )
        )
        # Claim age = FRA when stated, else the age we used for PIA
        start_age = fra if fra is not None else pick_age
        age_field = resolve_field("ss_start_age", person=person)
        if age_field is not None:
            field_person = person if age_field.scope == "person" else None
            raw = format_field_value(age_field, start_age)
            _add(
                ExtractedFieldSuggestion(
                    field_key="ss_start_age",
                    person=field_person,
                    label=age_field.label,
                    raw_value=raw,
                    display_value=str(start_age),
                    confidence="high" if fra is not None else "medium",
                    source_quote=(
                        f"Full retirement age {fra}" if fra is not None else f"Benefit chart age {pick_age}"
                    ),
                    document_type=DocumentType.PDF.value,
                )
            )
        # Engine uses PIA path when mode is "pia" (ss_amount alone is only for "manual")
        mode_field = resolve_field("ss_benefit_mode", person=person)
        if mode_field is not None:
            field_person = person if mode_field.scope == "person" else None
            _add(
                ExtractedFieldSuggestion(
                    field_key="ss_benefit_mode",
                    person=field_person,
                    label=mode_field.label,
                    raw_value="pia",
                    display_value="pia",
                    confidence="high",
                    source_quote="Social Security statement monthly benefit chart → PIA mode",
                    document_type=DocumentType.PDF.value,
                )
            )

    # --- SSA-1099 box 5 net benefits / annual ---
    annual = _label_amount(
        text,
        "Benefits Paid",
        "Net Benefits",
        "Box 5",
        "total benefits",
        "Social Security benefits",
    )
    if annual and annual > 0 and "ss_amount" not in keys_seen:
        monthly = round(annual / 12.0, 2)
        _add(
            _money_suggestion(
                "ss_amount",
                annual,
                person=person,
                quote="SSA benefits (annual)",
                confidence="high",
            )
        )
        _add(
            _money_suggestion(
                "ss_pia_monthly",
                monthly,
                person=person,
                quote="SSA benefits ÷ 12 (estimate)",
                confidence="medium",
            )
        )

    pia = _label_amount(text, "Primary Insurance Amount", "PIA", "Your full retirement age benefit")
    if pia and pia > 0 and "ss_pia_monthly" not in keys_seen:
        # often monthly on SSA statements
        if pia < 20_000:
            _add(
                _money_suggestion(
                    "ss_pia_monthly",
                    pia,
                    person=person,
                    quote="PIA monthly",
                    confidence="high",
                )
            )
            _add(
                _money_suggestion(
                    "ss_amount",
                    pia * 12,
                    person=person,
                    quote="PIA × 12",
                    confidence="medium",
                )
            )
    return out


def extract_brokerage_suggestions(text: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
    out: list[ExtractedFieldSuggestion] = []
    total = _label_amount(
        text,
        "Total Account Value",
        "Account value",
        "Ending balance",
        "Total equity",
        "Net account value",
        "Portfolio value",
        "Market value",
    )
    if total and total > 0:
        s = _money_suggestion(
            "initial_taxable_assets",
            total,
            person=person,
            quote="Brokerage total",
            confidence="medium",
        )
        if s:
            out.append(s)
        s2 = _money_suggestion(
            "initial_assets",
            total,
            person=person,
            quote="Brokerage total → assets",
            confidence="low",
        )
        if s2:
            out.append(s2)
    return out


def extract_401k_suggestions(text: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
    out: list[ExtractedFieldSuggestion] = []
    trad = _label_amount(
        text,
        "Traditional",
        "Pre-tax",
        "Pretax",
        "401(k) balance",
        "Vested balance",
        "Total balance",
    )
    roth = _label_amount(text, "Roth", "Roth 401", "Roth balance", "Roth contribution")
    # Prefer larger trad if both look like totals
    if trad and trad > 0:
        s = _money_suggestion(
            "initial_traditional_ira",
            trad,
            person=person,
            quote="Retirement plan traditional/pre-tax",
            confidence="medium",
        )
        if s:
            out.append(s)
    if roth and roth > 0 and (not trad or abs(roth - trad) > 1):
        s = _money_suggestion(
            "initial_roth_ira",
            roth,
            person=person,
            quote="Retirement plan Roth",
            confidence="medium",
        )
        if s:
            out.append(s)
    if trad and trad > 0 and not out:
        s = _money_suggestion(
            "initial_assets",
            trad,
            person=person,
            quote="Plan total → assets",
            confidence="low",
        )
        if s:
            out.append(s)
    return out


def extract_1099_r_suggestions(text: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
    out: list[ExtractedFieldSuggestion] = []
    gross = _label_amount(text, "Gross distribution", "Box 1", "Total distribution")
    if gross and gross > 0:
        s = _money_suggestion(
            "pension_amount",
            gross,
            person=person,
            quote="1099-R gross distribution",
            confidence="medium",
        )
        if s:
            out.append(s)
    return out


# Prefer full money amounts (with cents) — lender PDFs often glue labels together.
_MONEY_AMT = r"(\d{1,3}(?:,\d{3})+\.\d{2}|\d{4,}\.\d{2}|\d{1,3}\.\d{2})"
# Maturity / payoff as a calendar year only — never capture dollar amounts.
_PAYOFF_YEAR = re.compile(
    r"(?:matur(?:ity|es)\s*(?:date|year)?|final\s+(?:payment|due)|"
    r"loan\s+(?:matures|ends)|paid\s+off\s+(?:in|by|year))"
    r"[^\n\d]{0,40}?(20\d{2})\b",
    re.IGNORECASE,
)

# Monthly / total payment — spaced or glued (Freedom, Mr. Cooper, etc.)
_MONTHLY_PAYMENT_PATTERNS = (
    re.compile(
        r"current\s*total\s*py?mt[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"current\s*p\s*&\s*i\s*payment[^\d$]{0,48}\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"currentp&ipayment(?:currentescrowpayment)?\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"regular\s*monthly\s*payment[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"amount\s*due\*?\*?[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:monthly\s+(?:payment|p&i|principal\s*(?:and|&)\s*interest)|"
        r"payment\s+amount|regular\s+payment|total\s+payment\s+due|"
        r"current\s+payment)"
        r"[^\n\d$]{0,40}\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
)

_INTEREST_PATTERNS = (
    re.compile(
        r"(?:gross\s*interest\s*paid\*?)?(?:mortgage\s*interest\s*received"
        r"(?:from\s*payer\(s\)/borrower\(s\))?)\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"mortgageinterestreceivedfrompayer\(s\)/borrower\(s\)\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:interest\s+paid|ytd\s+interest|year[\s-]*to[\s-]*date\s+interest|"
        r"mortgage\s+interest\s+received(?:\s+from\s+payer)?)"
        r"[^\n\d$]{0,40}\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"1\s*mortgage\s+interest\s+received[^\d$]{0,80}\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
)

_PRINCIPAL_PATTERNS = (
    re.compile(
        r"outstanding\s*principal[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"interest\s*bearing\s*principal\s*balance[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:ending\s*bal(?:ance)?|endingbalance)[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"beginningbalanceappliedprincipalendingbalance"
        r"\$?\s*" + _MONEY_AMT + r"\$?\s*" + _MONEY_AMT + r"\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:outstanding\s+mortgage\s+principal|beg(?:inning)?\s*bal(?:ance)?)"
        r"[:\s]*\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
)

_RATE_PATTERNS = (
    re.compile(
        r"(?:interest\s*rate|note\s*rate|current\s*rate)\s*[:\s]*(\d{1,2}\.\d{2,4})\s*%",
        re.IGNORECASE,
    ),
    re.compile(
        r"interestrate\s*(\d{1,2}\.\d{2,4})\s*%",
        re.IGNORECASE,
    ),
)

_AS_OF_DATE = re.compile(
    r"(?:payment\s*due\s*date|due\s*date|statement\s*date|as\s*of)[^\d]{0,24}"
    r"(0?[1-9]|1[0-2])[\/\-.](0?[1-9]|[12]\d|3[01])[\/\-.]"
    r"(20\d{2}|\d{2})\b",
    re.IGNORECASE,
)
_AS_OF_YEAR = re.compile(
    r"\b(?:YEAR|tax\s*year|calendar\s*year)\s*:?\s*(20\d{2})\b",
    re.IGNORECASE,
)


def _first_amount(text: str, patterns: tuple[re.Pattern[str], ...]) -> float | None:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        # Prefer the last capture group when a pattern returns several amounts
        # (e.g. beginning / applied / ending balance triple).
        for group in reversed(match.groups()):
            if group is None:
                continue
            amount = _parse_amount(group)
            if amount is not None:
                return amount
    return None


def _mortgage_monthly_payment(text: str) -> float | None:
    """Total amount due (P&I + escrow) — used for annual cash-flow expense."""
    monthly = _first_amount(text, _MONTHLY_PAYMENT_PATTERNS)
    if monthly is None:
        monthly = _label_amount(
            text,
            "Regular Monthly Payment",
            "Regular monthly payment",
            "Monthly payment",
            "Payment amount",
            "Amount Due",
            "Total payment due",
            "CURRENT TOTAL PYMT",
            "Current P&I Payment",
        )
    if monthly is not None and 100 <= monthly <= 50_000:
        return monthly
    return None


def _mortgage_monthly_pi(text: str, *, total_payment: float | None = None) -> float | None:
    """Principal & interest only. Never fall back to a total that includes escrow."""
    _ = total_payment
    pi = _first_amount(
        text,
        (
            re.compile(
                r"current\s*p\s*&\s*i\s*payment[^\d$]{0,48}\$?\s*" + _MONEY_AMT,
                re.IGNORECASE,
            ),
            re.compile(
                r"currentp&ipayment(?:currentescrowpayment)?\$?\s*" + _MONEY_AMT,
                re.IGNORECASE,
            ),
        ),
    )
    if pi is not None and 100 <= pi <= 50_000:
        return pi
    # Payment history style: Payment $1,214.59 $779.83 $369.56 $65.20 (P + I + escrow)
    hist = re.search(
        r"(?:^|\n)\s*\d{1,2}/\d{1,2}/\d{2,4}\s+Payment\s+\$?"
        + _MONEY_AMT
        + r"\s+\$?"
        + _MONEY_AMT
        + r"\s+\$?"
        + _MONEY_AMT,
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if hist:
        principal_part = _parse_amount(hist.group(2))
        interest_part = _parse_amount(hist.group(3))
        if principal_part is not None and interest_part is not None:
            combo = principal_part + interest_part
            if 100 <= combo <= 50_000:
                return combo
    # Explicit "Principal $X Interest $Y" on amount-due breakdown
    prin = _label_amount(text, "Explanation of Amount Due Principal", "Principal")
    intr = None
    m_int = re.search(
        r"(?:ExplanationofAmountDue|Amount\s*Due)[^\n]{0,80}?Interest\s*\$?\s*"
        + _MONEY_AMT,
        text,
        re.IGNORECASE,
    )
    if m_int:
        intr = _parse_amount(m_int.group(1))
    if prin and intr and 50 <= prin <= 40_000 and 50 <= intr <= 40_000:
        return prin + intr
    return None


def _mortgage_interest_paid(text: str) -> float | None:
    interest = _first_amount(text, _INTEREST_PATTERNS)
    if interest is None:
        interest = _label_amount(
            text,
            "Interest paid",
            "INTEREST PAID",
            "YTD interest",
            "Year-to-date interest",
            "GrossInterestPaid",
            "Mortgage interest received",
        )
    # Ignore tiny matches from form boilerplate ("Box 1" → $2, etc.)
    if interest is not None and interest >= 100:
        return interest
    return None


def _mortgage_principal(text: str) -> float | None:
    principal = _first_amount(text, _PRINCIPAL_PATTERNS)
    if principal is None:
        principal = _label_amount(
            text,
            "Outstanding Principal",
            "Outstanding mortgage principal",
            "Interest bearing principal balance",
            "ENDING BAL",
            "Ending Balance",
            "BEG BAL",
        )
    if principal is not None and principal >= 1_000:
        return principal
    return None


def _mortgage_interest_rate_pct(text: str) -> float | None:
    for pattern in _RATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            rate = float(match.group(1))
        except ValueError:
            continue
        if 0.1 <= rate <= 20.0:
            return rate
    return None


def _statement_as_of(text: str, *, january_of_tax_year: bool = False) -> tuple[int, int] | None:
    """(year, month) for the amortization clock. None when the document has no date.

    Form 1098 Box 2 is the balance at the start of the tax year, so that form
    amortizes from January of the printed year rather than today's date.
    """
    if january_of_tax_year:
        year_match = _AS_OF_YEAR.search(text)
        if year_match:
            year = int(year_match.group(1))
            if 2020 <= year <= 2099:
                return year, 1
        return None
    match = _AS_OF_DATE.search(text)
    if match:
        month = int(match.group(1))
        year = int(match.group(3))
        if year < 100:
            year += 2000
        if 2020 <= year <= 2099:
            return year, month
    year_match = _AS_OF_YEAR.search(text)
    if year_match:
        year = int(year_match.group(1))
        if 2020 <= year <= 2099:
            return year, 1
    return None


def _add_payoff_year(
    out: list[ExtractedFieldSuggestion],
    keys: set[str],
    text: str,
    *,
    person: str | None,
    principal: float | None = None,
    monthly_pi: float | None = None,
    rate_pct: float | None = None,
    january_of_tax_year: bool = False,
    payoff_confidence: str = "high",
) -> None:
    if "mortgage_payoff_year" in keys:
        return
    field = resolve_field("mortgage_payoff_year", person=person)
    if field is None:
        return
    field_person = person if field.scope == "person" else None

    # 1) Prefer printed maturity year when clearly labeled
    year_match = _PAYOFF_YEAR.search(text)
    if year_match:
        year = int(year_match.group(1))
        if 2020 <= year <= 2099:
            out.append(
                ExtractedFieldSuggestion(
                    field_key="mortgage_payoff_year",
                    person=field_person,
                    label=field.label,
                    raw_value=format_field_value(field, year),
                    display_value=str(year),
                    confidence="medium",
                    source_quote=year_match.group(0)[:80],
                    document_type=DocumentType.PDF.value,
                )
            )
            keys.add("mortgage_payoff_year")
            return

    # 2) Compute from outstanding balance + P&I + note rate
    if principal and monthly_pi and rate_pct is not None:
        from model.mortgage_amort import amortization_summary

        as_of = _statement_as_of(text, january_of_tax_year=january_of_tax_year)
        if as_of is None:
            return
        as_of_year, as_of_month = as_of
        summary = amortization_summary(
            principal,
            monthly_pi,
            rate_pct,
            as_of_year=as_of_year,
            as_of_month=as_of_month,
        )
        if summary:
            year = int(summary["payoff_year"])
            out.append(
                ExtractedFieldSuggestion(
                    field_key="mortgage_payoff_year",
                    person=field_person,
                    label=field.label,
                    raw_value=format_field_value(field, year),
                    display_value=str(year),
                    confidence=payoff_confidence,
                    source_quote=(
                        f"Computed maturity {year} from balance ${principal:,.0f}, "
                        f"P&I ${monthly_pi:,.2f}/mo, rate {rate_pct:g}% "
                        f"(~{summary['years_remaining']} yrs remaining)"
                    ),
                    document_type=DocumentType.PDF.value,
                )
            )
            keys.add("mortgage_payoff_year")


def _extract_mortgage_common(
    text: str,
    *,
    person: str | None,
    interest_quote: str,
    interest_is_annual: bool,
) -> list[ExtractedFieldSuggestion]:
    """Shared 1098 / lender-statement mortgage mapping + amortization.

    ``mortgage_annual`` is principal and interest times 12 when that split is
    known, so the payment can stop at payoff without taking escrow with it.
    A total payment that still includes escrow is kept only at medium confidence.
    Year-to-date statement interest is not a full-year deduction and is not
    copied into the annual payment.
    """
    out: list[ExtractedFieldSuggestion] = []
    keys: set[str] = set()

    def _add(row: ExtractedFieldSuggestion | None) -> None:
        if row is None or not row.field_key or row.field_key in keys:
            return
        keys.add(row.field_key)
        out.append(row)

    interest = _mortgage_interest_paid(text)
    if interest:
        _add(
            _money_suggestion(
                "itemized_mortgage_interest",
                interest,
                person=None,
                quote=interest_quote,
                confidence="high" if interest_is_annual else "low",
            )
        )

    principal = _mortgage_principal(text)
    monthly_total = _mortgage_monthly_payment(text)
    monthly_pi = _mortgage_monthly_pi(text)
    rate_pct = _mortgage_interest_rate_pct(text)

    if monthly_pi:
        annual = round(monthly_pi * 12.0, 2)
        _add(
            _money_suggestion(
                "mortgage_annual",
                annual,
                person=person,
                quote=(
                    f"Principal and interest ${monthly_pi:,.2f}/mo × 12 "
                    f"= ${annual:,.0f}/yr (escrow excluded)"
                ),
                confidence="high",
            )
        )
    elif monthly_total:
        annual = round(monthly_total * 12.0, 2)
        _add(
            _money_suggestion(
                "mortgage_annual",
                annual,
                person=person,
                quote=(
                    f"Total payment ${monthly_total:,.2f}/mo × 12 = ${annual:,.0f}/yr. "
                    "This includes escrow, so do not also add a property-tax bill."
                ),
                confidence="medium",
            )
        )

    _add_payoff_year(
        out,
        keys,
        text,
        person=person,
        principal=principal,
        monthly_pi=monthly_pi,
        rate_pct=rate_pct,
        january_of_tax_year=interest_is_annual,
        payoff_confidence="high" if monthly_pi else "medium",
    )
    return out


def extract_1098_suggestions(text: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
    """IRS Form 1098 — annual mortgage interest, plus P&I when the form shows it."""
    return _extract_mortgage_common(
        text,
        person=person,
        interest_quote="Form 1098 Box 1 — mortgage interest for the tax year",
        interest_is_annual=True,
    )


def extract_mortgage_statement_suggestions(
    text: str, *, person: str | None = "self"
) -> list[ExtractedFieldSuggestion]:
    """Lender mortgage / HELOC statement — payment, rate, balance → maturity year."""
    return _extract_mortgage_common(
        text,
        person=person,
        interest_quote="Mortgage statement year-to-date interest (not a full year)",
        interest_is_annual=False,
    )


_PROPERTY_TAX_TOTAL_PATTERNS = (
    re.compile(
        r"(?:total\s+(?:amount\s+)?due|total\s+tax(?:es)?\s+due|"
        r"total\s+property\s+tax(?:es)?|annual\s+tax|"
        r"current\s+tax\s+levy|total\s+levy)"
        r"[^\n\d$]{0,40}\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
)
_PROPERTY_TAX_INSTALLMENT_PATTERNS = (
    re.compile(
        r"(?:(?:first|1st|second|2nd)\s+installment|installment\s*1)"
        r"[^\n\d$]{0,40}\$?\s*" + _MONEY_AMT,
        re.IGNORECASE,
    ),
)


def extract_property_tax_bill_suggestions(
    text: str, *, person: str | None = "self"
) -> list[ExtractedFieldSuggestion]:
    """County / city property tax bill — annual total, or one installment × 2."""
    out: list[ExtractedFieldSuggestion] = []
    total = _first_amount(text, _PROPERTY_TAX_TOTAL_PATTERNS)
    if total is None:
        total = _label_amount(
            text,
            "Total Amount Due",
            "Total Tax Due",
            "Total Taxes Due",
            "Annual Tax",
        )
    if total and 50 <= total <= 500_000:
        annual = total
        quote = "Property tax bill — total / annual amount"
    else:
        installment = _first_amount(text, _PROPERTY_TAX_INSTALLMENT_PATTERNS)
        if not installment or not (50 <= installment <= 250_000):
            return out
        annual = round(installment * 2.0, 2)
        quote = "Property tax bill — installment × 2 (annualized)"
    suggestion = _money_suggestion(
        "property_tax_annual",
        annual,
        person=person,
        quote=quote,
        confidence="high" if "total" in quote else "medium",
    )
    if suggestion:
        out.append(suggestion)
    return out


def parse_statement_pdf(
    path: str,
    *,
    document_id: str,
    person: str | None = "self",
) -> list[ExtractedFieldSuggestion]:
    file_path = Path(path)
    if not file_path.is_file():
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=file_path.name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=f"File not found: {path}",
            )
        ]
    try:
        text = extract_pdf_text(path)
    except Exception as exc:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=file_path.name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=f"Could not read PDF: {exc}",
            )
        ]
    if not text.strip():
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=file_path.name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes="No readable text — use a digital PDF export, not a scan.",
            )
        ]

    if document_id == "ssa_1099":
        rows = extract_ssa_suggestions(text, person=person)
    elif document_id == "brokerage_statement":
        rows = extract_brokerage_suggestions(text, person=person)
    elif document_id == "401k_statement":
        rows = extract_401k_suggestions(text, person=person)
    elif document_id in {"1099_r", "1099_int", "1099_div"}:
        rows = extract_1099_r_suggestions(text, person=person) if document_id == "1099_r" else []
    elif document_id == "form_1098":
        rows = extract_1098_suggestions(text, person=person)
    elif document_id == "mortgage_statement":
        rows = extract_mortgage_statement_suggestions(text, person=person)
        # Fall back to 1098 patterns if the PDF is a combined packet
        if not rows:
            rows = extract_1098_suggestions(text, person=person)
    elif document_id == "property_tax_bill":
        rows = extract_property_tax_bill_suggestions(text, person=person)
    else:
        rows = []

    if not rows:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=file_path.name,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=(
                    "Could not map fields with rules. "
                    "Local AI will try next if available, or enter values in Guided."
                ),
            )
        ]
    return rows

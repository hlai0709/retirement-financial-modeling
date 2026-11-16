"""Extract contribution split and related fields from pay stub PDF text."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from app.core.presentation.field_editor import resolve_field
from config.field_values import format_field_value
from config.model_fields import ModelField
from providers.document_extractors.pdf_text import extract_pdf_text
from settings import PROJECT_ROOT

_AMOUNT = re.compile(
    r"(?<![\w.])(?P<amt>\$?\s*-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\$?\s*-?\d+(?:\.\d{1,2})?)(?![\w.])"
)
# LMPeople / some portals glue Current+YTD: 0.0024,500.00 or 917.641,805.66
_GLUED_CURRENT_YTD = re.compile(
    r"(?P<cur>\d+\.\d{2})(?P<ytd>\d{1,3}(?:,\d{3})+\.\d{2}|\d{1,6}\.\d{2})"
)
_PAY_STUB_MARKER = re.compile(
    r"pay\s*stub|paycheck|earnings\s+statement|payroll|pay\s*period|"
    r"year[\s-]*to[\s-]*date|\bytd\b|net\s+pay|gross\s+pay|before-tax\s+deductions|"
    r"lmpeople|employee\s+service\s+center",
    re.IGNORECASE,
)
_EMPLOYER_MATCH = re.compile(r"\b(match|employer\s+contrib|company\s+match|safe\s*harbor)\b", re.I)
_YTD_HINT = re.compile(r"\bytd\b|year[\s-]*to[\s-]*date|y\.t\.d", re.I)
_BEFORE_TAX_HDR = re.compile(r"before[\s-]*tax\s+deductions?", re.I)
_AFTER_TAX_HDR = re.compile(r"after[\s-]*tax\s+deductions?", re.I)
_ANNUAL_SALARY = re.compile(
    r"\$?\s*(?P<amt>\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{4,}(?:\.\d{2})?)\s*/\s*annual",
    re.I,
)
_GROSS_YTD_ROW = re.compile(
    r"(?:^|\n)\s*YTD\s*:\s*(?P<gross>\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})",
    re.I,
)
_PAY_PERIOD = re.compile(
    r"(?P<start>\d{1,2}/\d{1,2}/\d{2,4})\s+to\s+(?P<end>\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)


def _parse_amount(raw: str) -> float | None:
    cleaned = raw.replace("$", "").replace(",", "").replace(" ", "").strip()
    if not cleaned or cleaned in {"-", "—", "."}:
        return None
    try:
        return abs(float(cleaned))
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _load_parser_config() -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "lazy_mode" / "parsers" / "pay_stub_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _line_amounts(line: str) -> list[float]:
    """Parse money tokens; split Current+YTD when portals glue them together."""
    values: list[float] = []
    # Prefer glued current/ytd pairs when present (LMPeople print view)
    glued = list(_GLUED_CURRENT_YTD.finditer(line))
    if glued:
        for match in glued:
            cur = _parse_amount(match.group("cur"))
            ytd = _parse_amount(match.group("ytd"))
            if cur is not None:
                values.append(cur)
            if ytd is not None:
                values.append(ytd)
        if values:
            return values

    for match in _AMOUNT.finditer(line):
        amount = _parse_amount(match.group("amt"))
        if amount is not None:
            values.append(amount)
    return values


def _pick_amount_from_line(line: str, *, prefer_ytd: bool) -> float | None:
    """Pick current vs YTD amount from a deduction line.

    Common layouts:
      Label  current  ytd
      Label  ytd only
      Label  amount
    Prefer the rightmost amount when YTD is indicated; else last amount.
    """
    amounts = _line_amounts(line)
    if not amounts:
        return None
    money = [value for value in amounts if value >= 1.0]
    if not money:
        money = amounts
    if prefer_ytd or _YTD_HINT.search(line):
        return money[-1]
    if len(money) >= 2:
        # Two columns: current, YTD — use YTD (last) for annualization-friendly totals
        return money[-1]
    return money[0]


def _iter_sectioned_lines(text: str) -> list[tuple[str, str]]:
    """Return (section, line) with section in before_tax | after_tax | other."""
    section = "other"
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = _normalize_line(raw)
        if not line:
            continue
        if _BEFORE_TAX_HDR.search(line):
            section = "before_tax"
            out.append((section, line))
            continue
        if _AFTER_TAX_HDR.search(line):
            section = "after_tax"
            out.append((section, line))
            continue
        # Reset section on major new blocks
        if re.search(r"^(taxes|hours/earnings|gross earnings|net pay|paid time off)\b", line, re.I):
            section = "other"
        out.append((section, line))
    return out


def _label_matches(line: str, labels: list[str]) -> str | None:
    lower = line.lower()
    for label in labels:
        if label.lower() in lower:
            # Avoid treating "Roth 401k" as traditional "401k"
            if label.lower() in {"401k", "401(k)", "401 k"} and "roth" in lower:
                continue
            return label
    return None


def _sum_labeled_amounts(
    text: str,
    labels: list[str],
    *,
    exclude_match: bool = True,
    only_sections: set[str] | None = None,
) -> tuple[float, list[str]]:
    total = 0.0
    quotes: list[str] = []
    for section, line in _iter_sectioned_lines(text):
        if only_sections is not None and section not in only_sections:
            continue
        if exclude_match and _EMPLOYER_MATCH.search(line):
            continue
        if not _label_matches(line, labels):
            continue
        amount = _pick_amount_from_line(line, prefer_ytd=True)
        if amount is None:
            continue
        total += amount
        quotes.append(f"{line[:90]} → {amount:,.2f}")
    return total, quotes


def _extract_gross_ytd(text: str, labels: list[str]) -> tuple[float | None, str]:
    # Explicit YTD summary row (LMPeople): "YTD: 152,528.70 ..."
    match = _GROSS_YTD_ROW.search(text)
    if match:
        amount = _parse_amount(match.group("gross"))
        if amount is not None and amount >= 1_000:
            return amount, match.group(0).strip()[:100]

    best: float | None = None
    quote = ""
    for line in text.splitlines():
        norm = _normalize_line(line)
        lower = norm.lower()
        if not any(label.lower() in lower for label in labels):
            if not (re.search(r"\bgross\b", lower) and _YTD_HINT.search(lower)):
                continue
        amount = _pick_amount_from_line(norm, prefer_ytd=True)
        if amount is None or amount < 100:
            continue
        if best is None or amount > best:
            best = amount
            quote = norm[:100]
    return best, quote


def _extract_annual_salary(text: str) -> tuple[float | None, str]:
    match = _ANNUAL_SALARY.search(text)
    if not match:
        return None, ""
    amount = _parse_amount(match.group("amt"))
    if amount is None or amount < 5_000:
        return None, ""
    return amount, match.group(0).strip()[:80]


def _pay_periods_per_year(text: str) -> tuple[int, str]:
    """Infer pay frequency from pay-period date range (default biweekly)."""
    match = _PAY_PERIOD.search(text)
    if not match:
        return 26, "biweekly (assumed)"
    try:
        from datetime import datetime

        def _parse_dt(raw: str) -> datetime:
            for fmt in ("%m/%d/%Y", "%m/%d/%y"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
            raise ValueError(raw)

        start = _parse_dt(match.group("start"))
        end = _parse_dt(match.group("end"))
        days = abs((end - start).days) + 1
    except (TypeError, ValueError):
        return 26, "biweekly (assumed)"
    if days <= 8:
        return 52, f"weekly ({days}-day period)"
    if days <= 16:
        return 26, f"biweekly ({days}-day period)"
    if days <= 20:
        return 24, f"semi-monthly ({days}-day period)"
    if days <= 35:
        return 12, f"monthly ({days}-day period)"
    return 26, f"biweekly (assumed; {days}-day period)"


def _sum_health_premiums(
    text: str,
    labels: list[str],
) -> tuple[float, float, list[str]]:
    """Return (period_total, ytd_total, quotes) for employee health premiums.

    Uses per-line current + YTD when two money columns are present (LMPeople style).
    """
    period_total = 0.0
    ytd_total = 0.0
    quotes: list[str] = []
    for section, line in _iter_sectioned_lines(text):
        # Premiums are usually before-tax deductions; allow other sections too
        if not _label_matches(line, labels):
            continue
        # Skip pure headers
        if re.search(r"before[\s-]*tax|after[\s-]*tax|description", line, re.I) and not any(
            ch.isdigit() for ch in line
        ):
            continue
        amounts = _line_amounts(line)
        money = [value for value in amounts if value >= 0.5]
        if not money:
            continue
        if len(money) >= 2:
            period_amt, ytd_amt = money[0], money[-1]
        else:
            # Single amount: treat as YTD if large, else period
            if money[0] >= 200:
                period_amt, ytd_amt = 0.0, money[0]
            else:
                period_amt, ytd_amt = money[0], money[0]
        period_total += period_amt
        ytd_total += ytd_amt
        quotes.append(f"{line[:70]} → period ${period_amt:,.2f} / YTD ${ytd_amt:,.2f}")
    return period_total, ytd_total, quotes


def _format_money(field: ModelField, value: float) -> tuple[str, str]:
    raw = format_field_value(field, value)
    display = f"${value:,.0f}"
    return raw, display


def _format_rate(field: ModelField, fraction: float) -> tuple[str, str]:
    raw = format_field_value(field, fraction)
    display = f"{fraction * 100:.1f}%"
    return raw, display


def extract_pay_stub_suggestions(
    text: str,
    *,
    label: str = "Pay stub",
    person: str | None = "self",
) -> list[ExtractedFieldSuggestion]:
    config = _load_parser_config()
    roth_labels = list(config.get("roth_labels") or [])
    trad_labels = list(config.get("traditional_labels") or [])
    after_tax_labels = list(config.get("after_tax_retirement_labels") or [])
    gross_labels = list(config.get("gross_labels") or [])

    if not _PAY_STUB_MARKER.search(text):
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=label,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote="",
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=(
                    "This file does not look like a pay stub. "
                    "Upload a digital PDF pay stub from payroll (ADP, Workday, Paychex, LMPeople, etc.)."
                ),
            )
        ]

    # Traditional: generic labels + before-tax Savings-SSP style lines
    trad_total, trad_quotes = _sum_labeled_amounts(text, trad_labels)
    # Roth explicit labels anywhere
    roth_total, roth_quotes = _sum_labeled_amounts(text, roth_labels)
    # After-tax retirement lines (LMPeople Savings-SSP under After-Tax) → Roth bucket for split
    after_tax_total, after_tax_quotes = _sum_labeled_amounts(
        text,
        after_tax_labels,
        only_sections={"after_tax"},
    )
    if after_tax_total > 0:
        roth_total += after_tax_total
        roth_quotes.extend(after_tax_quotes)

    # If Savings-SSP only appeared as traditional from global labels, but we also
    # double-counted after-tax SSP into traditional via "savings-ssp" in trad_labels,
    # subtract after-tax SSP that was under after_tax section from trad.
    if after_tax_total > 0 and trad_total >= after_tax_total:
        # Recompute traditional restricted to before_tax + other (not after_tax)
        trad_total, trad_quotes = _sum_labeled_amounts(
            text,
            trad_labels,
            only_sections={"before_tax", "other"},
        )

    deferral_total = roth_total + trad_total
    suggestions: list[ExtractedFieldSuggestion] = []

    if deferral_total >= 1:
        roth_fraction = roth_total / deferral_total if deferral_total > 0 else 0.0
        roth_field = resolve_field("contribution_roth_pct", person=None)
        if roth_field is not None:
            raw, display = _format_rate(roth_field, roth_fraction)
            quote_bits = []
            if roth_quotes:
                quote_bits.append(f"Roth/after-tax YTD ${roth_total:,.0f}")
            if trad_quotes:
                quote_bits.append(f"Traditional YTD ${trad_total:,.0f}")
            suggestions.append(
                ExtractedFieldSuggestion(
                    field_key="contribution_roth_pct",
                    person=None,
                    label=roth_field.label,
                    raw_value=raw,
                    display_value=display,
                    confidence="high" if trad_total > 0 and roth_total > 0 else "medium",
                    source_quote="; ".join(quote_bits)
                    or f"Roth share of deferrals ({roth_fraction * 100:.1f}%)",
                    document_type=DocumentType.PDF.value,
                )
            )

        contrib_field = resolve_field("annual_contributions", person=person)
        if contrib_field is not None:
            annual = deferral_total
            note = "sum of traditional + Roth/after-tax deferrals on stub"
            if deferral_total < 5_000:
                annual = deferral_total * 26
                note = f"period deferrals ${deferral_total:,.0f} × 26 pay periods (estimate)"
            else:
                note = (
                    f"YTD-style deferrals ${deferral_total:,.0f} "
                    "(use as annual if full year; else scale)"
                )
            field_person = person if contrib_field.scope == "person" else None
            raw, display = _format_money(contrib_field, annual)
            suggestions.append(
                ExtractedFieldSuggestion(
                    field_key="annual_contributions",
                    person=field_person,
                    label=contrib_field.label,
                    raw_value=raw,
                    display_value=display,
                    confidence="medium",
                    source_quote=note,
                    document_type=DocumentType.PDF.value,
                )
            )

    salary, salary_quote = _extract_annual_salary(text)
    gross, gross_quote = _extract_gross_ytd(text, gross_labels)
    income_field = resolve_field("annual_earned_income", person=person)
    if income_field is not None:
        field_person = person if income_field.scope == "person" else None
        if salary is not None and salary >= 5_000:
            raw, display = _format_money(income_field, salary)
            suggestions.append(
                ExtractedFieldSuggestion(
                    field_key="annual_earned_income",
                    person=field_person,
                    label=income_field.label,
                    raw_value=raw,
                    display_value=display,
                    confidence="high",
                    source_quote=salary_quote or f"Annual compensation ${salary:,.0f}",
                    document_type=DocumentType.PDF.value,
                )
            )
        elif gross is not None and gross >= 1_000:
            annual_gross = gross
            gnote = f"Gross YTD ${gross:,.0f}"
            if gross < 20_000:
                annual_gross = gross * 26
                gnote = f"Gross period ${gross:,.0f} × 26 (estimate)"
            raw, display = _format_money(income_field, annual_gross)
            suggestions.append(
                ExtractedFieldSuggestion(
                    field_key="annual_earned_income",
                    person=field_person,
                    label=income_field.label,
                    raw_value=raw,
                    display_value=display,
                    confidence="medium",
                    source_quote=gross_quote or gnote,
                    document_type=DocumentType.PDF.value,
                )
            )

    # Employee health premiums → employer_health_monthly (plan uses while working)
    health_labels = list(config.get("health_premium_labels") or [])
    if health_labels:
        period_prem, ytd_prem, health_quotes = _sum_health_premiums(text, health_labels)
        periods_per_year, freq_note = _pay_periods_per_year(text)
        monthly: float | None = None
        conf = "medium"
        quote = ""
        if period_prem >= 1:
            monthly = period_prem * periods_per_year / 12.0
            conf = "high"
            quote = (
                f"Employee health premiums this period ${period_prem:,.2f} × "
                f"{periods_per_year}/12 ({freq_note}) = ${monthly:,.0f}/mo"
            )
            if health_quotes:
                quote = f"{quote}; " + "; ".join(health_quotes[:4])
        elif ytd_prem >= 50:
            # Mid-year YTD: rough annualize assuming ~half year if YTD < full annual guess
            # Prefer period when available; here scale YTD by calendar progress if possible
            monthly = ytd_prem / 12.0 if ytd_prem > 3_000 else (ytd_prem * 2.0) / 12.0
            conf = "medium"
            quote = (
                f"Employee health YTD ${ytd_prem:,.0f} annualized ≈ ${monthly * 12:,.0f}/yr "
                f"(${monthly:,.0f}/mo)"
            )
        if monthly is not None and monthly >= 5:
            mode_field = resolve_field("employer_health_mode", person=person)
            monthly_field = resolve_field("employer_health_monthly", person=person)
            field_person = person if (monthly_field and monthly_field.scope == "person") else person
            if mode_field is not None:
                suggestions.append(
                    ExtractedFieldSuggestion(
                        field_key="employer_health_mode",
                        person=person if mode_field.scope == "person" else None,
                        label=mode_field.label,
                        raw_value="employer",
                        display_value="employer",
                        confidence=conf,
                        source_quote="Pay stub health premiums → employer coverage mode",
                        document_type=DocumentType.PDF.value,
                    )
                )
            if monthly_field is not None:
                raw, display = _format_money(monthly_field, monthly)
                suggestions.append(
                    ExtractedFieldSuggestion(
                        field_key="employer_health_monthly",
                        person=person if monthly_field.scope == "person" else None,
                        label=monthly_field.label,
                        raw_value=raw,
                        display_value=display,
                        confidence=conf,
                        source_quote=quote[:240],
                        document_type=DocumentType.PDF.value,
                    )
                )
            # Also suggest annual employee cost into pre-Medicare field as a reference
            # for years without employer coverage (user can uncheck). Engine zeros this
            # while employer_health is active in working years.
            pre_field = resolve_field("health_insurance_pre_medicare", person=person)
            if pre_field is not None:
                annual_ee = monthly * 12.0
                raw, display = _format_money(pre_field, annual_ee)
                suggestions.append(
                    ExtractedFieldSuggestion(
                        field_key="health_insurance_pre_medicare",
                        person=person if pre_field.scope == "person" else None,
                        label=pre_field.label,
                        raw_value=raw,
                        display_value=display,
                        confidence="medium",
                        source_quote=(
                            f"Same employee premiums annualized ${annual_ee:,.0f}/yr "
                            f"(used if no employer coverage; working years prefer Employer Health Monthly)"
                        ),
                        document_type=DocumentType.PDF.value,
                    )
                )

    if not suggestions:
        return [
            ExtractedFieldSuggestion(
                field_key=None,
                person=None,
                label=label,
                raw_value="",
                display_value="",
                confidence="low",
                source_quote=text[:400],
                document_type=DocumentType.PDF.value,
                ok=False,
                notes=(
                    "Could not find 401(k)/Roth deduction lines or gross pay on this pay stub. "
                    "Look for labels like 'Roth 401(k)', 'Pre-tax 401(k)', or 'Savings-SSP' "
                    "with dollar amounts. "
                    "Update config/lazy_mode/parsers/pay_stub_v1.json if your payroll uses different names."
                ),
            )
        ]

    return suggestions


def parse_pay_stub_pdf(path: str, *, person: str | None = "self") -> list[ExtractedFieldSuggestion]:
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
                notes="No readable text in this PDF. Use a digital pay stub PDF (not a photo/scan).",
            )
        ]
    return extract_pay_stub_suggestions(text, label=file_path.name, person=person)

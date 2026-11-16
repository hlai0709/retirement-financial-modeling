"""Extract planning fields from Form 1040 PDF exports (text layer)."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from providers.assumption_research import _extract_json_payload
from providers.document_extractors.pdf_text import extract_pdf_text
from providers.document_extractors.tax_return_json import extract_tax_return_suggestions
from providers.llm.factory import get_llm_provider
from providers.llm.models_catalog import resolve_model_choice
from security.llm_keychain import get_api_key
from settings import load_llm_settings

# Dollar-looking token: optional $, digits with optional thousands commas, optional cents.
_AMOUNT_TOKEN = re.compile(
    r"(?<![\w.])(?P<amt>\$?\s*-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\$?\s*-?\d+(?:\.\d{1,2})?)(?![\w.])"
)
# Line id at start of a logical line: 1z, 2b, 11, 15, …
_LINE_START = re.compile(
    r"(?:^|\n)\s*(?P<line>\d{1,2}[a-z]?)\b(?!\s*/)",
    re.IGNORECASE,
)

_LLM_SYSTEM = (
    "You extract U.S. Form 1040 values from OCR/text exports. "
    "Respond with ONLY valid JSON matching the requested schema. "
    "Use null for missing fields. Do not invent numbers."
)


def _parse_amount(raw: str) -> float | None:
    cleaned = raw.replace("$", "").replace(",", "").replace(" ", "").strip()
    if not cleaned or cleaned in {"-", "—", "."}:
        return None
    # Reject pure line-code leftovers like "1z"
    if re.fullmatch(r"\d{1,2}[a-z]", cleaned, re.IGNORECASE):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _amounts_in(text: str) -> list[float]:
    values: list[float] = []
    for match in _AMOUNT_TOKEN.finditer(text):
        amount = _parse_amount(match.group("amt"))
        if amount is not None:
            values.append(amount)
    return values


def _pick_money_amount(candidates: list[float], *, prefer_large: bool = True) -> float | None:
    """Choose the most plausible dollar amount from tokens found on a form line.

    TurboTax/IRS text often includes junk numbers first (copy #, box 1, form year)
    and puts the real amount last / as a comma-formatted figure.
    """
    if not candidates:
        return None
    # Prefer comma-scale money (already parsed as float; keep large values)
    large = [value for value in candidates if abs(value) >= 100]
    if prefer_large and large:
        # Rightmost large amount on the line is almost always the form entry
        return large[-1]
    # Allow small values only when nothing larger exists (interest, etc.)
    meaningful = [value for value in candidates if abs(value) >= 1 or value == 0]
    if not meaningful:
        return candidates[-1]
    return meaningful[-1]


def _line_span_text(text: str, content_start: int) -> str:
    """Return text from the line id through the end of that form line (or a short window).

    ``content_start`` should be the index of the line code itself (not a leading newline).
    """
    rest = text[content_start:]
    # Prefer the same physical line when it already contains money
    nl = rest.find("\n")
    first_line = rest[:nl] if nl != -1 else rest[:220]
    first_line = re.sub(r"[ \t]+", " ", first_line)
    if _amounts_in(first_line):
        return first_line
    # Else allow a short multi-line window until the next line id
    end_m = re.search(r"\n\s*\d{1,2}[a-z]?\b", rest[1:] if len(rest) > 1 else "", re.IGNORECASE)
    if end_m:
        chunk = rest[: end_m.start() + 1]
    else:
        chunk = rest[:220]
    return re.sub(r"[ \t]+", " ", chunk)


def _line_value(text: str, line_code: str) -> float | None:
    """Extract the dollar amount for a Form 1040 line code (e.g. 1z, 11, 2b)."""
    target = line_code.lower()
    best: float | None = None
    for match in _LINE_START.finditer(text):
        if match.group("line").lower() != target:
            continue
        # Start at the line code, not the leading newline captured by the regex
        content_start = match.start("line")
        span = _line_span_text(text, content_start)
        # Drop the line id itself from the scan so "11" is not read as $11
        after_id = re.sub(rf"^\s*{re.escape(line_code)}\b", "", span, count=1, flags=re.IGNORECASE)
        amounts = _amounts_in(after_id)
        # Filter out the line number if it reappears as a bare integer equal to the code's digits
        digits = re.match(r"(\d+)", line_code)
        if digits:
            code_num = float(digits.group(1))
            amounts = [value for value in amounts if value != code_num or abs(value) >= 100]
        # Filter lone "2" from "W-2" noise when larger money exists
        if any(abs(value) >= 100 for value in amounts):
            amounts = [value for value in amounts if abs(value) >= 100 or value == 0]
        picked = _pick_money_amount(amounts, prefer_large=True)
        if picked is not None:
            best = picked
    return best


def _schedule_a_line_amount(text: str, line_code: str) -> float | None:
    """Schedule A line amounts (5a–5e, 7, 8a, …) when present in the text layer."""
    code = line_code.lower()
    # Prefer "5b 12,345" / "5b $12,345" after the real-estate-taxes label
    patterns = (
        rf"(?:^|\n)\s*{re.escape(code)}\s+[.\s]*\$?\s*"
        rf"(-?\d{{1,3}}(?:,\d{{3}})+(?:\.\d{{1,2}})?|-?\d{{4,}}(?:\.\d{{1,2}})?)",
        rf"real\s+estate\s+taxes[^\n]{{0,40}}{re.escape(code)}[^\n]{{0,40}}"
        rf"\$?\s*(-?\d{{1,3}}(?:,\d{{3}})+(?:\.\d{{1,2}})?|-?\d{{4,}}(?:\.\d{{1,2}})?)",
    )
    if code == "5b":
        patterns = patterns + (
            rf"total\s+real\s+estate\s+taxes[^\n\d]{{0,40}}"
            rf"\$?\s*(-?\d{{1,3}}(?:,\d{{3}})+(?:\.\d{{1,2}})?|-?\d{{4,}}(?:\.\d{{1,2}})?)",
        )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        amount = _parse_amount(match.group(1))
        if amount is not None and abs(amount) >= 50:
            return amount
    # Fall back to generic Form 1040 line scanner
    return _line_value(text, line_code)


def _label_value(text: str, *labels: str) -> float | None:
    for label in labels:
        # TurboTax summary: "Adjusted Gross Income                   $    259,300.00"
        pattern = (
            rf"{re.escape(label)}\s*[|\u2502]?\s*"
            rf"(?P<body>\$?\s*-?\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?|-?\d+(?:\.\d{{1,2}})?)"
        )
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = _parse_amount(match.group("body"))
            if amount is not None:
                return amount
        # Same-line loose body (generic PDFs)
        pattern2 = rf"{re.escape(label)}(?P<body>[^\n]{{0,120}})"
        match2 = re.search(pattern2, text, re.IGNORECASE)
        if not match2:
            continue
        body = match2.group("body")
        amounts = _amounts_in(body)
        filtered = [
            value
            for value in amounts
            if abs(value) >= 100 or (abs(value) >= 1 and "," in body)
        ]
        picked = _pick_money_amount(filtered or amounts, prefer_large=True)
        if picked is not None and abs(picked) >= 1:
            if abs(picked) < 100 and any(token in label.lower() for token in ("wage", "w-2", "w2")):
                continue
            return picked
    return None


def _is_turbotax_export(text: str) -> bool:
    markers = (
        r"File by Mail Instructions",
        r"\bTTW\b",
        r"BAA REV\s+\d{2}/\d{2}/\d{2}\s+TTW",
        r"TurboTax",
        r"Print Center",
        r"Federal Tax\s+Return\s+Summary",
    )
    return any(re.search(marker, text, re.IGNORECASE) for marker in markers)


def _labeled_money(text: str, *labels: str) -> float | None:
    """Require a $ amount after the label (avoids matching form line numbers)."""
    for label in labels:
        pattern = (
            rf"{re.escape(label)}\s*[|\u2502]?\s*\$\s*"
            rf"(?P<body>-?\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?|-?\d+(?:\.\d{{1,2}})?)"
        )
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = _parse_amount(match.group("body"))
            if amount is not None:
                return amount
    return None


def _parse_turbotax_summary(text: str) -> dict[str, float]:
    """Parse TurboTax File-by-Mail / Federal Return Summary dollar table."""
    labels = {
        "agi": ("Adjusted Gross Income",),
        "taxable_income": ("Taxable Income",),
        "total_tax": ("Total Tax",),
        "total_payments": ("Total Payments/Credits", "Total Payments"),
        "payment_due": ("Payment Due", "Amount You Owe"),
    }
    out: dict[str, float] = {}
    for key, names in labels.items():
        value = _labeled_money(text, *names)
        if value is not None:
            out[key] = value
    rate_m = re.search(
        r"Effective Tax Rate\s+\$?\s*([\d.]+)\s*%",
        text,
        re.IGNORECASE,
    )
    if rate_m:
        try:
            out["effective_tax_rate_pct"] = float(rate_m.group(1))
        except ValueError:
            pass
    return out


def _form_1040_core_text(text: str) -> str:
    """Restrict to official Form 1040 page 1–2 text (not worksheets / schedules).

    TurboTax packages are 50–100+ pages; amount-column heuristics must not see
    every worksheet or they latch onto withholding / credit repeats.

    Important: TurboTax extracts *labels first*, then the right-hand money column
    at the *end* of the page — the window must be large enough to include that dump.
    """
    match = re.search(
        r"Form\s*1040\s+\d{4}\s*U\.S\.\s+Individual Income Tax Return"
        r"|Form1040\s+\d{4}U\.S\.\s*Individual Income Tax Return"
        r"|Form\s+1040\s+\(\d{4}\)",
        text,
        re.IGNORECASE,
    )
    if match:
        # ~2 form pages of text + amount columns
        chunk = text[match.start() : match.start() + 18_000]
        if re.search(r"Total amount from Form\(s\) W-2|Add lines 1a through 1h", chunk, re.I):
            return chunk

    match = re.search(
        r"Total amount from Form\(s\) W-2[\s\S]{0,10000}",
        text,
        re.I,
    )
    if match:
        return match.group(0)
    return text[:15_000]


def _trailing_money_column(text: str) -> list[float]:
    """TurboTax/IRS PDFs often dump the right-hand money column after labels.

    Typical tokens look like ``227,540.`` or ``0.`` on their own lines.
    """
    values: list[float] = []
    for match in re.finditer(
        r"(?m)^\s*(\d{1,3}(?:,\d{3})+|\d+)\.(?:\d{2})?\s*$",
        text,
    ):
        amount = _parse_amount(match.group(1))
        if amount is not None:
            values.append(amount)
    # Also "6,756. 3,673." multi-value lines
    for match in re.finditer(
        r"(?m)^\s*((?:\d{1,3}(?:,\d{3})+|\d+)\.(?:\d{2})?(?:\s+(?:\d{1,3}(?:,\d{3})+|\d+)\.(?:\d{2})?)+)\s*$",
        text,
    ):
        for token in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", match.group(1)):
            amount = _parse_amount(token)
            if amount is not None:
                values.append(amount)
    return values


def _infer_wages_from_amount_column(
    text: str,
    *,
    agi: float | None,
    exclude: set[float] | None = None,
) -> float | None:
    """Infer line 1z wages from TurboTax right-column amount dump.

    On Form 1040 page 1, wages (1a/1z) are large amounts ≤ AGI and often repeated.
    Prefer the **largest** repeated candidate below AGI (not the most frequent —
    withholding / worksheet totals often repeat more than wages).
    """
    from collections import Counter

    amounts = _trailing_money_column(text)
    if not amounts:
        amounts = [value for value in _amounts_in(text) if abs(value) >= 1_000]
    if not amounts:
        return None

    banned = {round(value, 2) for value in (exclude or set()) if value is not None}
    if agi is not None:
        banned.add(round(agi, 2))

    if agi is not None and agi >= 1000:
        candidates = [value for value in amounts if 1_000 <= value <= agi + 0.01]
    else:
        candidates = [value for value in amounts if value >= 1_000]
    candidates = [value for value in candidates if round(value, 2) not in banned]
    if not candidates:
        return None

    counts = Counter(round(value, 2) for value in candidates)
    repeated = [value for value, count in counts.items() if count >= 2]
    if repeated:
        return float(max(repeated))

    if agi is not None:
        below = [value for value in candidates if value < agi - 1]
        if below:
            return float(max(below))
    return float(max(candidates))


def _detect_tax_year(text: str) -> int | None:
    for pattern in (
        r"File by Mail Instructions for your\s+(\d{4})\s+Federal",
        r"Form\s*1040\s+(\d{4})",
        r"Form1040\s+(\d{4})",
        r"Form\s+1040(?:\s*\([^)]+\))?\s+(\d{4})",
        r"U\.S\.\s+Individual\s+Income\s+Tax\s+Return\s+(\d{4})",
        r"Tax\s+Year\s+(\d{4})",
        r"\b(20[12]\d)\s+Form\s+1040\b",
        r"For the year Jan\.\s*1–Dec\.\s*31,\s*(\d{4})",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            if 2018 <= year <= 2035:
                return year
    return None


def _detect_state(text: str) -> str | None:
    match = re.search(
        r"(?:state(?:\s+of)?\s+residence|your\s+state)[:\s]+([A-Z]{2})\b",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()
    # Address line: CITY ST ZIP
    match = re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", text)
    if match:
        return match.group(1).upper()
    return None


def _detect_filing_status(text: str) -> str | None:
    # Prefer checked boxes when text shows "X Married filing jointly" style
    patterns = (
        (r"(?:☑|☒|\[x\]|✓)\s*married\s+filing\s+jointly", "mfj"),
        (r"(?:☑|☒|\[x\]|✓)\s*single", "single"),
        (r"(?:☑|☒|\[x\]|✓)\s*head\s+of\s+household", "hoh"),
        (r"(?:☑|☒|\[x\]|✓)\s*married\s+filing\s+separately", "mfs"),
        (r"married\s+filing\s+jointly", "mfj"),
        (r"\bsingle\b", "single"),
        (r"head\s+of\s+household", "hoh"),
        (r"married\s+filing\s+separately", "mfs"),
    )
    for pattern, status in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return status
    return None


def parse_1040_text_to_payload(text: str, *, label: str = "Form 1040 PDF") -> dict[str, Any]:
    """Best-effort mapping of common 1040 PDF text layouts to tax-return JSON shape.

    TurboTax "print for your records" PDFs put line *labels* in one stream and the
    right-hand *amount column* later on the page — line-adjacent regex fails there.
    We therefore prefer:
      1) TurboTax Federal Return Summary labels (AGI, taxable income, total tax)
      2) Amount-column heuristics for wages (1a/1z)
      3) Classic same-line Form 1040 text (generic PDFs)
    """
    normalized = text.replace("\r", "\n")
    tax_year = _detect_tax_year(normalized) or 2024
    tt_summary = _parse_turbotax_summary(normalized) if _is_turbotax_export(normalized) else {}
    # Also try summary labels on any PDF (harmless if absent)
    if not tt_summary:
        tt_summary = _parse_turbotax_summary(normalized)

    form_core = _form_1040_core_text(normalized)
    agi_guess = tt_summary.get("agi")

    wages = (
        _line_value(form_core, "1z")
        or _line_value(form_core, "1a")
        or _label_value(
            form_core,
            "wages, salaries, tips, etc",
            "wages, tips, other compensation",
            "total amount from form(s) w-2",
        )
    )
    # Guard: tiny values are almost always line/box junk (e.g. "box 1")
    if wages is not None and abs(wages) < 1000:
        wages = None
    if wages is None or (agi_guess and wages and wages > agi_guess * 1.05):
        exclude = {
            tt_summary.get("taxable_income"),
            tt_summary.get("total_tax"),
            tt_summary.get("total_payments"),
            tt_summary.get("payment_due"),
        }
        inferred = _infer_wages_from_amount_column(
            form_core,
            agi=agi_guess,
            exclude={value for value in exclude if value is not None},
        )
        if inferred is not None:
            wages = inferred

    def _money_field(*extractors: float | None, min_abs: float = 100) -> float | None:
        for value in extractors:
            if value is None:
                continue
            if abs(value) < min_abs:
                continue
            return value
        return None

    income = {
        "wages": wages,
        "taxable_interest": _money_field(
            _line_value(form_core, "2b"),
            _label_value(form_core, "taxable interest"),
            min_abs=1,
        ),
        "ordinary_dividends": _money_field(
            _line_value(form_core, "3b"),
            _label_value(form_core, "ordinary dividends"),
            min_abs=1,
        ),
        "ira_distributions": _money_field(_line_value(form_core, "4b"), min_abs=1),
        "pension": _money_field(
            _line_value(form_core, "5b"),
            _label_value(form_core, "pensions and annuities"),
            min_abs=1,
        ),
        "social_security_gross": _money_field(
            _line_value(form_core, "6a"),
            _line_value(form_core, "6b"),
            _label_value(form_core, "social security benefits"),
            min_abs=1,
        ),
        "capital_gain": _money_field(
            _line_value(form_core, "7a"),
            _line_value(form_core, "7"),
            _label_value(form_core, "capital gain or (loss)", "capital gain"),
            min_abs=100,
        ),
        "rental_income": _money_field(
            _label_value(form_core, "rental real estate"),
            min_abs=100,
        ),
    }
    real_estate_taxes = _money_field(
        _label_value(
            normalized,
            "state and local real estate taxes",
            "real estate taxes",
            "total real estate taxes",
        ),
        _schedule_a_line_amount(normalized, "5b"),
        min_abs=50,
    )
    salt = _money_field(
        _label_value(normalized, "state and local taxes", "state and local tax"),
        _schedule_a_line_amount(normalized, "5e"),
        _schedule_a_line_amount(normalized, "5d"),
        _schedule_a_line_amount(normalized, "7"),
        min_abs=1,
    )
    # If only real estate taxes were found, still seed SALT with that floor
    if salt is None and real_estate_taxes is not None:
        salt = real_estate_taxes
    itemized = {
        "salt": salt,
        "real_estate_taxes": real_estate_taxes,
        "mortgage_interest": _money_field(
            _label_value(normalized, "home mortgage interest"),
            min_abs=1,
        ),
        "charitable": _money_field(
            _label_value(normalized, "gifts to charity", "charitable contributions"),
            min_abs=1,
        ),
        "medical": _money_field(
            _label_value(normalized, "medical and dental"),
            min_abs=1,
        ),
    }
    form_1040 = {
        "agi": tt_summary.get("agi")
        or _money_field(
            _line_value(form_core, "11a"),
            _line_value(form_core, "11"),
            _label_value(normalized, "adjusted gross income"),
            min_abs=100,
        ),
        "taxable_income": tt_summary.get("taxable_income")
        or _money_field(
            _line_value(form_core, "15"),
            _label_value(normalized, "taxable income"),
            min_abs=0,
        ),
        "income_tax": _money_field(
            _line_value(form_core, "16"),
            min_abs=100,
        ),
        "total_tax": tt_summary.get("total_tax")
        or _money_field(
            _line_value(form_core, "24"),
            _label_value(normalized, "total tax"),
            min_abs=100,
        ),
        "state_income_tax": _money_field(
            _label_value(normalized, "state income tax"),
            min_abs=1,
        ),
    }

    payload: dict[str, Any] = {
        "schema_version": 1,
        "label": label,
        "tax_year": tax_year,
        "income": {key: val for key, val in income.items() if val is not None},
        "itemized": {key: val for key, val in itemized.items() if val is not None},
        "form_1040": {key: val for key, val in form_1040.items() if val is not None},
    }
    if "effective_tax_rate_pct" in tt_summary:
        # Stash for tax-rate derivation (fraction, not percent) if AGI path missing
        payload["effective_tax_rate_pct"] = tt_summary["effective_tax_rate_pct"]

    # Drop values that are almost certainly form year / line-number junk
    income_clean = payload["income"]
    if income_clean.get("capital_gain") is not None and abs(
        float(income_clean["capital_gain"]) - float(tax_year)
    ) < 0.01:
        del income_clean["capital_gain"]
    if income_clean.get("wages") is not None and abs(
        float(income_clean["wages"]) - float(tax_year)
    ) < 0.01:
        del income_clean["wages"]
    form_clean = payload["form_1040"]
    if form_clean.get("income_tax") is not None and form_clean.get("total_tax") is not None:
        # Prefer TurboTax summary total tax; line 16 column dumps are often wrong
        if tt_summary.get("total_tax") is not None:
            del form_clean["income_tax"]
        else:
            itax = float(form_clean["income_tax"])
            ttax = float(form_clean["total_tax"])
            if ttax > 0 and (itax > ttax * 1.5 or itax < ttax * 0.05):
                del form_clean["income_tax"]
    itemized_clean = payload["itemized"]
    for key in list(itemized_clean.keys()):
        if abs(float(itemized_clean[key])) < 10:
            del itemized_clean[key]

    state = _detect_state(normalized)
    if state:
        payload["state_of_residence"] = state
    filing = _detect_filing_status(normalized)
    if filing:
        payload["filing_status"] = filing
    return payload


def _payload_has_fields(payload: dict[str, Any]) -> bool:
    income = payload.get("income") or {}
    form = payload.get("form_1040") or {}
    itemized = payload.get("itemized") or {}
    # Require at least one substantive money field (not form-line junk like $1 or $11)
    money_values = [
        value
        for value in list(income.values()) + list(form.values()) + list(itemized.values())
        if isinstance(value, (int, float)) and abs(float(value)) >= 100
    ]
    if money_values:
        return True
    return False


def _llm_parse_1040_text(text: str, *, label: str) -> dict[str, Any]:
    settings = load_llm_settings()
    if not settings.configured:
        raise RuntimeError(
            "Could not read enough Form 1040 lines from this PDF. "
            "Configure LLM Settings (Expert → AI Assistant) for AI extraction, "
            "or use tax return JSON."
        )
    api_key = get_api_key(settings.provider)
    if not api_key:
        raise RuntimeError(
            f"Could not read enough Form 1040 lines from this PDF. "
            f"Add an API key for {settings.provider} in LLM Settings, or use JSON."
        )
    model = resolve_model_choice(settings.provider, settings.model, api_key=api_key)
    excerpt = text[:14_000]
    user = (
        "Extract Form 1040 values into JSON with this shape:\n"
        '{"schema_version":1,"tax_year":2024,"filing_status":"mfj",'
        '"state_of_residence":"CA","income":{"wages":0,"taxable_interest":0,'
        '"ordinary_dividends":0,"ira_distributions":0,"pension":0,'
        '"social_security_gross":0,"capital_gain":0,"rental_income":0},'
        '"itemized":{"salt":0,"mortgage_interest":0,"charitable":0,"medical":0},'
        '"form_1040":{"agi":0,"taxable_income":0,"income_tax":0,"total_tax":0,'
        '"state_income_tax":null}}\n'
        "IMPORTANT: wages is Form 1040 line 1z (total), NOT box numbers or the digit 1. "
        "Use full annual dollar amounts (e.g. 185000 not 1).\n"
        f"Document label: {label}\n\n1040 text:\n{excerpt}"
    )
    llm = get_llm_provider(settings.provider, api_key)
    completion = llm.complete(system=_LLM_SYSTEM, user=user, model=model)
    payload = _extract_json_payload(completion.content)
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    payload.setdefault("schema_version", 1)
    payload.setdefault("label", label)
    return payload


def extract_tax_return_pdf(path: str) -> tuple[dict[str, Any], list[ExtractedFieldSuggestion]]:
    file_path = Path(path)
    label = file_path.name
    text = extract_pdf_text(file_path)
    if len(text.strip()) < 40:
        raise ValueError(
            "This PDF has little or no readable text (it may be a scan). "
            "Export a text PDF from TurboTax/H&R Block, or use tax return JSON."
        )

    payload = parse_1040_text_to_payload(text, label=label)
    if not _payload_has_fields(payload):
        payload = _llm_parse_1040_text(text, label=label)

    suggestions = extract_tax_return_suggestions(payload)
    suggestions = [
        replace(row, confidence="medium", document_type=DocumentType.PDF.value)
        for row in suggestions
    ]
    return payload, suggestions


class TaxReturnPdfExtractor:
    document_type = DocumentType.PDF

    def can_extract(self, path: str, payload: Any | None = None) -> bool:
        return Path(path).suffix.lower() == ".pdf"

    def extract(self, path: str, payload: Any | None = None) -> list[ExtractedFieldSuggestion]:
        try:
            _, suggestions = extract_tax_return_pdf(path)
            return suggestions
        except Exception as exc:
            return [
                ExtractedFieldSuggestion(
                    field_key=None,
                    person=None,
                    label="Form 1040 PDF",
                    raw_value="",
                    display_value="",
                    confidence="low",
                    source_quote=path,
                    document_type=DocumentType.PDF.value,
                    ok=False,
                    notes=str(exc),
                )
            ]

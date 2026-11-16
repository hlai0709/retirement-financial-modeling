"""Local PDF PII redaction for Retirement Planner financial documents.

Primary use cases (by design):
  • Bank / brokerage statements
  • Form W-2
  • Form 1040 and common tax schedules (1099, etc.)

Policy (maximum privacy for a personal finance app):
  REDACT: names, all addresses (employee, employer, mailing), phones, emails, DOB,
          SSN/ITIN (full + partial/masked), EIN, account/routing/member/control IDs.
  KEEP:   wages, tax amounts, balances, deposits, and other pure financial figures
          so the document remains useful for planning after redaction.

Requires pymupdf; Ollama is optional when skip_ai=True or unavailable.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Local-only AI: never cloud APIs. Ollama must be on this machine.
DEFAULT_MODEL = os.environ.get("REDACTOR_MODEL", "qwen3:14b")
# Hard-pinned localhost — ignores OLLAMA_HOST if pointed at a remote server.
OLLAMA_LOCAL_HOST = "http://127.0.0.1:11434"
# Keep AI prompts small for speed (full rules still run on every page)
_AI_TEXT_LIMIT = int(os.environ.get("REDACTOR_AI_TEXT_LIMIT", "3500"))
_AI_NUM_PREDICT = int(os.environ.get("REDACTOR_AI_NUM_PREDICT", "384"))
_AI_NUM_CTX = int(os.environ.get("REDACTOR_AI_NUM_CTX", "4096"))
# Cap how many phrase searches we run per page (longest / most specific first)
_MAX_PHRASES_PER_PAGE = int(os.environ.get("REDACTOR_MAX_PHRASES", "60"))
_AI_MAX_SOURCE_PAGES = int(os.environ.get("REDACTOR_AI_MAX_PAGES", "6"))

_ollama_client = None


def _local_ollama_client():
    """Return a cached Ollama client that only talks to loopback (local machine)."""
    global _ollama_client
    if _ollama_client is not None:
        return _ollama_client
    import ollama

    _ollama_client = ollama.Client(host=OLLAMA_LOCAL_HOST)
    return _ollama_client

# --- Identity / contact (always redact) ---
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_DASH = re.compile(r"\b\d{3}[-\u2013\u2014]\d{2}[-\u2013\u2014]\d{4}\b")
_SSN_SPACE = re.compile(r"\b\d{3}\s+\d{2}\s+\d{4}\b")
_SSN_DOT = re.compile(r"\b\d{3}\.\d{2}\.\d{4}\b")
_SSN_BARE = re.compile(r"(?<!\d)\d{9}(?!\d)")
# Any partial / masked SSN style (xxx-xx-1234, ***-**-1234, xxxxx1234, last-4 only forms)
_SSN_PARTIAL = re.compile(
    r"\b(?:xxx|XXX|x{3}|X{3}|\*{3}|#{3}|•{3})"
    r"[-\s.]?(?:xx|XX|x{2}|X{2}|\*{2}|#{2}|•{2})?"
    r"[-\s.]?\d{4}\b"
)
_SSN_MASKED_ALT = re.compile(
    r"\b(?:x{3,5}|X{3,5}|\*{3,5}|#{3,5})[-\s.]?\d{2,4}\b"
)
_SSN_LAST4_ANY = re.compile(
    r"(?:SSN|Social\s*Security(?:\s*No\.?|\s*Number)?|ITIN|Taxpayer\s*ID|"
    r"last\s*4|ending\s*in|ends\s*in|ID\s*#?)[:\s#]*"
    r"(?:[xX*#•]{2,5}[-\s.]?){0,2}(\d{4})\b",
    re.I,
)
# EIN / FEIN XX-XXXXXXX
_EIN = re.compile(r"\b\d{2}[-\u2013\u2014]\d{7}\b")
_EIN_BARE = re.compile(r"(?<!\d)\d{9}(?!\d)")  # same length as SSN; context decides
_PHONE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_DOB = re.compile(
    r"\b(?:DOB|Date of Birth|Birth\s*Date)[:\s]+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.I,
)
_DOB_BARE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b")
# Bank / account / routing (IDs, not wages)
_ACCOUNT = re.compile(r"\b\d{10,17}\b")
_ROUTING = re.compile(r"\b\d{9}\b")  # ABA routing; only with routing/account context
# US ZIP (addresses)
_ZIP = re.compile(r"\b\d{5}(?:[-\s]\d{4})?\b")
# Street address lines
_STREET = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z0-9][A-Za-z0-9.'’\-]+\s+){0,6}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|"
    r"Court|Ct|Way|Circle|Cir|Place|Pl|Parkway|Pkwy|Highway|Hwy)\.?\b",
    re.I,
)
_CITY_STATE_ZIP = re.compile(
    r"\b[A-Z][A-Za-z.'’\-\s]{1,40},\s*[A-Z]{2}\s+\d{5}(?:[-\s]\d{4})?\b"
)
# Driver license / passport-ish
_DL_LABELED = re.compile(
    r"(?:Driver'?s?\s*Licen[sc]e|DL|Passport|State\s*ID|ID\s*Number|Member\s*ID|"
    r"Account\s*Number|Routing\s*Number|Control\s*Number)[:\s#]*([A-Z0-9\-*]{4,20})",
    re.I,
)

_W2_MARKER = re.compile(r"form\s+w-?2|wage\s+and\s+tax\s+statement", re.I)
_TAX_FORM = re.compile(
    r"form\s+1040|form\s+w-?2|form\s+1099|1099-[A-Z]+|schedule\s+[a-z]|u\.?s\.?\s+individual\s+income\s+tax",
    re.I,
)
_BANK_STMT = re.compile(
    r"account\s+statement|bank\s+statement|brokerage\s+statement|"
    r"checking\s+account|savings\s+account|statement\s+period|"
    r"beginning\s+balance|ending\s+balance|account\s+summary|"
    r"chase|bank\s+of\s+america|wells\s+fargo|fidelity|schwab|vanguard|"
    r"capital\s+one|citi|us\s*bank|pnc\b",
    re.I,
)

# Document-class hints for progress / AI
_DOC_HINTS = (
    (_W2_MARKER, "W-2"),
    (_TAX_FORM, "tax return"),
    (_BANK_STMT, "bank/brokerage statement"),
)

# Labels whose following values are identity/address (not wages)
_LABEL_VALUE = re.compile(
    r"(?P<label>"
    r"Employee'?s?\s+social\s+security\s+number|"
    r"Social\s+security\s+number|"
    r"SSN|ITIN|TIN|"
    r"Your\s+first\s+name(?:\s+and\s+middle\s+initial)?|"
    r"First\s+name(?:\s+and\s+middle\s+initial)?|"
    r"Last\s+name|"
    r"Name(?:\(s\))?\s+shown\s+on(?:\s+Form)?|"
    r"Name(?:\(s\))?\s+shown\s+on\s+return|"
    r"Print\s+your\s+name|"
    r"Spouse'?s?\s+(?:first\s+name(?:\s+and\s+middle\s+initial)?|last\s+name|name|SSN|social\s+security\s+number)|"
    r"Employee'?s?\s+name(?:\s*,?\s*address,?\s*and\s+ZIP\s+code)?|"
    r"Employee'?s?\s+name|"
    r"Employee'?s?\s+address|"
    r"Employer'?s?\s+name(?:\s*,?\s*address,?\s*and\s+ZIP\s+code)?|"
    r"Employer'?s?\s+name|"
    r"Employer'?s?\s+address|"
    r"Employer\s+identification\s+number|"
    r"EIN|FEIN|"
    r"Control\s+number|"
    r"Account\s+number|"
    r"Routing\s+(?:transit\s+)?number|"
    r"Mailing\s+address|"
    r"Home\s+address|"
    r"Business\s+address|"
    r"Taxpayer|"
    r"Account\s+(?:holder|name|owner)|"
    r"Primary\s+account\s+holder|"
    r"Member\s+(?:name|number|ID)|"
    r"Customer\s+(?:name|number|ID)|"
    r"Card\s+number|"
    r"Account\s+ending\s+in"
    r")\s*[:\.]?\s*(?P<value>[^\n]{2,60})",
    re.I,
)
# "Taxpayer JOHN Q PUBLIC" / "Name: JANE DOE"
_INLINE_TAXPAYER = re.compile(
    r"\b(?:Taxpayer|Payor|Payee|Account\s+holder)\s+([A-Z][A-Za-z'’.\-]+(?:\s+[A-Z][A-Za-z'’.\-]+){1,4})\b"
)
# Schedules: "Name(s) shown on Form 1040" then next line is often "Henry D Lai"
_NAME_SHOWN_LABEL = re.compile(
    r"^Name(?:\(s\))?\s+shown\s+on\b.*$",
    re.I | re.M,
)
# Title Case person name: "Henry D Lai" / "Jane Q Public" (not ALL CAPS form headers)
_TITLE_CASE_PERSON = re.compile(
    r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z](?:\.|[a-z]{0,20})?){0,2}\s+[A-Z][a-z]{1,20})\b"
)
# Masked account on bank statements: ****1234 / XXXX-1234
_MASKED_ACCOUNT = re.compile(r"\b(?:X{4}|\*{4}|#{4}|x{4})[-\s]?\d{4}\b", re.I)
_NAME_LINE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z'’.\-]+(?:\s+[A-Z][A-Za-z'’.\-]+){1,5})\s*$"
)
_NAME_COMMA = re.compile(
    r"\b([A-Z][A-Za-z'’.\-]{1,30},\s*[A-Z][A-Za-z'’.\-]{1,30}(?:\s+[A-Z]\.?)?)\b"
)

# Financial figures to KEEP (do not treat as ID)
_MONEY = re.compile(
    r"\$?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\$?\s*-?\d+\.\d{2}"
)
_FINANCIAL_CONTEXT = re.compile(
    r"wage|tip|compensation|federal|income\s*tax|social\s*security\s*tax|"
    r"medicare|dependent|benefit|deferral|contribution|gross|net|"
    r"withheld|taxable|adjustment|interest|dividend|capital\s*gain|"
    r"balance|deposit|payment|amount|total|agi|tax\s*owed|refund|"
    r"box\s*\d|line\s*\d|schedule",
    re.I,
)

ProgressCb = Callable[[dict[str, Any]], None]


def format_eta(seconds: float | None) -> str:
    """Human-readable remaining time, e.g. '~2m 15s left'."""
    if seconds is None:
        return ""
    if seconds <= 0:
        return "almost done"
    s = int(round(seconds))
    if s < 5:
        return "a few seconds left"
    if s < 60:
        return f"~{s}s left"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"~{m}m {sec:02d}s left" if sec else f"~{m}m left"
    h, m = divmod(m, 60)
    return f"~{h}h {m}m left"


def estimate_eta_seconds(
    *,
    elapsed: float,
    percent: float,
    page: int = 0,
    pages: int = 0,
    completed_pages: int = 0,
) -> float | None:
    if elapsed < 0.4:
        return None
    candidates: list[float] = []
    if 1.0 < percent < 100.0:
        total = elapsed / (percent / 100.0)
        candidates.append(max(0.0, total - elapsed))
    if completed_pages >= 1 and pages > completed_pages:
        per_page = elapsed / completed_pages
        candidates.append(per_page * (pages - completed_pages))
    elif page >= 1 and pages > page and percent > 5:
        per_page = elapsed / max(page - 0.5, 0.5)
        candidates.append(per_page * (pages - page + 0.5))
    if not candidates:
        return None
    return max(candidates)


def capabilities() -> dict[str, Any]:
    pymupdf_ok = False
    ollama_pkg = False
    ollama_reachable = False
    model = DEFAULT_MODEL
    notes: list[str] = []

    try:
        import fitz  # noqa: F401

        pymupdf_ok = True
    except ImportError:
        notes.append("Install pymupdf: pip install pymupdf")

    try:
        import ollama  # noqa: F401

        ollama_pkg = True
    except ImportError:
        notes.append("Install ollama package for AI redaction: pip install ollama")

    if ollama_pkg:
        try:
            client = _local_ollama_client()
            client.list()
            ollama_reachable = True
        except Exception:
            notes.append(
                "Start local Ollama (app or `ollama serve` on 127.0.0.1:11434). "
                "Redaction AI never uses cloud APIs."
            )

    return {
        "ready": pymupdf_ok,
        "pymupdf": pymupdf_ok,
        "ollama_package": ollama_pkg,
        "ollama_reachable": ollama_reachable,
        "ai_available": pymupdf_ok and ollama_pkg and ollama_reachable,
        "model": model,
        "ollama_host": OLLAMA_LOCAL_HOST,
        "local_only": True,
        "notes": notes,
    }


def _emit(
    progress: ProgressCb | None,
    *,
    page: int = 0,
    pages: int = 0,
    percent: float = 0,
    phase: str = "",
    message: str = "",
    started_at: float | None = None,
    completed_pages: int = 0,
) -> None:
    if not progress:
        return
    pct = max(0.0, min(100.0, float(percent)))
    elapsed = (time.monotonic() - started_at) if started_at is not None else 0.0
    if pct >= 100:
        eta: float | None = 0.0
    else:
        eta = estimate_eta_seconds(
            elapsed=elapsed,
            percent=pct,
            page=page,
            pages=pages,
            completed_pages=completed_pages,
        )
    progress(
        {
            "page": page,
            "pages": pages,
            "percent": pct,
            "phase": phase,
            "message": message,
            "elapsed_seconds": round(elapsed, 1),
            "eta_seconds": None if eta is None else round(eta, 1),
            "eta_label": format_eta(eta),
        }
    )


def _looks_like_year(n: str) -> bool:
    try:
        y = int(n)
        return 1990 <= y <= 2035
    except ValueError:
        return False


def _normalize_numeric_token(raw: str) -> str:
    """Strip currency junk TurboTax leaves on amounts (trailing dots, spaces, $)."""
    s = raw.strip()
    s = s.strip("$").strip()
    # TurboTax often emits "227,540." or "0." with a trailing period
    s = re.sub(r"\.+$", "", s)
    s = s.strip()
    return s


def _is_financial_number(raw: str, *, surrounding: str = "") -> bool:
    """True if this looks like a wage/tax/balance figure to KEEP (not an ID)."""
    s = _normalize_numeric_token(raw)
    if not s:
        return False
    if "$" in raw:
        return True
    # 12,000.50 / 12,000 / 12000.50 / 227,540 (TurboTax style)
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", s):
        return True
    if re.fullmatch(r"-?\d+\.\d{1,2}", s):
        return True
    # Percentages
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{1,4})?\s*%", raw.strip()):
        return True
    # Tax year on form header
    if re.fullmatch(r"(?:19|20)\d{2}", s) and _looks_like_year(s):
        return True
    digits_only = re.sub(r"\D", "", s)
    # Bare 5–8 digit integers are almost always dollars on tax forms (88000, 259300)
    # Never treat as SSN/account.
    if re.fullmatch(r"-?\d{5,8}", s):
        return True
    # 1–4 digit amounts next to money context (line totals, small credits)
    if re.fullmatch(r"-?\d{1,4}", s) and _FINANCIAL_CONTEXT.search(surrounding):
        return True
    if re.fullmatch(r"-?\d{1,8}", s) and _FINANCIAL_CONTEXT.search(surrounding):
        if len(digits_only) == 9:
            return bool(
                re.search(
                    r"wage|tip|compensation|withheld|medicare|amount|total|balance",
                    surrounding,
                    re.I,
                )
            )
        return True
    return False


def _is_money_like_phrase(phrase: str) -> bool:
    """True if the whole phrase is (or is dominated by) a dollar amount — never redact."""
    s = phrase.strip()
    if _is_financial_number(s, surrounding=s):
        return True
    # " $    259,300.00 " / "227,540." / "0." / "( 1,500 )"
    if re.fullmatch(
        r"[\s$()-]*-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\.?[\s)]*",
        s,
    ):
        return True
    if re.fullmatch(r"[\s$()-]*-?\d+\.\d{1,2}\.?[\s)]*", s):
        return True
    if re.fullmatch(r"[\s$()-]*-?\d{1,8}\.?[\s)]*", s):
        # Bare integers / TurboTax "0." / "12." — tax form amounts, not SSNs
        return True
    return False


def _expand_id_variants(raw: str) -> set[str]:
    """Search variants for SSN/EIN/partial IDs (spacing / masks differ in PDFs)."""
    # Never expand money into fake ID search terms
    if _is_money_like_phrase(raw) or _is_financial_number(raw, surrounding=raw):
        return set()
    digits = re.sub(r"\D", "", raw)
    out = {raw, raw.strip()}
    if len(digits) == 9:
        a, b, c = digits[:3], digits[3:5], digits[5:]
        out.update(
            {
                digits,
                f"{a}-{b}-{c}",
                f"{a} {b} {c}",
                f"{a}.{b}.{c}",
                f"{a}  {b}  {c}",
                f"{a}-{b}{c}",  # rare
            }
        )
        # EIN style XX-XXXXXXX
        out.add(f"{digits[:2]}-{digits[2:]}")
    elif len(digits) == 4:
        # Last-4 only as masked forms — bare "1234" matches too many tax line amounts
        out.update(
            {
                f"XXX-XX-{digits}",
                f"***-**-{digits}",
                f"xxx-xx-{digits}",
                f"XXXXX{digits}",
                f"***{digits}",
                f"ending in {digits}",
                f"ends in {digits}",
            }
        )
        # Include bare last-4 only with explicit mask context already in raw
        if re.search(r"[xX*#•]", raw):
            out.add(digits)
    return {v for v in out if v and len(v) >= 3 and not _is_money_like_phrase(v)}


def _context_window(text: str, start: int, end: int, pad: int = 48) -> str:
    return text[max(0, start - pad) : min(len(text), end + pad)]


def _collect_regex_pii(text: str) -> set[str]:
    """Maximum identity redaction; keep pure financial amounts."""
    found: set[str] = set()

    for pattern in (
        _EMAIL,
        _SSN_DASH,
        _SSN_SPACE,
        _SSN_DOT,
        _SSN_PARTIAL,
        _SSN_MASKED_ALT,
        _MASKED_ACCOUNT,
        _EIN,
        _PHONE,
        _DOB,
        _DOB_BARE,
        _ACCOUNT,
        _STREET,
        _CITY_STATE_ZIP,
    ):
        for match in pattern.finditer(text):
            raw = match.group(0)
            ctx = _context_window(text, match.start(), match.end())
            if _is_money_like_phrase(raw) or _is_financial_number(raw, surrounding=ctx):
                continue
            found.add(raw)
            if re.search(r"\d", raw):
                found |= _expand_id_variants(raw)

    # Bare 9-digit: SSN/EIN only when near identity context (not random tax math)
    for match in _SSN_BARE.finditer(text):
        n = match.group(0)
        ctx = _context_window(text, match.start(), match.end())
        if _is_money_like_phrase(n) or _is_financial_number(n, surrounding=ctx):
            continue
        ctx_l = ctx.lower()
        if not re.search(
            r"ssn|social\s*security|itin|taxpayer|ein|fein|employee|employer|"
            r"identification|routing|account\s*number|member\s*id",
            ctx_l,
        ):
            continue
        found.add(n)
        found |= _expand_id_variants(n)

    for match in _SSN_LAST4_ANY.finditer(text):
        # Keep full "SSN ending in 1234" phrase; do NOT add bare 1234 (wipes tax amounts)
        found.add(match.group(0))
        if match.lastindex and match.group(1):
            last4 = match.group(1)
            found |= _expand_id_variants(f"xxx-xx-{last4}")

    for match in _DL_LABELED.finditer(text):
        found.add(match.group(0))
        if match.lastindex and match.group(1):
            val = match.group(1)
            if not _is_money_like_phrase(val):
                found.add(val)

    # Routing number only with banking context (avoid wiping random 9-digit wages)
    for match in _ROUTING.finditer(text):
        ctx = _context_window(text, match.start(), match.end()).lower()
        if "routing" in ctx or "aba" in ctx or "transit" in ctx:
            found.add(match.group(0))

    # ZIP codes that sit on address-like lines (not tax years)
    for match in _ZIP.finditer(text):
        z = match.group(0)
        if _looks_like_year(z[:4] if len(z) >= 4 else z):
            continue
        ctx = _context_window(text, match.start(), match.end())
        if re.search(
            r"street|st\b|ave|road|rd\b|drive|lane|blvd|address|city|zip|mail|"
            r"[A-Z]{2}\s+" + re.escape(z),
            ctx,
            re.I,
        ):
            found.add(z)

    for match in _NAME_COMMA.finditer(text):
        found.add(match.group(1).strip())

    for match in _INLINE_TAXPAYER.finditer(text):
        name = match.group(1).strip()
        if not _is_blocked_redaction_phrase(name):
            found.add(name)
            found |= _name_tokens(name, from_name_field=True)

    # "Name(s) shown on Form 1040 / return" → following line is the taxpayer name
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if not re.match(r"^Name(?:\(s\))?\s+shown\s+on\b", line, re.I):
            continue
        # value may be on same line after label, or next 1–2 lines
        same = re.sub(r"^Name(?:\(s\))?\s+shown\s+on\b[^:]*[:\s]*", "", line, flags=re.I).strip()
        candidates = []
        if same and len(same) > 2 and not re.match(r"Form\b|return\b|1040", same, re.I):
            candidates.append(same)
        for j in (1, 2):
            if i + j < len(lines):
                candidates.append(lines[i + j])
        for cand in candidates:
            if _is_blocked_redaction_phrase(cand):
                continue
            if re.match(r"^(Your social|SSN|OMB|Department|Part\b|Form\b|\d)", cand, re.I):
                continue
            # Prefer person-shaped lines
            if _TITLE_CASE_PERSON.search(cand) or re.match(
                r"^[A-Z][a-zA-Z'’.\-]+(?:\s+[A-Z][a-zA-Z'’.\-]*){1,4}$", cand
            ):
                found.add(cand)
                found |= _name_tokens(cand, from_name_field=True)
                break

    # Title-case full names anywhere on tax/bank docs (catches schedule footers)
    if _TAX_FORM.search(text) or _W2_MARKER.search(text) or _BANK_STMT.search(text):
        for match in _TITLE_CASE_PERSON.finditer(text):
            name = match.group(1).strip()
            # Skip form titles that look title-case
            if re.search(
                r"Form|Schedule|Department|Treasury|Internal|Revenue|Social Security|"
                r"United States|Additional Medicare|Capital Gain|Adjusted Gross|"
                r"Filing Status|Digital Asset|Standard Deduction|Tax Return|"
                r"Wage and Tax|Interest Income|Ordinary Dividend",
                name,
                re.I,
            ):
                continue
            if _is_blocked_redaction_phrase(name):
                continue
            # Require at least first + last (3+ tokens if middle initial)
            parts = name.split()
            if len(parts) < 2:
                continue
            # Last token should look like a surname (not "Tax", "Form", …)
            if parts[-1].lower() in _NEVER_REDACT_WORDS:
                continue
            found.add(name)
            found |= _name_tokens(name, from_name_field=True)

    return found


def _collect_label_values(text: str) -> set[str]:
    """Pull identity/address values after labels (employee AND employer)."""
    found: set[str] = set()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for match in _LABEL_VALUE.finditer(text):
        label = match.group("label").lower()
        value = (match.group("value") or "").strip()
        value = re.split(r"\s{2,}|Form\s+W|OMB\s+No", value, maxsplit=1)[0].strip(" :\t")
        if len(value) < 2 or len(value) > 60:
            continue
        if _is_financial_number(value, surrounding=label + " " + value):
            continue
        if _is_blocked_redaction_phrase(value):
            continue
        found.add(value)
        if any(k in label for k in ("social", "ssn", "itin", "tin", "ein", "fein", "control", "account", "routing")):
            found |= _expand_id_variants(value)
        if "name" in label or label.strip() in {"taxpayer"}:
            # from_name_field allows short surnames like Lai
            found |= _name_tokens(value, from_name_field=True)

    # Label-only lines → following block (name + multi-line address)
    label_only = re.compile(
        r"^(?:[a-f]\s+)?(?:"
        r"Employee'?s?\s+(?:social\s+security\s+number|name(?:.*)?|address.*)|"
        r"Employer'?s?\s+(?:name(?:.*)?|address.*|identification\s+number)|"
        r"Social\s+security\s+number|SSN|ITIN|EIN|FEIN|"
        r"Control\s+number|Account\s+number|Routing\s+number|"
        r"Account\s+(?:holder|name|owner)|Primary\s+account\s+holder|"
        r"Member\s+(?:name|number)|Customer\s+(?:name|number)|"
        r"Mailing\s+address|Home\s+address|Business\s+address|Address"
        r")\s*:?\s*$",
        re.I,
    )
    # Stop before wage/tax boxes — those amounts stay
    stop_next = re.compile(
        r"^(?:[a-f]\s+)?(?:"
        r"Form|Copy|OMB|Department|Void|"
        r"\d{1,2}[a-z]?\s+(?:Wages|Federal|Social|Medicare|Dependent|Statutory|"
        r"Retirement|Third-party|Nonqualified|Other|State|Local)|"
        r"Wage and Tax|Employee's social|Employer's identification"
        r")\b",
        re.I,
    )
    for i, line in enumerate(lines):
        if not label_only.match(line):
            continue
        is_addressish = bool(re.search(r"name|address", line, re.I))
        take = 5 if is_addressish else 2
        for j in range(1, take + 1):
            if i + j >= len(lines):
                break
            nxt = lines[i + j]
            if len(nxt) < 2 or len(nxt) > 100:
                continue
            if label_only.match(nxt) or stop_next.match(nxt):
                break
            if _is_financial_number(nxt, surrounding=line + " " + nxt):
                continue
            found.add(nxt)
            if re.search(r"social|ssn|itin|ein|fein|control|account|routing", line, re.I):
                found |= _expand_id_variants(nxt)
            if re.search(r"\bname\b", line, re.I):
                found |= _name_tokens(nxt, from_name_field=True)
            # Address lines under name/address labels (employee or employer)
            if is_addressish:
                found.add(nxt)
                for m in _ZIP.finditer(nxt):
                    found.add(m.group(0))

    # Tax forms: person/employer name-shaped lines near header
    if _TAX_FORM.search(text) or _W2_MARKER.search(text):
        for line in lines[:50]:
            m = _NAME_LINE.match(line)
            if not m:
                continue
            name = m.group("name")
            low = name.lower()
            if low in {"wage and tax statement", "form w-2", "void"}:
                continue
            if any(
                w in low
                for w in (
                    "form",
                    "copy",
                    "department",
                    "internal",
                    "revenue",
                    "wage",
                    "federal",
                    "medicare",
                    "social security tax",
                )
            ):
                continue
            found.add(name)
            found |= _name_tokens(name)

    return found


# Never redact these alone — shreds 1040 instructions ("you", "and", "or", months…).
_NEVER_REDACT_WORDS: frozenset[str] = frozenset(
    {
        # Pronouns / function words
        "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by", "as",
        "is", "are", "was", "were", "be", "been", "being", "am",
        "i", "me", "my", "we", "our", "you", "your", "yours", "he", "she", "it", "its",
        "they", "them", "their", "this", "that", "these", "those", "who", "whom", "which",
        "what", "when", "where", "why", "how", "if", "then", "than", "so", "not", "no",
        "yes", "do", "does", "did", "done", "can", "could", "will", "would", "should",
        "may", "might", "must", "shall", "with", "without", "from", "into", "onto",
        "over", "under", "about", "after", "before", "between", "through", "during",
        "each", "all", "any", "some", "more", "most", "other", "such", "only", "own",
        "same", "both", "few", "many", "much", "also", "just", "still", "even", "back",
        "up", "down", "out", "off", "again", "further", "once", "here", "there",
        # Common form / mail instruction words (not PII)
        "form", "forms", "page", "pages", "line", "lines", "box", "schedule", "see",
        "attach", "attached", "copy", "copies", "print", "printed", "file", "filed",
        "filing", "return", "returns", "tax", "taxes", "federal", "state", "local",
        "income", "wage", "wages", "tips", "interest", "dividend", "dividends",
        "capital", "gain", "gains", "loss", "losses", "refund", "payment", "payments",
        "pay", "paid", "check", "checks", "mail", "mailed", "mailing", "sign", "signature",
        "date", "dates", "deadline", "postmarked", "postage", "envelope", "instructions",
        "instruction", "important", "note", "notes", "remember", "keep", "records",
        "record", "program", "select", "center", "save", "pdf", "information", "info",
        "more", "less", "total", "amount", "amounts", "balance", "due", "owed", "owe",
        "shows", "include", "includes", "including", "together", "separately",
        "single", "married", "jointly", "separately", "widow", "widower", "dependent",
        "dependents", "spouse", "self", "employer", "employee", "standard", "itemized",
        "deduction", "deductions", "credit", "credits", "withheld", "withholding",
        "social", "security", "medicare", "adjusted", "gross", "taxable", "exempt",
        "presidential", "campaign", "digital", "assets", "crypto", "someone", "claim",
        "qualifying", "child", "children", "relative", "head", "household",
        "internal", "revenue", "service", "treasury", "department", "omb", "irs",
        "turbo", "turbotax", "intuit", "printout", "official", "complete", "completed",
        "steps", "step", "required", "finish", "finished", "prefer", "e-file", "efile",
        "go", "end", "these", "below", "above", "next", "year", "years", "january",
        "february", "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul",
        "aug", "sep", "sept", "oct", "nov", "dec", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday", "number", "numbers", "write",
        "written", "payable", "money", "order", "front", "first", "second", "third",
        "fourth", "review", "state", "different", "please", "correct", "forget",
        "don't", "dont", "closing", "closed", "before", "after", "one", "two", "three",
        "four", "five", "six", "seven", "eight", "nine", "ten", "use", "used", "using",
        "make", "made", "making", "need", "needed", "must", "should", "city", "town",
        "zip", "code", "codes", "apt", "suite", "unit", "street", "st", "ave", "road",
        "rd", "drive", "dr", "lane", "ln", "blvd", "way", "court", "ct", "place", "pl",
        "inc", "llc", "ltd", "corp", "co", "company", "corporation", "bank", "trust",
        "void", "none", "n/a", "na", "unknown", "other", "specify", "enter", "amount",
        "see", "instructions", "worksheet", "continue", "stop", "caution", "warning",
        "yes", "no", "true", "false", "full", "part", "year", "tax", "return",
        # 1040 field labels / common form nouns (must not be blacked page-wide)
        "name", "names", "initial", "initials", "middle", "last", "first", "spouse",
        "spouses", "dependent", "dependents", "address", "addresses", "home", "foreign",
        "country", "province", "postal", "status", "joint", "jointly", "separately",
        "single", "married", "household", "qualifying", "surviving", "presidential",
        "election", "campaign", "fund", "checking", "box", "boxes", "check", "checked",
        "only", "one", "had", "income", "enter", "above", "below", "here", "space",
        "spaces", "write", "staple", "irs", "use", "do", "not", "beginning", "ending",
        "other", "tax", "year", "see", "separate", "filed", "pursuant", "section",
        "combat", "zone", "deceased", "post", "office", "town", "city", "state",
        "zip", "code", "apt", "no", "number", "street", "box", "main", "half",
        "during", "receive", "reward", "award", "property", "services", "sell",
        "exchange", "dispose", "asset", "assets", "digital", "financial", "interest",
        "yes", "no", "more", "than", "four", "lived", "with", "student", "disabled",
        "permanently", "totally", "full-time", "fulltime", "identification", "shown",
        "attach", "sequence", "part", "add", "subtract", "multiply", "line", "lines",
        "amount", "zero", "less", "total", "go", "to", "www", "http", "https",
        "calendar", "year", "due", "voucher", "detach", "mail", "with", "your",
        "payment", "estimated", "money", "order", "payable", "united", "states",
        "treasury", "recharacterize", "contribution", "basis", "ira", "blank",
        "report", "depreciation", "amortization", "keep", "for", "records", "method",
        "prior", "current", "cost", "land", "bus", "special", "allowance", "code",
        "adjustments", "preferences", "alternative", "minimum", "description",
        "personal", "property", "business", "profit", "loss", "rental", "royalties",
        "partnerships", "corporations", "trusts", "farm", "gambling", "cancellation",
        "debt", "foreign", "earned", "exclusion", "alaska", "permanent", "fund",
        "jury", "duty", "prizes", "awards", "activity", "engaged", "stock", "options",
        "olympic", "paralympic", "medals", "prize", "money", "alimony", "received",
        "divorce", "separation", "agreement", "unemployment", "compensation",
        "repaid", "overpayment", "operating", "net", "refunds", "credits", "offsets",
        "local", "item", "items", "sold", "error", "personal", "note", "remaining",
        "amounts", "reported", "elsewhere", "nature", "transaction", "transactions",
    }
)

# Full phrases that are IRS/TurboTax boilerplate — never redact
_NEVER_REDACT_PHRASES: frozenset[str] = frozenset(
    {
        "form 1040",
        "form w-2",
        "form w2",
        "social security number",
        "social security",
        "adjusted gross income",
        "taxable income",
        "standard deduction",
        "filing status",
        "married filing jointly",
        "married filing separately",
        "head of household",
        "qualifying surviving spouse",
        "internal revenue service",
        "file by mail instructions",
        "federal tax return",
        "estimated tax",
        "digital assets",
        "presidential election campaign",
    }
)


def _looks_like_street_address(phrase: str) -> bool:
    """Street lines must stay redactable even if tokens include 'main'/'street'."""
    s = phrase.strip()
    if _STREET.search(s):
        return True
    if re.search(
        r"\b\d{1,6}\s+[A-Za-z].*\b(?:street|st|avenue|ave|road|rd|blvd|drive|dr|lane|ln|way|court|ct|place|pl)\b",
        s,
        re.I,
    ):
        return True
    # "123 MAIN STREET" style
    if re.match(r"^\d{1,6}\s+[A-Za-z]", s) and len(s.split()) >= 2:
        return True
    return False


def _is_blocked_redaction_phrase(phrase: str) -> bool:
    """True if phrase must never be redacted (form text / English, not PII)."""
    s = re.sub(r"\s+", " ", phrase.strip())
    if not s:
        return True
    # Real addresses / IDs are never "blocked" as form text
    if _looks_like_street_address(s):
        return False
    if re.search(r"\d{3}[-.\s]?\d{2}[-.\s]?\d{4}", s):  # SSN-like
        return False
    if re.fullmatch(r"\d{2}-\d{7}", s):  # EIN
        return False

    low = s.lower().strip(".,;:\"'()[]")
    if low in _NEVER_REDACT_PHRASES:
        return True
    if low in _NEVER_REDACT_WORDS:
        return True
    # Pure punctuation / form lines
    if re.fullmatch(r"[\W_]+", s):
        return True
    # Form line refs: "Line 1", "Box 12", "Schedule A"
    if re.fullmatch(r"(?:line|box|schedule|form|part)\s*[a-z0-9.\-]+", low):
        return True
    # Single common English word (even Title Case)
    if re.fullmatch(r"[A-Za-z'’\-]+", s) and low in _NEVER_REDACT_WORDS:
        return True
    # 1–2 letter tokens (not short surnames — those need from_name_field path)
    if re.fullmatch(r"[A-Za-z]{1,2}", s):
        return True
    # Multi-word phrase where EVERY token is a stopword → boilerplate
    tokens = [t.strip(".,;:\"'()").lower() for t in s.split() if t.strip(".,;:\"'()")]
    if tokens and all(t in _NEVER_REDACT_WORDS or t.isdigit() or len(t) <= 2 for t in tokens):
        digits = re.sub(r"\D", "", s)
        # "123 MAIN STREET" already returned False above; other digit+stopword
        if len(digits) < 4 and not _looks_like_street_address(s):
            return True
    # Tax years alone
    if re.fullmatch(r"(?:19|20)\d{2}", s):
        return True
    # Sentence / instruction fragments (over-capture from labels or AI)
    words = [t.strip(".,;:\"'()").lower() for t in s.split() if t.strip(".,;:\"'()")]
    if len(words) >= 6:
        return True
    if len(s) > 48 and not re.search(r"\d{3}[-.\s]?\d{2}[-.\s]?\d{4}", s):
        return True
    stop_hits = sum(1 for w in words if w in _NEVER_REDACT_WORDS)
    if len(words) >= 4 and stop_hits >= 2 and len(re.sub(r"\D", "", s)) < 4:
        if not _looks_like_street_address(s):
            return True
    return False


def _is_plausible_person_name_token(token: str, *, from_name_field: bool = False) -> bool:
    """
    Single-word name piece safe to redact without shredding form text.

    Allows short surnames like \"Lai\" (3 letters) when they look like names
    (Title Case / ALL CAPS) and are not English/form stopwords.
    """
    core = token.strip(".,;:'\" ").replace(".", "")
    if not core.isalpha():
        return False
    low = core.lower()
    if low in _NEVER_REDACT_WORDS:
        return False
    if len(core) >= 4:
        return True
    if len(core) == 3:
        # Short surnames (Lai, Cho, …): only proper-name casing
        if core.isupper() or (core[0].isupper() and core[1:].islower()):
            return True
        # Explicit last/first-name field values may be all-lowercase OCR
        return from_name_field
    if len(core) == 2 and from_name_field:
        # Rare 2-letter surnames (Ng, …) only from labeled name fields
        return core.isupper() or core[0].isupper()
    return False


def _name_tokens(name: str, *, from_name_field: bool = False) -> set[str]:
    """Parts of a person/org name worth redacting if full-phrase search fails."""
    if _is_blocked_redaction_phrase(name):
        return set()
    parts = re.split(r"[\s,]+", name.strip())
    out: set[str] = set()
    for p in parts:
        p = p.strip(".,")
        if not _is_plausible_person_name_token(p, from_name_field=from_name_field):
            continue
        out.add(p)
        out.add(p.upper())
        out.add(p.title())
    if len(parts) >= 2:
        joined2 = " ".join(parts[:2])
        if len(joined2) >= 6 and not _is_blocked_redaction_phrase(joined2):
            out.add(joined2)
        joined_last = " ".join(parts[-2:])
        if len(joined_last) >= 6 and not _is_blocked_redaction_phrase(joined_last):
            out.add(joined_last)
    return out


def _detect_doc_kind(text: str) -> str:
    for pattern, label in _DOC_HINTS:
        if pattern.search(text):
            return label
    return "financial document"


def _get_redaction_list(text: str, custom_rules: str = "", *, model: str = DEFAULT_MODEL) -> list[str]:
    """Call local Ollama only (127.0.0.1) — never OpenAI/xAI/cloud APIs."""
    kind = _detect_doc_kind(text)
    if _W2_MARKER.search(text):
        form_hint = (
            "Document type: IRS Form W-2. "
            "Redact employee AND employer names, ALL addresses (both), SSN/ITIN every format "
            "(including partial/masked/last-4), EIN, control numbers, phones, emails. "
            "Do NOT redact box wage/tip/tax-withheld/Medicare dollar amounts. "
        )
    elif _TAX_FORM.search(text):
        form_hint = (
            "Document type: US tax return (1040/1099/schedules). "
            "Redact taxpayer names, SSNs/ITINs (full + partial), addresses, phones, "
            "dependent names/SSNs, preparer IDs if present. "
            "Do NOT redact AGI, tax, refund, income line dollar amounts. "
        )
    elif _BANK_STMT.search(text):
        form_hint = (
            "Document type: bank or brokerage statement. "
            "Redact account holder names, full/partial account numbers, routing numbers, "
            "card numbers, mailing addresses, phones, emails, member/customer IDs. "
            "Do NOT redact transaction amounts, balances, or dates of transactions. "
        )
    else:
        form_hint = (
            f"Document type: {kind} (personal finance). "
            "Prefer redacting identity/account IDs; keep dollar amounts and balances. "
        )
    prompt = (
        "You are the redaction assistant inside a retirement / personal finance planner. "
        "Users mainly redact bank statements, W-2s, and 1040 tax forms before sharing or archiving. "
        "Return ONLY a JSON array of strings copied EXACTLY from the document. "
        f"{form_hint}"
        "ALWAYS include when present: person names, employer/payor/bank names when they identify the user, "
        "all street/city/state/ZIP addresses, phones, emails, DOB, "
        "SSN/ITIN (full, spaced, dashed, partial xxx-xx-1234, last 4), "
        "EIN/FEIN, account numbers, routing numbers, control/member/customer IDs. "
        "NEVER include pure financial amounts (wages, tips, tax withheld, deposits, balances, $ figures). "
        "NEVER include ordinary English or form words (you, and, or, more, information, payment, "
        "check, return, form, tax, income, filing, schedule, instructions, april, etc.). "
        "NEVER include IRS form labels or questions alone. "
        "Do NOT invent values. Only return specific identity values found in the text. "
        f"Extra rules: {custom_rules or 'none'}.\n\n"
        f"DOCUMENT TEXT:\n{text[:_AI_TEXT_LIMIT]}\n\n"
        'Example: ["JANE Q PUBLIC","ACME CORP","123 MAIN ST","****1234","123-45-6789"]'
    )

    client = _local_ollama_client()
    # Defense in depth: refuse if client is not loopback
    base = str(getattr(getattr(client, "_client", None), "base_url", "") or "")
    if base and "127.0.0.1" not in base and "localhost" not in base:
        raise RuntimeError(
            f"Refusing non-local Ollama host {base!r}. Redaction AI is local-only."
        )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "options": {
            "temperature": 0,
            "num_predict": _AI_NUM_PREDICT,
            "num_ctx": _AI_NUM_CTX,
        },
    }
    try:
        response = client.chat(**kwargs, think=False)
    except TypeError:
        response = client.chat(**kwargs)

    content = str(response["message"]["content"]).strip()
    if "</think>" in content:
        content = content.split("</think>", 1)[-1].strip()
    phrases: list[str] = []
    try:
        start = content.find("[")
        end = content.rfind("]") + 1
        if start != -1 and end > 0:
            parsed = json.loads(content[start:end])
            if isinstance(parsed, list):
                phrases = [str(item).strip() for item in parsed if item and len(str(item).strip()) > 1]
    except (json.JSONDecodeError, TypeError, ValueError):
        phrases = []

    text_lower = text.lower()
    verified: set[str] = set()
    for phrase in phrases:
        if _is_financial_number(phrase, surrounding=phrase):
            continue
        if phrase.lower() in text_lower or phrase in text:
            verified.add(phrase)
            idx = text_lower.find(phrase.lower())
            if idx >= 0:
                verified.add(text[idx : idx + len(phrase)])
            if re.search(r"\d", phrase):
                verified |= _expand_id_variants(phrase)
            if any(c.isalpha() for c in phrase):
                verified |= _name_tokens(phrase)
        else:
            for variant in _expand_id_variants(phrase):
                if variant.lower() in text_lower or variant in text:
                    verified.add(variant)

    return list(verified)


def _find_phrase_rects(
    page: Any,
    phrase: str,
    *,
    whole_word_only: bool = False,
    words_cache: list | None = None,
) -> list[Any]:
    """
    Locate phrase on page: search_for first, then word-bbox stitching.
    whole_word_only: skip substring search_for (avoids redacting 'in' inside 'income').
    """
    import fitz

    rects: list[Any] = []
    collapsed = re.sub(r"\s+", " ", phrase.strip())
    if not collapsed:
        return []

    # Prefer exact phrase first; only try case variants if needed (major speedup)
    variants: list[str] = [collapsed]
    if collapsed != collapsed.upper():
        variants.append(collapsed.upper())
    if collapsed != phrase.strip() and phrase.strip() not in variants:
        variants.insert(0, phrase.strip())

    if not whole_word_only:
        for v in variants:
            try:
                hits = page.search_for(v)
            except Exception:
                hits = []
            if hits:
                return list(hits)

    # Word-level match (slower) — only for short alphabetic tokens that need exact words
    tokens = [t for t in re.split(r"\s+", collapsed) if t]
    if not tokens:
        return []
    # Skip expensive word scan for long multi-word strings already missed by search_for
    if len(tokens) > 4 and not whole_word_only:
        return []
    try:
        words = words_cache if words_cache is not None else page.get_text("words")
    except Exception:
        return []
    if not words:
        return []

    norm = [str(w[4]) for w in words]
    target = [t.lower().strip(".,;:") for t in tokens]
    n = len(target)
    for i in range(len(norm) - n + 1):
        window = [norm[i + j].lower().strip(".,;:") for j in range(n)]
        if window == target:
            x0 = min(words[i + j][0] for j in range(n))
            y0 = min(words[i + j][1] for j in range(n))
            x1 = max(words[i + j][2] for j in range(n))
            y1 = max(words[i + j][3] for j in range(n))
            rects.append(fitz.Rect(x0, y0, x1, y1))
    return rects


def _apply_phrases(page: Any, phrases: set[str]) -> int:
    """Apply redaction annotations; return number of hit regions."""
    hits = 0
    # Longest / most specific first; cap count for multi-page speed
    ordered = sorted(
        (p.strip() for p in phrases if p and len(p.strip()) > 1),
        key=len,
        reverse=True,
    )
    # De-dupe case-insensitively while keeping first (longest) form
    seen_lower: set[str] = set()
    unique: list[str] = []
    for p in ordered:
        key = p.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        unique.append(p)
    ordered = unique[:_MAX_PHRASES_PER_PAGE]

    words_cache: list | None = None
    need_words = False

    for p in ordered:
        if _is_blocked_redaction_phrase(p):
            continue
        if _is_money_like_phrase(p) or _is_financial_number(p, surrounding=p):
            continue
        if re.fullmatch(r"[A-Za-z'’.\-]+", p) and p.lower() in _NEVER_REDACT_WORDS:
            continue
        if re.fullmatch(r"[\d,.$()\s\-]+\.?", p) and len(re.sub(r"\D", "", p)) < 9:
            if not re.search(r"[xX*#•]", p):
                continue
        alpha_only = bool(re.fullmatch(r"[A-Za-z'’.\-]+", p))
        whole = alpha_only or len(p) <= 6 or (p.isdigit() and len(p) <= 4)
        if whole and words_cache is None and not need_words:
            need_words = True
            try:
                words_cache = page.get_text("words")
            except Exception:
                words_cache = []
        for rect in _find_phrase_rects(
            page, p, whole_word_only=whole, words_cache=words_cache
        ):
            try:
                page.add_redact_annot(rect, fill=(0, 0, 0))
                hits += 1
            except Exception:
                continue
    return hits


def _page_has_identity_cues(text: str) -> bool:
    return bool(
        re.search(
            r"social\s*security|first\s+name|last\s+name|home\s+address|"
            r"SSN|EIN|account\s+(?:number|holder)|routing|"
            r"employee'?s?\s+name|employer'?s?\s+name|taxpayer",
            text,
            re.I,
        )
    )


def _build_ai_source_text(page_texts: list[str]) -> str:
    """Pick a few identity-rich pages for a single AI call (not every page)."""
    rich: list[str] = []
    for i, t in enumerate(page_texts):
        if not t.strip():
            continue
        if i < 2 or _page_has_identity_cues(t):
            rich.append(t[:2200])
        if len(rich) >= _AI_MAX_SOURCE_PAGES:
            break
    if not rich and page_texts:
        rich = [page_texts[0][:_AI_TEXT_LIMIT]]
    return "\n\n---PAGE---\n\n".join(rich)[:_AI_TEXT_LIMIT]


def _phrases_for_page(text: str, custom_rules: str, *, use_ai: bool, model: str) -> set[str]:
    found = _collect_regex_pii(text)
    found |= _collect_label_values(text)
    expanded: set[str] = set()
    for item in found:
        if _is_blocked_redaction_phrase(item):
            continue
        expanded.add(item)
        digits = re.sub(r"\D", "", item)
        if re.search(r"\d", item) and len(digits) in {4, 9, 10, 11, 12, 13, 14, 15, 16, 17}:
            for v in _expand_id_variants(item):
                if not _is_blocked_redaction_phrase(v):
                    expanded.add(v)
        # Split multi-word names; single-token values (e.g. "Lai") keep as-is if allowed
        words = item.split()
        if any(c.isalpha() for c in item) and 1 <= len(words) <= 6 and len(item) <= 60:
            if len(words) == 1:
                if _is_plausible_person_name_token(item, from_name_field=True):
                    expanded.add(item)
                    expanded.add(item.upper())
                    expanded.add(item.title())
            else:
                expanded |= _name_tokens(item, from_name_field=True)
    if use_ai and text.strip():
        for phrase in _get_redaction_list(text, custom_rules, model=model):
            if _is_blocked_redaction_phrase(phrase):
                continue
            expanded.add(phrase)
            if re.search(r"\d", phrase):
                for v in _expand_id_variants(phrase):
                    if not _is_blocked_redaction_phrase(v):
                        expanded.add(v)
            words = phrase.split()
            if any(c.isalpha() for c in phrase) and 1 <= len(words) <= 6:
                if len(words) == 1:
                    if _is_plausible_person_name_token(phrase, from_name_field=True):
                        expanded.add(phrase)
                        expanded.add(phrase.upper())
                        expanded.add(phrase.title())
                else:
                    expanded |= _name_tokens(phrase, from_name_field=True)
    cleaned: set[str] = set()
    for p in expanded:
        if not p or len(p.strip()) <= 1:
            continue
        if _is_blocked_redaction_phrase(p):
            continue
        if _is_money_like_phrase(p) or _is_financial_number(p, surrounding=p):
            continue
        # Bare 1–8 digit tokens (and TurboTax "227,540.") are money/line noise — never redact
        if re.fullmatch(r"[\s$()-]*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\.?[\s)]*", p.strip()):
            continue
        if re.fullmatch(r"-?\d{1,8}\.?", p.strip()):
            continue
        cleaned.add(p)
    return cleaned


def redact_pdf(
    input_path: str | Path,
    output_path: str | Path,
    custom_rules: str = "",
    *,
    skip_ai: bool = False,
    model: str = DEFAULT_MODEL,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """
    Redact PII from input PDF into output_path.

    progress callback receives dicts: page, pages, percent, phase, message, eta_*.
    """
    try:
        import fitz  # noqa: F401
    except ImportError:
        return {
            "ok": False,
            "message": "PDF redaction requires pymupdf. Run: pip install pymupdf",
        }

    input_path = Path(input_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    if not input_path.is_file():
        return {"ok": False, "message": f"PDF not found: {input_path}"}

    use_ai = not skip_ai
    if use_ai:
        caps = capabilities()
        if not caps["ai_available"]:
            # Do not silently downgrade — caller asked for AI
            return {
                "ok": False,
                "message": (
                    "AI redaction was requested but Ollama is not available. "
                    + "; ".join(caps.get("notes") or ["Start Ollama and pull the model."])
                ),
                "used_ai": False,
            }

    started_at = time.monotonic()
    _emit(
        progress,
        percent=2,
        phase="open",
        message="Opening PDF…",
        started_at=started_at,
    )

    try:
        import fitz

        doc = fitz.open(str(input_path))
    except Exception as exc:
        return {"ok": False, "message": f"Could not open PDF: {exc}"}

    pages = len(doc)
    total_hits = 0
    try:
        # --- Pass 1: extract text once per page ---
        _emit(
            progress,
            percent=5,
            phase="read",
            message=f"Reading {pages} page(s)…",
            started_at=started_at,
        )
        page_texts: list[str] = []
        for page in doc:
            text = page.get_text("text") or ""
            page_texts.append(text)

        # --- Pass 2: optional single AI call (not once per page) ---
        ai_phrases: set[str] = set()
        if use_ai:
            _emit(
                progress,
                percent=12,
                phase="ai",
                message="Local AI (one pass on identity pages)…",
                started_at=started_at,
            )
            try:
                source = _build_ai_source_text(page_texts)
                for phrase in _get_redaction_list(source, custom_rules, model=model):
                    if _is_blocked_redaction_phrase(phrase) or _is_money_like_phrase(phrase):
                        continue
                    ai_phrases.add(phrase)
                    if re.search(r"\d", phrase):
                        ai_phrases |= {
                            v
                            for v in _expand_id_variants(phrase)
                            if not _is_blocked_redaction_phrase(v)
                            and not _is_money_like_phrase(v)
                        }
                    words = phrase.split()
                    if any(c.isalpha() for c in phrase) and 1 <= len(words) <= 6:
                        ai_phrases |= _name_tokens(phrase, from_name_field=True)
            except Exception as exc:
                doc.close()
                return {
                    "ok": False,
                    "message": f"AI redaction failed: {exc}. Try Fast mode.",
                    "used_ai": False,
                }

        # --- Pass 3: per-page rules + apply ---
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            completed = page_idx
            # Reserve 15–90% of bar for pages after AI
            base = 15 + (page_idx / max(pages, 1)) * 75
            page_span = 75 / max(pages, 1)
            text = page_texts[page_idx]

            # Skip empty pages quickly
            if not text.strip():
                _emit(
                    progress,
                    page=page_num,
                    pages=pages,
                    percent=base + page_span,
                    phase="skip",
                    message=f"Page {page_num}/{pages}: empty — skip",
                    started_at=started_at,
                    completed_pages=completed,
                )
                continue

            doc_kind = _detect_doc_kind(text)
            _emit(
                progress,
                page=page_num,
                pages=pages,
                percent=base + page_span * 0.2,
                phase="scan",
                message=f"Page {page_num}/{pages}: {doc_kind} rules…",
                started_at=started_at,
                completed_pages=completed,
            )

            # Fast path: rules only on each page; merge global AI phrases
            phrases = _phrases_for_page(text, custom_rules, use_ai=False, model=model)
            if ai_phrases:
                # Only keep AI hits that appear on this page (cheaper apply)
                text_l = text.lower()
                for p in ai_phrases:
                    if p.lower() in text_l or p in text:
                        phrases.add(p)

            _emit(
                progress,
                page=page_num,
                pages=pages,
                percent=base + page_span * 0.7,
                phase="apply",
                message=f"Page {page_num}/{pages}: applying redactions…",
                started_at=started_at,
                completed_pages=completed,
            )
            total_hits += _apply_phrases(page, phrases)
            page.apply_redactions()

        _emit(
            progress,
            page=pages,
            pages=pages,
            percent=92,
            phase="save",
            message="Saving PDF…",
            started_at=started_at,
            completed_pages=pages,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Faster save: skip full garbage collect on large returns
        doc.save(str(output_path), garbage=2, deflate=True)
        doc.close()
    except Exception as exc:
        try:
            doc.close()
        except Exception:
            pass
        return {"ok": False, "message": f"Redaction failed: {exc}"}

    elapsed = time.monotonic() - started_at
    mode = "AI (1-pass) + rules" if use_ai else "rules (fast)"
    return {
        "ok": True,
        "message": (
            f"Redacted {pages} page(s) in {elapsed:.1f}s ({mode}); ~{total_hits} region(s).\n"
            "Tuned for bank statements, W-2s, and 1040s: "
            "names, addresses, phones, SSN/ITIN/EIN/account IDs removed; "
            "wages, taxes, and balances kept.\n"
            "Review every page before sharing."
        ),
        "pages": pages,
        "used_ai": use_ai,
        "regions": total_hits,
        "elapsed_seconds": round(elapsed, 2),
        "output_path": str(output_path),
        "input_path": str(input_path),
    }

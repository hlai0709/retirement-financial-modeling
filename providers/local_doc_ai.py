"""Local Ollama extraction of planning fields from document text (AI Mode).

Uses the same loopback Ollama host as PDF redaction — never cloud APIs for personal PDFs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.types import DocumentType
from app.core.lazy_mode.manifest import document_by_id
from app.core.presentation.field_editor import resolve_field
from config.field_values import format_field_value
from providers.pdf_redactor import DEFAULT_MODEL, OLLAMA_LOCAL_HOST, _local_ollama_client

_AI_NUM_PREDICT = 512
_AI_NUM_CTX = 4096
_TEXT_LIMIT = 8000


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _suggestion(
    field_key: str,
    *,
    person: str | None,
    value: Any,
    source_quote: str,
    confidence: str,
    document_type: str,
) -> ExtractedFieldSuggestion | None:
    if value is None or value == "":
        return None
    field = resolve_field(field_key, person=person)
    if field is None:
        return None
    field_person = person if field.scope == "person" else None
    parsed: Any = value
    if field.field_type == "money":
        parsed = _money(value)
        if parsed is None:
            return None
    elif field.field_type in {"rate_pct", "rate"}:
        try:
            parsed = float(value)
            if parsed > 1.0:
                parsed = parsed / 100.0
        except (TypeError, ValueError):
            return None
    elif field.field_type in {"int", "age", "year"}:
        try:
            parsed = int(float(str(value).replace(",", "").replace("$", "")))
        except (TypeError, ValueError):
            return None
        if field.field_type == "year" and (parsed < 1900 or parsed > 2100):
            # Reject balances / loan IDs mistaken for calendar years
            return None
        if field.key == "mortgage_payoff_year" and (parsed < 2020 or parsed > 2080):
            return None
    elif field.field_type == "bool":
        if isinstance(value, bool):
            parsed = value
        else:
            parsed = str(value).strip().lower() in {"1", "true", "yes", "y"}
    raw = format_field_value(field, parsed)
    display = raw
    if field.field_type == "money" and isinstance(parsed, (int, float)):
        display = f"${parsed:,.0f}"
    elif field.field_type in {"rate_pct", "rate"} and isinstance(parsed, (int, float)):
        display = f"{float(parsed) * 100:g}%"
    return ExtractedFieldSuggestion(
        field_key=field_key,
        person=field_person,
        label=field.label,
        raw_value=raw,
        display_value=display,
        confidence=confidence if confidence in {"high", "medium", "low"} else "medium",
        source_quote=(source_quote or "")[:240],
        document_type=document_type,
    )


def extract_fields_with_local_ai(
    text: str,
    *,
    document_id: str,
    person: str | None = "self",
    model: str = DEFAULT_MODEL,
) -> list[ExtractedFieldSuggestion]:
    """Ask local Ollama for JSON field extractions for this document type."""
    if not text or not text.strip():
        return []

    doc = document_by_id(document_id) or {}
    field_keys = list(doc.get("fields") or [])
    if not field_keys:
        field_keys = [
            "annual_earned_income",
            "state_of_residence",
            "pre_retire_tax_rate",
            "itemized_salt_annual",
            "itemized_mortgage_interest",
            "ss_amount",
            "pension_amount",
            "rental_income_start",
        ]

    label = str(doc.get("label") or document_id)
    schema_hint = ", ".join(field_keys)
    # Strict extraction only — never planning advice, defaults, or "what you should use".
    prompt = (
        "You are a document OCR/extraction helper. Your only job is to READ numbers and labels "
        "that already appear in the DOCUMENT TEXT and map them to planning field keys.\n"
        "You do NOT give financial advice, recommendations, estimates, or defaults.\n"
        "You do NOT invent values, annualize unless the document itself states an annual figure "
        "or a clear YTD total, or guess what a field 'should' be.\n\n"
        f"Document type: {label} (id={document_id}).\n"
        "Return ONLY a JSON array of objects. Each object must have:\n"
        '  field_key (string), person ("self"|"wife"|null), value (number or string),\n'
        '  confidence ("high"|"medium"|"low"),\n'
        "  source_quote (REQUIRED: short exact substring copied from the document that contains the value).\n"
        f"Only use these field_key values when present: {schema_hint}.\n"
        "Rules:\n"
        "- Extract only facts printed on this document. If a value is not clearly on the page, omit it.\n"
        "- Every row MUST include source_quote taken from the text (not a paraphrase of advice).\n"
        "- Money as plain numbers (no $). Rates as decimals (0.22) or percents (22) — either OK.\n"
        "- Form 1040 line wages → annual_earned_income when that line is present.\n"
        "- W-2 box 1 wages → annual_earned_income when present.\n"
        "- Pay stub: use stated annual salary or YTD gross / stated deferral lines only.\n"
        "- person is self unless the text clearly names the spouse only.\n"
        "- Do not fill retirement_age, life_expectancy, or other judgment fields.\n"
        "- Do not compute 'recommended' contribution rates or tax rates unless the document states them.\n"
        "- mortgage_payoff_year MUST be a calendar year of maturity/payoff (e.g. 2045), NEVER "
        "principal balance, payoff quote dollars, or monthly payment.\n"
        "- mortgage_annual is yearly housing payment (monthly P&I or total payment × 12), not interest-only "
        "and not year-to-date totals unless labeled as annual.\n"
        "- property_tax_annual is the annual property tax due (sum of installments if needed), not SALT cap.\n"
        "- itemized_salt_annual is Schedule A total state/local taxes (may be capped for deduction).\n"
        "- Omit any field you cannot support with a quote from the text.\n\n"
        f"DOCUMENT TEXT:\n{text[:_TEXT_LIMIT]}\n"
    )

    client = _local_ollama_client()
    base = str(getattr(getattr(client, "_client", None), "base_url", "") or "")
    if base and "127.0.0.1" not in base and "localhost" not in base:
        raise RuntimeError(f"Refusing non-local Ollama host {base!r}")

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

    rows = _parse_json_array(content)
    suggestions: list[ExtractedFieldSuggestion] = []
    doc_type = DocumentType.PDF.value
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("field_key") or "").strip()
        if not key or key not in field_keys:
            # Allow known keys even if not listed on doc (flexible AI)
            if not resolve_field(key, person=row.get("person")):
                continue
        pers = row.get("person")
        if pers in {"", "null", "None"}:
            pers = None
        if pers not in {None, "self", "wife"}:
            pers = person
        if pers is None and document_id in {"w2", "pay_stub", "ssa_1099"}:
            pers = person or "self"
        conf = str(row.get("confidence") or "medium").lower()
        quote = str(row.get("source_quote") or "").strip()
        # Reject advice-like or empty quotes — local AI must ground values in the document
        if not quote or _looks_like_guidance(quote):
            continue
        if not _quote_supported_by_text(quote, text):
            # Soften: allow if a distinctive fragment appears in the document
            fragment = re.sub(r"\s+", " ", quote)[:40].strip()
            if len(fragment) < 6 or fragment.lower() not in re.sub(r"\s+", " ", text).lower():
                continue
        sug = _suggestion(
            key,
            person=pers,
            value=row.get("value"),
            source_quote=f"{quote} · extracted from document",
            confidence=conf,
            document_type=doc_type,
        )
        if sug:
            suggestions.append(sug)
    return suggestions


def _looks_like_guidance(quote: str) -> bool:
    lower = quote.lower()
    banned = (
        "should",
        "recommend",
        "suggest",
        "consider",
        "estimate",
        "typical",
        "average",
        "default",
        "i think",
        "you may want",
        "guidance",
        "advice",
    )
    return any(token in lower for token in banned)


def _quote_supported_by_text(quote: str, text: str) -> bool:
    q = re.sub(r"\s+", " ", quote).strip().lower()
    body = re.sub(r"\s+", " ", text).lower()
    if len(q) < 4:
        return False
    if q in body:
        return True
    # Allow quote with minor punctuation differences
    digits = re.findall(r"\d[\d,]*\.?\d*", quote)
    return bool(digits) and all(d.replace(",", "") in body.replace(",", "") for d in digits[:3])


def _parse_json_array(content: str) -> list[Any]:
    try:
        start = content.find("[")
        end = content.rfind("]") + 1
        if start != -1 and end > 0:
            parsed = json.loads(content[start:end])
            if isinstance(parsed, list):
                return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    # Sometimes models return a single object
    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > 0:
            obj = json.loads(content[start:end])
            if isinstance(obj, dict):
                return [obj]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def local_ai_available() -> bool:
    try:
        client = _local_ollama_client()
        client.list()
        return True
    except Exception:
        return False


def host_label() -> str:
    return f"{OLLAMA_LOCAL_HOST} · {DEFAULT_MODEL}"


def local_chat(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    num_predict: int = 2048,
    num_ctx: int = 8192,
    temperature: float = 0.0,
) -> str:
    """Chat with loopback Ollama only — for jobs that should stay offline."""
    client = _local_ollama_client()
    base = str(getattr(getattr(client, "_client", None), "base_url", "") or "")
    if base and "127.0.0.1" not in base and "localhost" not in base:
        raise RuntimeError(f"Refusing non-local Ollama host {base!r}")
    prompt = f"{system.strip()}\n\n{user.strip()}"
    kwargs: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }
    try:
        response = client.chat(**kwargs, think=False)
    except TypeError:
        response = client.chat(**kwargs)
    content = str(response["message"]["content"]).strip()
    if "</think>" in content:
        content = content.split("</think>", 1)[-1].strip()
    return content

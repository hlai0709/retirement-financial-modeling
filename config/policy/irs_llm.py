"""LLM-assisted extraction of IRS newsroom policy fields when regex parsing fails."""

from __future__ import annotations

import os
from typing import Any

from providers.assumption_research import _extract_json_payload
from providers.llm.factory import get_llm_provider
from providers.llm.models_catalog import resolve_model_choice
from security.llm_keychain import get_api_key
from settings import load_llm_settings

_SYSTEM_PROMPT = (
    "You extract U.S. federal tax inflation-adjustment figures from IRS newsroom text. "
    "Use ONLY numbers present in the provided excerpt. Respond with ONLY valid JSON — "
    "no markdown fences, no commentary."
)

_REQUIRED_SCALAR_KEYS = (
    "mfj_standard_deduction",
    "single_standard_deduction",
    "hoh_standard_deduction",
)
_REQUIRED_AMT_KEYS = (
    "exemption_mfj",
    "exemption_single",
    "phaseout_start_mfj",
    "phaseout_start_single",
)
_BRACKET_RATES = (0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37)


def llm_fallback_enabled() -> bool:
    manifest_flag = True
    try:
        from config.policy.live import load_manifest

        manifest = load_manifest()
        if manifest.get("llm_fallback") is False:
            manifest_flag = False
    except Exception:
        pass
    if os.getenv("POLICY_LLM_FALLBACK", "").strip().lower() in {"0", "false", "no"}:
        return False
    if os.getenv("POLICY_LLM_FALLBACK", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if not manifest_flag:
        return False
    settings = load_llm_settings()
    if not settings.configured:
        return False
    return bool(get_api_key(settings.provider))


def _newsroom_excerpt(text: str, tax_year: int, *, max_chars: int = 14_000) -> str:
    anchor = text.lower().find(f"tax year {tax_year}")
    if anchor < 0:
        anchor = text.lower().find("standard deduction")
    if anchor < 0:
        anchor = 0
    start = max(0, anchor - 1_500)
    return text[start : start + max_chars]


def _build_user_prompt(*, tax_year: int, excerpt: str, newsroom_url: str | None) -> str:
    url_line = newsroom_url or f"IRS newsroom tax year {tax_year}"
    return (
        f"Tax year: {tax_year}\n"
        f"Source page: {url_line}\n\n"
        "Extract federal ordinary income tax brackets (single and MFJ), standard deductions "
        "(single/MFS, MFJ, HOH), and alternative minimum tax exemption/phaseout amounts.\n\n"
        "Return JSON exactly matching this schema:\n"
        "{\n"
        '  "source": "IRS Rev. Proc. YYYY-NN or IRS newsroom TY####",\n'
        '  "scalars": {\n'
        '    "mfj_standard_deduction": <int>,\n'
        '    "single_standard_deduction": <int>,\n'
        '    "hoh_standard_deduction": <int>\n'
        "  },\n"
        '  "brackets": {\n'
        '    "single": [[<10% ceiling>, 0.10], [<12% ceiling>, 0.12], ..., [<35% ceiling>, 0.35], ["inf", 0.37]],\n'
        '    "mfj": [[<10% ceiling>, 0.10], [<12% ceiling>, 0.12], ..., [<35% ceiling>, 0.35], ["inf", 0.37]]\n'
        "  },\n"
        '  "amt": {\n'
        '    "exemption_mfj": <int>,\n'
        '    "exemption_single": <int>,\n'
        '    "exemption_mfs": <int optional>,\n'
        '    "phaseout_start_mfj": <int>,\n'
        '    "phaseout_start_single": <int>\n'
        "  }\n"
        "}\n"
        "Bracket ceilings are the top of each rate band (ascending). "
        "The final bracket must be [\"inf\", 0.37].\n\n"
        f"IRS excerpt:\n{excerpt}"
    )


def _coerce_brackets(raw: Any, label: str) -> list[list[float | str]]:
    if not isinstance(raw, list) or len(raw) < 7:
        raise ValueError(f"LLM brackets.{label} must include 10%–37% tiers")

    brackets: list[list[float | str]] = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError(f"Invalid bracket entry in {label}: {entry!r}")
        ceiling, rate = entry
        if ceiling in ("inf", float("inf")):
            brackets.append(["inf", float(rate)])
        else:
            brackets.append([int(float(ceiling)), float(rate)])

    rates = [float(rate) for _, rate in brackets]
    if rates[-1] != 0.37 or brackets[-1][0] != "inf":
        raise ValueError(f"LLM brackets.{label} must end with [\"inf\", 0.37]")
    if rates[:-1] != list(_BRACKET_RATES[:-1]):
        raise ValueError(f"LLM brackets.{label} rates must be {_BRACKET_RATES}")
    return brackets


def _validate_llm_payload(payload: dict[str, Any], *, tax_year: int) -> dict[str, Any]:
    scalars = payload.get("scalars")
    if not isinstance(scalars, dict):
        raise ValueError("LLM response missing scalars object")
    for key in _REQUIRED_SCALAR_KEYS:
        if key not in scalars:
            raise ValueError(f"LLM response missing scalars.{key}")
        scalars[key] = int(float(scalars[key]))

    brackets = payload.get("brackets")
    if not isinstance(brackets, dict):
        raise ValueError("LLM response missing brackets object")
    parsed_brackets = {
        "single": _coerce_brackets(brackets.get("single"), "single"),
        "mfj": _coerce_brackets(brackets.get("mfj"), "mfj"),
    }

    amt = payload.get("amt")
    if not isinstance(amt, dict):
        raise ValueError("LLM response missing amt object")
    for key in _REQUIRED_AMT_KEYS:
        if key not in amt:
            raise ValueError(f"LLM response missing amt.{key}")
        amt[key] = int(float(amt[key]))
    if "exemption_mfs" in amt:
        amt["exemption_mfs"] = int(float(amt["exemption_mfs"]))
    else:
        amt["exemption_mfs"] = round(amt["exemption_mfj"] / 2)
    amt["phaseout_start_mfs"] = amt["phaseout_start_single"]

    source = str(payload.get("source") or f"IRS newsroom TY{tax_year} (LLM)")
    return {
        "source": source,
        "scalars": scalars,
        "brackets": parsed_brackets,
        "amt": amt,
        "fetch_method": "irs_newsroom_llm",
    }


def parse_newsroom_policy_via_llm(
    text: str,
    *,
    tax_year: int,
    newsroom_url: str | None = None,
) -> dict[str, Any]:
    """Extract policy tables from newsroom text using the configured LLM provider."""
    settings = load_llm_settings()
    if not settings.configured:
        raise RuntimeError("LLM fallback requires a configured provider in LLM Settings")

    api_key = get_api_key(settings.provider)
    if not api_key:
        raise RuntimeError(f"No API key stored for {settings.provider}")

    model = resolve_model_choice(settings.provider, settings.model, api_key=api_key)
    llm = get_llm_provider(settings.provider, api_key)
    completion = llm.complete(
        system=_SYSTEM_PROMPT,
        user=_build_user_prompt(
            tax_year=tax_year,
            excerpt=_newsroom_excerpt(text, tax_year),
            newsroom_url=newsroom_url,
        ),
        model=model,
    )
    payload = _extract_json_payload(completion.content)
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    result = _validate_llm_payload(payload, tax_year=tax_year)
    result["llm_provider"] = completion.provider
    result["llm_model"] = completion.model
    return result
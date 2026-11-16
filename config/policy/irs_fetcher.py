"""Fetch and parse IRS tax-year inflation adjustments from newsroom HTML."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.policy.live import (
    OFFICIAL_DIR,
    load_manifest,
    load_official_policy,
    official_policy_path,
)

USER_AGENT = "RetirementFinancialModeling/1.0 (local planning tool)"
MARGINAL_RATE_ORDER = (0.35, 0.32, 0.24, 0.22, 0.12)


def _fetch_url(url: str, *, timeout: float = 45.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _money_values(text: str) -> list[int]:
    return [int(value.replace(",", "")) for value in re.findall(r"\$([\d,]+)", text)]


def _normalize_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text)


def manifest_entry_for_year(tax_year: int) -> dict[str, Any] | None:
    manifest = load_manifest()
    for entry in manifest.get("years", []):
        if int(entry.get("tax_year", 0)) == tax_year:
            return entry
    return None


def newsroom_url_for_year(tax_year: int) -> str | None:
    entry = manifest_entry_for_year(tax_year)
    if entry and entry.get("newsroom_url"):
        return str(entry["newsroom_url"])
    return (
        f"https://www.irs.gov/newsroom/"
        f"irs-releases-tax-inflation-adjustments-for-tax-year-{tax_year}"
    )


def rev_proc_pdf_url_for_year(tax_year: int) -> str | None:
    entry = manifest_entry_for_year(tax_year)
    if entry and entry.get("irs_url"):
        return str(entry["irs_url"])
    return None


def fetch_newsroom_html(tax_year: int, *, url: str | None = None) -> tuple[str, str]:
    """Return (html, source_url)."""
    source = url or newsroom_url_for_year(tax_year)
    if not source:
        raise ValueError(f"No newsroom URL configured for tax year {tax_year}")
    return _fetch_url(source), source


def _parse_standard_deductions(text: str) -> dict[str, int]:
    values: dict[str, int] = {}

    mfj_patterns = (
        r"married couples filing jointly, the standard deduction (?:rises|increases) to \$([\d,]+)",
        r"standard deduction increases to \$([\d,]+) for married couples filing jointly",
        r"Married Filing Jointly.*?\$([\d,]+)",
    )
    single_patterns = (
        r"single taxpayers and married individuals filing separately(?: for tax year \d{4})?, the standard deduction (?:rises|is) to \$([\d,]+)",
        r"single taxpayers and married individuals filing separately, the standard deduction (?:rises|is) to \$([\d,]+)",
        r"standard deduction (?:rises|is) to \$([\d,]+) for single taxpayers",
        r"Single; Married Filing Separately.*?\$([\d,]+)",
    )
    hoh_patterns = (
        r"heads of households, the standard deduction (?:will be|is) \$([\d,]+)",
        r"heads of households, the standard deduction (?:rises to|will be) \$([\d,]+)",
        r"Heads of Households.*?\$([\d,]+)",
    )

    for pattern in mfj_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            values["mfj_standard_deduction"] = int(match.group(1).replace(",", ""))
            break
    for pattern in single_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            values["single_standard_deduction"] = int(match.group(1).replace(",", ""))
            break
    for pattern in hoh_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            values["hoh_standard_deduction"] = int(match.group(1).replace(",", ""))
            break

    if len(values) < 3:
        raise ValueError("Could not parse all standard deduction amounts from IRS page")
    return values


def _parse_rate_over_blocks(text: str) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    single_blocks: list[tuple[float, int]] = []
    mfj_blocks: list[tuple[float, int]] = []
    pattern = re.compile(
        r"(35|32|24|22|12)% for incomes over \$([\d,]+)\s*"
        r"\(\$([\d,]+) for married couples filing jointly\)\.?",
        flags=re.IGNORECASE,
    )
    for rate_str, single_raw, mfj_raw in pattern.findall(text):
        rate = float(rate_str) / 100.0
        single_blocks.append((rate, int(single_raw.replace(",", ""))))
        mfj_blocks.append((rate, int(mfj_raw.replace(",", ""))))

    if len(single_blocks) < 5:
        raise ValueError("Could not parse marginal rate thresholds from IRS page")

    ten_single = re.search(
        r"10% for incomes(?: of single individuals)?(?: with incomes)? of \$([\d,]+) or less "
        r"\(\$([\d,]+) for married couples filing jointly\)",
        text,
        flags=re.IGNORECASE,
    )
    if not ten_single:
        ten_single = re.search(
            r"10% for incomes \$([\d,]+) or less \(\$([\d,]+) or less for married couples filing jointly\)",
            text,
            flags=re.IGNORECASE,
        )
    if not ten_single:
        raise ValueError("Could not parse 10% bracket ceiling from IRS page")

    ten_ceiling_single = int(ten_single.group(1).replace(",", ""))
    ten_ceiling_mfj = int(ten_single.group(2).replace(",", ""))
    return (
        [(0.10, ten_ceiling_single), *sorted(single_blocks, key=lambda item: item[1])],
        [(0.10, ten_ceiling_mfj), *sorted(mfj_blocks, key=lambda item: item[1])],
    )


def _brackets_from_blocks(blocks: list[tuple[float, int]]) -> list[list[float | str]]:
    """Turn IRS newsroom thresholds into (ceiling, rate) rows.

    Newsroom copy lists "12% for incomes over $X", where X is the floor of
    that rate and the ceiling of the prior rate. The 10% figure is already
    that band's ceiling. The 37% floor must be included so the 35% band has
    a finite width.
    """
    ten_ceilings = [floor for rate, floor in blocks if abs(rate - 0.10) < 1e-9]
    starts = sorted(
        ((floor, rate) for rate, floor in blocks if abs(rate - 0.10) >= 1e-9),
        key=lambda item: (item[0], item[1]),
    )
    if not ten_ceilings or not starts:
        raise ValueError("ordinary brackets missing the 10% ceiling or a higher rate")
    brackets: list[list[float | str]] = [[float(ten_ceilings[0]), 0.10]]
    for index, (_floor, rate) in enumerate(starts):
        if index + 1 < len(starts):
            ceiling: float | str = float(starts[index + 1][0])
        else:
            ceiling = "inf"
        brackets.append([ceiling, rate])
    if brackets[-1][1] != 0.37 or brackets[-1][0] != "inf":
        raise ValueError("ordinary brackets must end at the 37% rate with no ceiling")
    return brackets


def _parse_top_bracket_floors(text: str) -> tuple[int, int]:
    match = re.search(
        r"37% for individual single taxpayers with incomes greater than \$([\d,]+) "
        r"\(\$([\d,]+) for married couples filing jointly\)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Could not parse top 37% bracket floor from IRS page")
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2).replace(",", "")),
    )


def _parse_amt(text: str) -> dict[str, int]:
    amt: dict[str, int] = {}

    # 2026+ OBBB format: single sentence with MFJ values in a parenthetical.
    obbb_block = re.search(
        r"exemption amount for unmarried individuals is \$([\d,]+) "
        r"and begins to phase out at \$([\d,]+) "
        r"\(\$([\d,]+) for married couples filing jointly "
        r"for whom the exemption begins to phase out at \$([\d,]+)\)",
        text,
        flags=re.IGNORECASE,
    )
    if obbb_block:
        amt["exemption_single"] = int(obbb_block.group(1).replace(",", ""))
        amt["phaseout_start_single"] = int(obbb_block.group(2).replace(",", ""))
        amt["exemption_mfj"] = int(obbb_block.group(3).replace(",", ""))
        amt["phaseout_start_mfj"] = int(obbb_block.group(4).replace(",", ""))
    else:
        joint_block = re.search(
            r"married couples filing jointly, the exemption amount(?: increases)? to \$([\d,]+) "
            r"(?:\([^)]+\) )?and begins to phase out at \$([\d,]+)",
            text,
            flags=re.IGNORECASE,
        )
        if joint_block:
            amt["exemption_mfj"] = int(joint_block.group(1).replace(",", ""))
            amt["phaseout_start_mfj"] = int(joint_block.group(2).replace(",", ""))

        single_block = re.search(
            r"unmarried individuals(?: increases)? to \$([\d,]+)"
            r"(?: \(\$([\d,]+) for married individuals filing separately\))? "
            r"and begins to phase out at \$([\d,]+)",
            text,
            flags=re.IGNORECASE,
        )
        if single_block:
            amt["exemption_single"] = int(single_block.group(1).replace(",", ""))
            if single_block.group(2):
                amt["exemption_mfs"] = int(single_block.group(2).replace(",", ""))
            amt["phaseout_start_single"] = int(single_block.group(3).replace(",", ""))

    if "exemption_mfj" not in amt or "exemption_single" not in amt:
        raise ValueError("Could not parse AMT exemption amounts from IRS page")
    if "exemption_mfs" not in amt:
        amt["exemption_mfs"] = round(amt["exemption_mfj"] / 2)
    if "phaseout_start_mfj" not in amt:
        raise ValueError("Could not parse AMT MFJ phaseout from IRS page")
    if "phaseout_start_single" not in amt:
        raise ValueError("Could not parse AMT single phaseout from IRS page")
    amt["phaseout_start_mfs"] = amt["phaseout_start_single"]
    return amt


def _scale_brackets(
    brackets: list[list[float | str]],
    ratio: float,
) -> list[list[float | str]]:
    scaled: list[list[float | str]] = []
    for ceiling, rate in brackets:
        if ceiling in ("inf", float("inf")):
            scaled.append(["inf", rate])
        else:
            scaled.append([round(float(ceiling) * ratio), rate])
    return scaled


def _scale_scalars(scalars: dict[str, Any], ratio: float, *, skip: set[str]) -> dict[str, Any]:
    scaled: dict[str, Any] = {}
    for key, value in scalars.items():
        if key in skip or not isinstance(value, (int, float)):
            scaled[key] = value
        else:
            scaled[key] = round(float(value) * ratio, 2) if isinstance(value, float) else round(float(value) * ratio)
    return scaled


def _merge_with_prior_year(
    *,
    tax_year: int,
    parsed: dict[str, Any],
    prior: dict[str, Any],
    fetch_method: str = "irs_newsroom_html",
) -> dict[str, Any]:
    ratio = parsed["scalars"]["single_standard_deduction"] / float(
        prior["scalars"]["single_standard_deduction"]
    )
    fetched_scalar_keys = set(parsed["scalars"])
    scalars = _scale_scalars(prior["scalars"], ratio, skip=fetched_scalar_keys)
    scalars.update(parsed["scalars"])

    brackets = {
        "single": parsed["brackets"]["single"],
        "mfj": parsed["brackets"]["mfj"],
        "hoh": _scale_brackets(prior["brackets"]["hoh"], ratio),
        "single_ltcg": _scale_brackets(prior["brackets"]["single_ltcg"], ratio),
        "mfj_ltcg": _scale_brackets(prior["brackets"]["mfj_ltcg"], ratio),
        "hoh_ltcg": _scale_brackets(prior["brackets"]["hoh_ltcg"], ratio),
    }

    amt = dict(prior.get("amt", {}))
    amt.update(parsed.get("amt", {}))
    if "bracket_26_single" in amt:
        amt["bracket_26_mfj"] = round(amt["bracket_26_single"] * 2)

    rev_proc = rev_proc_pdf_url_for_year(tax_year) or ""
    source = parsed.get("source") or f"IRS newsroom TY{tax_year}"
    if rev_proc:
        source = f"{source} ({rev_proc.split('/')[-1].replace('.pdf', '')})"

    fetch_notes = (
        "Federal ordinary brackets and standard deductions parsed from IRS newsroom. "
        "HOH brackets, LTCG tiers, SS thresholds, Medicare, and FPL scaled from prior tax year."
    )
    if fetch_method == "irs_newsroom_llm":
        fetch_notes = (
            "Federal ordinary brackets, standard deductions, and AMT extracted via LLM "
            "from IRS newsroom HTML. HOH brackets, LTCG tiers, SS thresholds, Medicare, "
            "and FPL scaled from prior tax year."
        )

    merged = {
        "tax_year": tax_year,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "fetch_method": fetch_method,
        "fetch_notes": fetch_notes,
        "scalars": scalars,
        "brackets": brackets,
        "amt": amt,
    }
    if parsed.get("llm_provider"):
        merged["llm_provider"] = parsed["llm_provider"]
    if parsed.get("llm_model"):
        merged["llm_model"] = parsed["llm_model"]
    return merged


def _parse_newsroom_policy_regex(text: str, *, tax_year: int) -> dict[str, Any]:
    scalars = _parse_standard_deductions(text)
    single_blocks, mfj_blocks = _parse_rate_over_blocks(text)
    top_single, top_mfj = _parse_top_bracket_floors(text)
    single_blocks.append((0.37, top_single))
    mfj_blocks.append((0.37, top_mfj))
    amt = _parse_amt(text)

    rev_match = re.search(r"Revenue Procedure (\d{4}-\d+)", text, flags=re.IGNORECASE)
    source = f"IRS Rev. Proc. {rev_match.group(1)}" if rev_match else f"IRS newsroom TY{tax_year}"

    return {
        "source": source,
        "scalars": scalars,
        "brackets": {
            "single": _brackets_from_blocks(single_blocks),
            "mfj": _brackets_from_blocks(mfj_blocks),
        },
        "amt": amt,
        "fetch_method": "irs_newsroom_html",
    }


def parse_newsroom_policy(
    html: str,
    *,
    tax_year: int,
    llm_fallback: bool = True,
    newsroom_url: str | None = None,
) -> dict[str, Any]:
    text = _normalize_html(html)
    try:
        return _parse_newsroom_policy_regex(text, tax_year=tax_year)
    except ValueError:
        if not llm_fallback:
            raise
        from config.policy.irs_llm import llm_fallback_enabled, parse_newsroom_policy_via_llm

        if not llm_fallback_enabled():
            raise
        return parse_newsroom_policy_via_llm(
            text,
            tax_year=tax_year,
            newsroom_url=newsroom_url,
        )


def build_policy_payload(
    *,
    tax_year: int,
    html: str,
    llm_fallback: bool = True,
    newsroom_url: str | None = None,
) -> dict[str, Any]:
    parsed = parse_newsroom_policy(
        html,
        tax_year=tax_year,
        llm_fallback=llm_fallback,
        newsroom_url=newsroom_url,
    )
    prior_year = tax_year - 1
    prior_path = official_policy_path(prior_year)
    if not prior_path.exists():
        raise FileNotFoundError(
            f"Prior-year policy {prior_year} required to scale HOH/LTCG/SS/Medicare fields: {prior_path}"
        )
    prior = load_official_policy(prior_year)
    return _merge_with_prior_year(
        tax_year=tax_year,
        parsed=parsed,
        prior=prior,
        fetch_method=str(parsed.get("fetch_method", "irs_newsroom_html")),
    )


def fetch_and_build_policy(
    tax_year: int,
    *,
    url: str | None = None,
    llm_fallback: bool = True,
) -> dict[str, Any]:
    html, source_url = fetch_newsroom_html(tax_year, url=url)
    payload = build_policy_payload(
        tax_year=tax_year,
        html=html,
        llm_fallback=llm_fallback,
        newsroom_url=source_url,
    )
    payload["newsroom_url"] = source_url
    return payload


def write_official_policy(payload: dict[str, Any]) -> Path:
    tax_year = int(payload["tax_year"])
    OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
    path = official_policy_path(tax_year)
    path.write_text(
        json_dumps(payload) + "\n",
        encoding="utf-8",
    )
    from config.policy.live import clear_official_policy_cache

    clear_official_policy_cache(tax_year)
    return path


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2)


def compare_with_existing(
    fetched: dict[str, Any],
    existing: dict[str, Any],
    *,
    tolerance: float = 0.02,
) -> list[str]:
    """Return human-readable diffs over tolerance (fractional)."""
    diffs: list[str] = []
    for key in ("mfj_standard_deduction", "single_standard_deduction"):
        new_val = float(fetched["scalars"][key])
        old_val = float(existing["scalars"][key])
        if old_val and abs(new_val - old_val) / old_val > tolerance:
            diffs.append(f"scalars.{key}: fetched {new_val:,.0f} vs bundled {old_val:,.0f}")
    for label in ("single", "mfj"):
        new_top = fetched["brackets"][label][-2][0]
        old_top = existing["brackets"][label][-2][0]
        if isinstance(new_top, (int, float)) and isinstance(old_top, (int, float)):
            if old_top and abs(float(new_top) - float(old_top)) / float(old_top) > tolerance:
                diffs.append(f"brackets.{label} top 35% ceiling differs")
    return diffs
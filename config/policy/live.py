"""Load official IRS policy tables by tax year (overrides embedded 2024 baseline)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.policy.tables import (
    MFJ_SS_BASE_2024,
    MFJ_SS_TIER2_2024,
    NIIT_MFJ_THRESHOLD_2024,
    NIIT_MFS_THRESHOLD_2024,
    NIIT_SINGLE_THRESHOLD_2024,
    SINGLE_SS_BASE_2024,
    SINGLE_SS_TIER2_2024,
)

POLICY_PKG_DIR = Path(__file__).resolve().parent
UPDATES_DIR = POLICY_PKG_DIR / "updates"
OFFICIAL_DIR = UPDATES_DIR / "official"
ACTIVE_POINTER = UPDATES_DIR / "policy_active.json"
MANIFEST_PATH = UPDATES_DIR / "manifest.json"

# Social Security provisional-income bases and NIIT thresholds are statutory
# and are not inflation-indexed. Official JSON copies that grew them are ignored.
_UNINDEXED_THRESHOLDS = {
    "mfj_ss_base": MFJ_SS_BASE_2024,
    "mfj_ss_tier2": MFJ_SS_TIER2_2024,
    "single_ss_base": SINGLE_SS_BASE_2024,
    "single_ss_tier2": SINGLE_SS_TIER2_2024,
    "niit_mfj_threshold": NIIT_MFJ_THRESHOLD_2024,
    "niit_single_threshold": NIIT_SINGLE_THRESHOLD_2024,
    "niit_mfs_threshold": NIIT_MFS_THRESHOLD_2024,
}

_policy_json_cache: dict[int, dict[str, Any]] = {}
_auto_pull_attempted = False


@dataclass(frozen=True)
class ActivePolicyMeta:
    tax_year: int
    source: str
    activated_at: str
    path: Path


def _bracket_tuples(raw: list[list[float]]) -> tuple[tuple[float, float], ...]:
    return tuple((float(ceiling), float(rate)) for ceiling, rate in raw)


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"years": [], "default_year": 2024}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def list_official_years() -> list[int]:
    manifest = load_manifest()
    entries = manifest.get("years", [])
    if entries:
        years: list[int] = []
        for entry in entries:
            if isinstance(entry, dict):
                years.append(int(entry["tax_year"]))
            else:
                years.append(int(entry))
        return sorted(set(years))
    return sorted(
        int(path.stem)
        for path in OFFICIAL_DIR.glob("*.json")
        if path.stem.isdigit()
    )


def official_policy_path(tax_year: int) -> Path:
    return OFFICIAL_DIR / f"{tax_year}.json"


def clear_official_policy_cache(tax_year: int | None = None) -> None:
    """Drop cached official JSON so a fresh write is visible in this process."""
    if tax_year is None:
        _policy_json_cache.clear()
        return
    _policy_json_cache.pop(int(tax_year), None)


def load_official_policy(tax_year: int) -> dict[str, Any]:
    year = int(tax_year)
    cached = _policy_json_cache.get(year)
    if cached is not None:
        return cached
    path = official_policy_path(year)
    if not path.exists():
        raise FileNotFoundError(f"No official policy file for tax year {year}: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _policy_json_cache[year] = payload
    return payload


def get_active_policy_meta() -> ActivePolicyMeta | None:
    if not ACTIVE_POINTER.exists():
        return None
    payload = json.loads(ACTIVE_POINTER.read_text(encoding="utf-8"))
    tax_year = int(payload["tax_year"])
    path = official_policy_path(tax_year)
    if not path.exists():
        return None
    return ActivePolicyMeta(
        tax_year=tax_year,
        source=str(payload.get("source", "")),
        activated_at=str(payload.get("activated_at", "")),
        path=path,
    )


def _auto_pull_enabled(manifest: dict[str, Any]) -> bool:
    if manifest.get("auto_pull") is True:
        return True
    return os.getenv("POLICY_AUTO_PULL", "").strip().lower() in {"1", "true", "yes"}


def _policy_fetch_age_days(policy: dict[str, Any]) -> int | None:
    fetched_at = policy.get("fetched_at")
    if not fetched_at:
        return None
    try:
        stamp = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
        return max(0, delta.days)
    except (TypeError, ValueError):
        return None


def maybe_auto_pull_latest() -> ActivePolicyMeta | None:
    """Fetch manifest default_year from IRS when auto-pull is enabled and data is stale.

    One attempt per process. A projection asks for tax tables many times; a stale
    ``fetched_at`` must not hit the IRS on every year.
    """
    global _auto_pull_attempted

    manifest = load_manifest()
    if not _auto_pull_enabled(manifest):
        return None

    force_pull = os.getenv("POLICY_FORCE_PULL", "").strip().lower() in {"1", "true", "yes"}
    if _auto_pull_attempted and not force_pull:
        return None
    if not force_pull:
        _auto_pull_attempted = True

    default_year = int(manifest.get("default_year", 2024))
    max_age_days = int(manifest.get("auto_pull_max_age_days", 30))
    policy_path = official_policy_path(default_year)

    if policy_path.exists() and not force_pull:
        try:
            policy = load_official_policy(default_year)
            age_days = _policy_fetch_age_days(policy)
            if age_days is not None and age_days < max_age_days:
                return None
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    try:
        from config.policy.irs_fetcher import fetch_and_build_policy, write_official_policy

        payload = fetch_and_build_policy(default_year)
        write_official_policy(payload)
        return activate_policy_year(default_year)
    except (OSError, ValueError, FileNotFoundError):
        return None


def ensure_active_policy(*, try_auto_pull: bool = True) -> ActivePolicyMeta | None:
    """Activate manifest default_year; optionally refresh stale tables from IRS."""
    if try_auto_pull:
        maybe_auto_pull_latest()

    existing = get_active_policy_meta()
    if existing is not None:
        return existing

    manifest = load_manifest()
    default_year = int(manifest.get("default_year", 2024))
    try:
        return activate_policy_year(default_year)
    except FileNotFoundError:
        return None


def activate_policy_year(tax_year: int) -> ActivePolicyMeta:
    policy = load_official_policy(tax_year)
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "tax_year": tax_year,
        "source": policy.get("source", ""),
        "activated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    ACTIVE_POINTER.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return ActivePolicyMeta(
        tax_year=tax_year,
        source=payload["source"],
        activated_at=payload["activated_at"],
        path=official_policy_path(tax_year),
    )


def policy_tables_for_year(
    tax_year: int,
    *,
    inflation_factor: float = 1.0,
) -> dict[str, Any]:
    """Return scaled policy constants/brackets for a specific official tax year."""
    raw = load_official_policy(tax_year)
    factor = max(0.01, inflation_factor)

    def scale(value: float) -> float:
        return float(value) * factor

    def scale_brackets(name: str) -> tuple[tuple[float, float], ...]:
        brackets = raw.get("brackets", {}).get(name, [])
        scaled: list[tuple[float, float]] = []
        for ceiling, rate in brackets:
            cap = float("inf") if ceiling in ("inf", float("inf")) else scale(float(ceiling))
            scaled.append((cap, float(rate)))
        return tuple(scaled)

    scalars = raw.get("scalars", {})
    amt = raw.get("amt", {})
    return {
        "base_year": tax_year,
        "inflation_factor": factor,
        "source": raw.get("source", ""),
        "mfj_standard_deduction": scale(scalars.get("mfj_standard_deduction", 29_200)),
        "single_standard_deduction": scale(scalars.get("single_standard_deduction", 14_600)),
        "hoh_standard_deduction": scale(scalars.get("hoh_standard_deduction", 21_900)),
        "mfj_brackets": scale_brackets("mfj"),
        "single_brackets": scale_brackets("single"),
        "hoh_brackets": scale_brackets("hoh"),
        "mfj_ss_base": _UNINDEXED_THRESHOLDS["mfj_ss_base"],
        "mfj_ss_tier2": _UNINDEXED_THRESHOLDS["mfj_ss_tier2"],
        "single_ss_base": _UNINDEXED_THRESHOLDS["single_ss_base"],
        "single_ss_tier2": _UNINDEXED_THRESHOLDS["single_ss_tier2"],
        "fpl_base": scale(scalars.get("fpl_base", 15_060)),
        "fpl_per_additional_person": scale(scalars.get("fpl_per_additional_person", 5_380)),
        "medicare_part_b_monthly": scale(scalars.get("medicare_part_b_monthly", 174.70)),
        "medicare_part_d_monthly": scale(scalars.get("medicare_part_d_monthly", 55.50)),
        "mfj_additional_sd_65": scale(scalars.get("mfj_additional_sd_65", 1_550)),
        "single_additional_sd_65": scale(scalars.get("single_additional_sd_65", 1_950)),
        "mfj_ltcg_tiers": scale_brackets("mfj_ltcg"),
        "single_ltcg_tiers": scale_brackets("single_ltcg"),
        "hoh_ltcg_tiers": scale_brackets("hoh_ltcg"),
        "niit_mfj_threshold": _UNINDEXED_THRESHOLDS["niit_mfj_threshold"],
        "niit_single_threshold": _UNINDEXED_THRESHOLDS["niit_single_threshold"],
        "niit_mfs_threshold": _UNINDEXED_THRESHOLDS["niit_mfs_threshold"],
        "amt_exemption_mfj": scale(amt.get("exemption_mfj", 126_500)),
        "amt_exemption_single": scale(amt.get("exemption_single", 85_700)),
        "amt_exemption_mfs": scale(amt.get("exemption_mfs", 43_350)),
        "amt_phaseout_mfj": scale(amt.get("phaseout_start_mfj", 1_218_700)),
        "amt_phaseout_single": scale(amt.get("phaseout_start_single", 609_350)),
        "amt_phaseout_mfs": scale(amt.get("phaseout_start_mfs", 609_350)),
        "amt_26_bracket_mfj": scale(amt.get("bracket_26_mfj", 232_600)),
        "amt_26_bracket_single": scale(amt.get("bracket_26_single", 116_300)),
    }


def active_policy_label() -> str:
    meta = get_active_policy_meta()
    if meta is None:
        return "embedded 2024 baseline (config/policy/tables.py)"
    return f"IRS {meta.tax_year} — {meta.source}"
#!/usr/bin/env python3
"""
Manage official IRS policy table JSON files (config/policy/updates/official/).

Usage:
  python tools/fetch_policy_tables.py --list
  python tools/fetch_policy_tables.py --status
  python tools/fetch_policy_tables.py --activate 2025
  python tools/fetch_policy_tables.py --ensure-latest
  python tools/fetch_policy_tables.py --fetch 2025
  python tools/fetch_policy_tables.py --fetch 2026 --activate
  python tools/fetch_policy_tables.py --pull-latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.policy.live import (  # noqa: E402
    activate_policy_year,
    active_policy_label,
    ensure_active_policy,
    get_active_policy_meta,
    list_official_years,
    load_manifest,
    official_policy_path,
)


def _print_status() -> int:
    meta = get_active_policy_meta()
    manifest = load_manifest()
    default_year = int(manifest.get("default_year", 2024))
    available = list_official_years()
    print(f"Available years: {', '.join(str(y) for y in available)}")
    print(f"Manifest default: {default_year}")
    if meta is None:
        print("Active policy: none (embedded 2024 baseline)")
        return 1
    print(f"Active policy: {meta.tax_year} — {meta.source}")
    print(f"Activated at: {meta.activated_at}")
    print(f"Path: {meta.path}")
    return 0


def _print_list() -> int:
    manifest = load_manifest()
    active = get_active_policy_meta()
    active_year = active.tax_year if active else None
    for entry in manifest.get("years", []):
        year = int(entry["tax_year"])
        marker = " *" if year == active_year else ""
        source = entry.get("source", "")
        print(f"{year}{marker}: {source}")
    for year in list_official_years():
        if not any(int(e["tax_year"]) == year for e in manifest.get("years", [])):
            print(f"{year}: (file only) {official_policy_path(year)}")
    return 0


def _activate(year: int) -> int:
    meta = activate_policy_year(year)
    print(f"Activated IRS {meta.tax_year} — {meta.source}")
    print(active_policy_label())
    return 0


def _fetch_year(year: int, *, dry_run: bool, activate: bool) -> int:
    from config.policy.irs_fetcher import (
        compare_with_existing,
        fetch_and_build_policy,
        write_official_policy,
    )
    from config.policy.live import load_official_policy

    print(f"Fetching IRS newsroom inflation adjustments for tax year {year}...")
    try:
        payload = fetch_and_build_policy(year)
    except (OSError, ValueError) as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return 1

    existing_path = official_policy_path(year)
    if existing_path.exists():
        diffs = compare_with_existing(payload, load_official_policy(year))
        if diffs:
            print("Differences vs bundled file:")
            for diff in diffs:
                print(f"  - {diff}")
        else:
            print("Fetched values align with bundled file (within tolerance).")

    print(f"Source: {payload.get('source')}")
    print(f"Newsroom: {payload.get('newsroom_url', 'n/a')}")
    print(
        "MFJ standard deduction: "
        f"${payload['scalars']['mfj_standard_deduction']:,.0f}"
    )
    if payload.get("fetch_method") == "irs_newsroom_llm":
        print(
            "Parse method: LLM fallback "
            f"({payload.get('llm_provider', 'unknown')}/{payload.get('llm_model', 'unknown')})"
        )
    if payload.get("fetch_notes"):
        print(f"Note: {payload['fetch_notes']}")

    if dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    path = write_official_policy(payload)
    print(f"Wrote {path}")
    if activate:
        return _activate(year)
    return 0


def _pull_latest(*, dry_run: bool, activate: bool) -> int:
    manifest = load_manifest()
    default_year = int(manifest.get("default_year", 2024))
    print(f"Pull target tax year: {default_year}")
    code = _fetch_year(default_year, dry_run=dry_run, activate=False)
    if code != 0:
        return code
    if activate:
        return _activate(default_year)
    return 0


def _ensure_latest() -> int:
    manifest = load_manifest()
    default_year = int(manifest.get("default_year", 2024))
    available = list_official_years()
    if default_year not in available:
        print(f"Default year {default_year} not available on disk.", file=sys.stderr)
        return 1
    active = get_active_policy_meta()
    if active is not None and active.tax_year >= default_year:
        print(f"Already on latest available year: {active.tax_year}")
        return 0
    return _activate(default_year)


def main() -> int:
    parser = argparse.ArgumentParser(description="Official IRS policy table manager")
    parser.add_argument("--list", action="store_true", help="List bundled official years")
    parser.add_argument("--status", action="store_true", help="Show active policy pointer")
    parser.add_argument("--activate", type=int, metavar="YEAR", help="Activate a bundled tax year")
    parser.add_argument(
        "--ensure-latest",
        action="store_true",
        help="Activate manifest default_year if newer than current active",
    )
    parser.add_argument(
        "--fetch",
        type=int,
        metavar="YEAR",
        help="Download IRS newsroom inflation adjustments and write official/YEAR.json",
    )
    parser.add_argument(
        "--pull-latest",
        action="store_true",
        help="Fetch manifest default_year from IRS, write JSON, and activate it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fetch/--pull-latest, print JSON without writing files",
    )
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="With --pull-latest, fetch only (do not activate after write)",
    )
    parser.add_argument(
        "--activate-after-fetch",
        action="store_true",
        help="With --fetch, activate the year after writing official JSON",
    )
    args = parser.parse_args()

    if args.list:
        return _print_list()
    if args.status:
        return _print_status()
    if args.activate is not None:
        return _activate(args.activate)
    if args.fetch is not None:
        activate_after = bool(getattr(args, "activate_after_fetch", False))
        return _fetch_year(args.fetch, dry_run=args.dry_run, activate=activate_after)
    if args.pull_latest:
        return _pull_latest(dry_run=args.dry_run, activate=not args.no_activate)
    if args.ensure_latest:
        return _ensure_latest()

    ensure_active_policy()
    return _print_status()


if __name__ == "__main__":
    raise SystemExit(main())
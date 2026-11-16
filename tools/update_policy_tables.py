#!/usr/bin/env python3
"""
Annual policy-table update helper.

Usage:
  python tools/update_policy_tables.py --check
  python tools/update_policy_tables.py --template 2025
  python tools/update_policy_tables.py --apply config/policy/updates/2025.json --dry-run

Workflow:
  1. Generate a template JSON for the target tax year.
  2. Fill in IRS/CMS/HHS published values.
  3. Run with --apply to preview or patch config/policy/tables.py constants.
  4. Regenerate golden files: python validation/build_golden_file.py
  5. Run regression: python scripts/regression_test.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLICY_FILE = PROJECT_ROOT / "config" / "policy" / "tables.py"
UPDATES_DIR = PROJECT_ROOT / "config" / "policy" / "updates"

# Scalar constants in policy_tables.py that can be updated from JSON.
SCALAR_CONSTANTS = (
    "MFJ_STANDARD_DEDUCTION_2024",
    "SINGLE_STANDARD_DEDUCTION_2024",
    "HOH_STANDARD_DEDUCTION_2024",
    "MFJ_SS_BASE_2024",
    "MFJ_SS_TIER2_2024",
    "SINGLE_SS_BASE_2024",
    "SINGLE_SS_TIER2_2024",
    "MFS_SS_BASE_LIVED_APART_2024",
    "MFS_SS_TIER2_LIVED_APART_2024",
    "MFJ_ADDITIONAL_SD_65_2024",
    "SINGLE_ADDITIONAL_SD_65_2024",
    "NIIT_MFJ_THRESHOLD_2024",
    "NIIT_SINGLE_THRESHOLD_2024",
    "NIIT_MFS_THRESHOLD_2024",
    "FPL_BASE_2024",
    "FPL_PER_ADDITIONAL_PERSON_2024",
    "PART_B_STANDARD_MONTHLY_2024",
    "PART_D_AVERAGE_MONTHLY_2024",
    "QCD_ANNUAL_CAP_2024",
    "SS_PIA_BEND1_2024",
    "SS_PIA_BEND2_2024",
    "DEFAULT_POLICY_BASE_YEAR",
)


def _template_payload(target_year: int) -> dict:
    return {
        "target_year": target_year,
        "notes": "Fill published IRS/CMS/HHS values, then run --apply.",
        "scalars": {name: None for name in SCALAR_CONSTANTS},
        "brackets": {
            "MFJ_BRACKETS_2024": [],
            "SINGLE_BRACKETS_2024": [],
            "HOH_BRACKETS_2024": [],
            "MFJ_LTCG_TIERS_2024": [],
            "SINGLE_LTCG_TIERS_2024": [],
            "HOH_LTCG_TIERS_2024": [],
        },
        "irmaa": {
            "MFJ_IRMAA_TIERS_2024": [],
            "SINGLE_IRMAA_TIERS_2024": [],
        },
        "aca_tiers": {
            "ACA_APPLICABLE_PCT_TIERS": [],
        },
        "medigap": {
            "MEDIGAP_PLAN_MONTHLY_2024": {},
        },
    }


def _write_template(target_year: int) -> Path:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = UPDATES_DIR / f"{target_year}.template.json"
    path.write_text(json.dumps(_template_payload(target_year), indent=2) + "\n", encoding="utf-8")
    return path


def _load_current_scalars() -> dict[str, str]:
    text = POLICY_FILE.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for name in SCALAR_CONSTANTS:
        match = re.search(rf"^{re.escape(name)}\s*=\s*(.+)$", text, re.MULTILINE)
        if match:
            found[name] = match.group(1).strip()
    return found


def _check_policy_file() -> list[str]:
    issues: list[str] = []
    text = POLICY_FILE.read_text(encoding="utf-8")
    for name in SCALAR_CONSTANTS:
        if name not in text:
            issues.append(f"Missing constant: {name}")
    if "def policy_for_projection" not in text:
        issues.append("Missing policy_for_projection()")
    return issues


def _apply_scalars(update: dict, *, dry_run: bool) -> list[str]:
    changes: list[str] = []
    scalars = update.get("scalars", {})
    if not scalars:
        return changes

    text = POLICY_FILE.read_text(encoding="utf-8")
    for name, value in scalars.items():
        if value is None:
            continue
        if name not in SCALAR_CONSTANTS:
            raise ValueError(f"Unknown scalar constant: {name}")
        replacement = f"{name} = {value}"
        pattern = rf"^{re.escape(name)}\s*=\s*.+$"
        if not re.search(pattern, text, re.MULTILINE):
            raise ValueError(f"Constant not found in policy_tables.py: {name}")
        old_line = re.search(pattern, text, re.MULTILINE).group(0)  # type: ignore[union-attr]
        if old_line == replacement:
            continue
        changes.append(f"{old_line} -> {replacement}")
        text = re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE)

    if changes and not dry_run:
        POLICY_FILE.write_text(text, encoding="utf-8")
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Annual policy table update helper")
    parser.add_argument("--check", action="store_true", help="Validate policy_tables.py structure")
    parser.add_argument("--template", type=int, metavar="YEAR", help="Write update template JSON")
    parser.add_argument("--apply", type=Path, metavar="JSON", help="Apply scalar updates from JSON")
    parser.add_argument("--dry-run", action="store_true", help="Preview --apply without writing")
    parser.add_argument("--show-current", action="store_true", help="Print current scalar constants")
    args = parser.parse_args(argv)

    if args.check:
        issues = _check_policy_file()
        if issues:
            print("Policy table check FAILED:")
            for issue in issues:
                print(f"  - {issue}")
            return 1
        print(f"Policy table check OK ({len(SCALAR_CONSTANTS)} tracked scalars)")
        return 0

    if args.template:
        path = _write_template(args.template)
        print(f"Template written: {path}")
        return 0

    if args.show_current:
        current = _load_current_scalars()
        for name, value in current.items():
            print(f"{name} = {value}")
        return 0

    if args.apply:
        payload = json.loads(args.apply.read_text(encoding="utf-8"))
        changes = _apply_scalars(payload, dry_run=args.dry_run)
        if not changes:
            print("No scalar changes needed.")
            return 0
        header = "Dry-run scalar updates:" if args.dry_run else "Applied scalar updates:"
        print(header)
        for change in changes:
            print(f"  {change}")
        if args.dry_run:
            print("Re-run without --dry-run to write config/policy/tables.py")
        else:
            print("Next: python validation/build_golden_file.py && python scripts/regression_test.py")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
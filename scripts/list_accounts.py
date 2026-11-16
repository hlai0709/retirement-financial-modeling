#!/usr/bin/env python3
"""
List connected Plaid accounts, balances, and owner mappings.

Usage:
    python scripts/list_accounts.py              # cached snapshot, or live refresh if empty
    python scripts/list_accounts.py --refresh    # force live Plaid refresh
    python scripts/list_accounts.py --cached     # vault cache only (no API calls)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.data_service import DataService, RefreshSnapshot
from security.keychain import list_item_ids
from settings import accounts_path, get_settings


def _format_money(amount: float, currency: str = "USD") -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def _print_header(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _print_institutions(service: DataService) -> None:
    config = service.load_accounts_config()
    item_ids = list_item_ids()

    _print_header("Connected institutions")
    if not item_ids:
        print("  (none — run: python scripts/setup_plaid.py link)")
        return

    institution_by_item = {inst.item_id: inst for inst in config.institutions}
    for item_id in item_ids:
        record = institution_by_item.get(item_id)
        if record:
            print(f"  {record.institution_name}  item_id={item_id}  connected={record.connected_at}")
        else:
            print(f"  (unregistered item)  item_id={item_id}")


def _print_accounts(snapshot: RefreshSnapshot) -> None:
    _print_header(f"Accounts  (as of {snapshot.as_of or 'unknown'})")

    if not snapshot.accounts:
        print("  (no accounts — connect an institution or run with --refresh)")
        return

    rows = []
    for account in snapshot.accounts:
        mask = f"…{account.mask}" if account.mask else ""
        asset_flag = "yes" if account.include_in_assets else "no"
        rows.append(
            (
                account.institution,
                account.name,
                mask,
                account.type,
                account.subtype or "",
                _format_money(account.balance, account.currency),
                account.owner,
                asset_flag,
                account.balance_source,
            )
        )

    headers = ("Institution", "Name", "Mask", "Type", "Subtype", "Balance", "Owner", "Assets", "Source")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(cells))

    print("  " + fmt_row(headers))
    print("  " + "  ".join("-" * width for width in widths))
    for row in rows:
        print("  " + fmt_row(row))


def _print_owner_totals(snapshot: RefreshSnapshot) -> None:
    _print_header("Owner asset totals")
    if not snapshot.owner_totals:
        print("  (none)")
        return

    for owner, total in sorted(snapshot.owner_totals.items()):
        print(f"  {owner:10} {_format_money(total)}")

    unassigned = [account for account in snapshot.accounts if account.owner == "unassigned"]
    if unassigned:
        print()
        print(f"  Note: {len(unassigned)} account(s) still unassigned — map owners in accounts.json")
        print(f"         {accounts_path()}")


def _print_errors(snapshot: RefreshSnapshot) -> None:
    if not snapshot.errors:
        return
    _print_header("Refresh errors")
    for error in snapshot.errors:
        print(f"  - {error}")


def _load_snapshot(service: DataService, *, refresh: bool, cached_only: bool) -> RefreshSnapshot:
    if cached_only:
        cached = service.load_cached_snapshot()
        if cached is None:
            raise RuntimeError("No cached snapshot found. Run with --refresh first.")
        return cached

    if refresh:
        return service.refresh_live_data()

    cached = service.load_cached_snapshot()
    if cached is not None:
        return cached

    if not list_item_ids():
        raise RuntimeError("No Plaid items connected. Run: python scripts/setup_plaid.py link")

    return service.refresh_live_data()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List Plaid accounts and owner mappings")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch live balances from Plaid (default if cache is empty)",
    )
    group.add_argument(
        "--cached",
        action="store_true",
        help="Show last cached snapshot only (no Plaid API calls)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    settings = get_settings()
    if args.refresh and not settings.plaid_configured:
        print("Error: PLAID_CLIENT_ID and PLAID_SECRET required in .env for --refresh", file=sys.stderr)
        return 1

    service = DataService()

    try:
        snapshot = _load_snapshot(service, refresh=args.refresh, cached_only=args.cached)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("Retirement Planner — account listing")
    _print_institutions(service)
    _print_accounts(snapshot)
    _print_owner_totals(snapshot)
    _print_errors(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
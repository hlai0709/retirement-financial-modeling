"""Parse manual CSV balance imports when Plaid is unavailable."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

VALID_OWNERS = frozenset({"self", "wife", "exclude", "unassigned"})


@dataclass(frozen=True)
class CsvBalanceRow:
    account_name: str
    balance: float
    owner: str


def _parse_amount(raw: str) -> float:
    cleaned = raw.strip().replace("$", "").replace(",", "")
    if not cleaned:
        raise ValueError("balance is empty")
    return float(cleaned)


def _normalize_owner(raw: str | None) -> str:
    owner = (raw or "self").strip().lower()
    if owner not in VALID_OWNERS:
        raise ValueError(f"owner must be one of {sorted(VALID_OWNERS)}")
    return owner


def _looks_like_header(row: list[str]) -> bool:
    if not row:
        return False
    joined = " ".join(cell.lower() for cell in row)
    return "account" in joined or "balance" in joined or "owner" in joined


def parse_balance_csv(path: str | Path) -> list[CsvBalanceRow]:
    """
    Parse ``account_name,balance[,owner]`` CSV exports.

    Owner defaults to ``self`` when omitted. Rows with owner ``exclude`` are skipped
    from asset totals but still returned for review.
    """
    rows: list[CsvBalanceRow] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        raw_rows = [row for row in reader if any(cell.strip() for cell in row)]

    if not raw_rows:
        raise ValueError("CSV file is empty")

    start = 1 if _looks_like_header(raw_rows[0]) else 0
    for line_number, cells in enumerate(raw_rows[start:], start=start + 1):
        if len(cells) < 2:
            raise ValueError(f"Line {line_number}: expected account_name,balance[,owner]")
        account_name = cells[0].strip()
        if not account_name:
            raise ValueError(f"Line {line_number}: account_name is required")
        balance = _parse_amount(cells[1])
        owner = _normalize_owner(cells[2] if len(cells) > 2 else "self")
        rows.append(CsvBalanceRow(account_name=account_name, balance=balance, owner=owner))

    if not rows:
        raise ValueError("No balance rows found in CSV")
    return rows


def owner_totals_from_rows(rows: list[CsvBalanceRow]) -> dict[str, float]:
    totals = {"self": 0.0, "wife": 0.0}
    for row in rows:
        if row.owner in totals and row.owner != "exclude":
            totals[row.owner] += row.balance
    return totals
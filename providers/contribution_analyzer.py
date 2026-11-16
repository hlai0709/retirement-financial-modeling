"""Estimate annual investment contributions from Plaid deposit transactions."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from providers.plaid_provider import TransactionRecord

CONTRIBUTION_LOOKBACK_MONTHS = 12

EXCLUDED_PFC_DETAILED = frozenset(
    {
        "INCOME_WAGES",
        "INCOME_INTEREST_EARNED",
        "INCOME_DIVIDENDS",
        "INCOME_RETIREMENT_PENSION",
        "INCOME_TAX_REFUND",
        "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
        "TRANSFER_OUT_WITHDRAWAL",
        "TRANSFER_OUT_ACCOUNT_TRANSFER",
        "INVESTMENT_SELL",
    }
)

INCLUDED_PFC_DETAILED = frozenset(
    {
        "TRANSFER_IN_DEPOSIT",
        "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
        "TRANSFER_IN_ACCOUNT_TRANSFER",
    }
)

CONTRIBUTION_KEYWORDS = (
    "401k",
    "403b",
    "457",
    "ira",
    "roth",
    "tsp",
    "contribution",
    "brokerage",
    "fidelity",
    "vanguard",
    "schwab",
    "etrade",
    "merrill",
    "acorns",
    "betterment",
    "wealthfront",
)


@dataclass
class ContributionLineItem:
    merchant: str
    source_type: str
    monthly_amount: float
    annual_amount: float
    occurrence_count: int
    owner: str = "unassigned"
    included: bool = True

    def to_dict(self) -> dict:
        return {
            "merchant": self.merchant,
            "source_type": self.source_type,
            "monthly_amount": self.monthly_amount,
            "annual_amount": self.annual_amount,
            "occurrence_count": self.occurrence_count,
            "owner": self.owner,
            "included": self.included,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContributionLineItem:
        return cls(
            merchant=data["merchant"],
            source_type=data.get("source_type", "deposit"),
            monthly_amount=float(data["monthly_amount"]),
            annual_amount=float(data["annual_amount"]),
            occurrence_count=int(data.get("occurrence_count", 0)),
            owner=data.get("owner", "unassigned"),
            included=bool(data.get("included", True)),
        )


@dataclass
class ContributionAnalysis:
    items: list[ContributionLineItem] = field(default_factory=list)
    annual_household: float = 0.0
    months_analyzed: float = 0.0
    transactions_used: int = 0
    transactions_excluded: int = 0

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "annual_household": self.annual_household,
            "months_analyzed": self.months_analyzed,
            "transactions_used": self.transactions_used,
            "transactions_excluded": self.transactions_excluded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContributionAnalysis:
        return cls(
            items=[ContributionLineItem.from_dict(item) for item in data.get("items", [])],
            annual_household=float(data.get("annual_household", 0)),
            months_analyzed=float(data.get("months_analyzed", 0)),
            transactions_used=int(data.get("transactions_used", 0)),
            transactions_excluded=int(data.get("transactions_excluded", 0)),
        )

    def included_annual_total(self) -> float:
        return sum(item.annual_amount for item in self.items if item.included)

    def owner_annual_totals(self, mode: str) -> dict[str, float]:
        totals = {"self": 0.0, "wife": 0.0}
        for item in self.items:
            if not item.included or item.owner not in totals:
                continue
            totals[item.owner] += item.annual_amount
        if mode == "joint" and not any(item.owner in totals for item in self.items if item.included):
            half = self.included_annual_total() / 2
            return {"self": half, "wife": half}
        return totals


def _parse_txn_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _looks_like_contribution(txn: TransactionRecord) -> bool:
    if txn.pending:
        return False
    if txn.amount >= 0:
        return False

    detailed = (txn.pfc_detailed or "").upper()
    primary = (txn.pfc_primary or "").upper()
    if detailed in EXCLUDED_PFC_DETAILED or primary in {"INCOME", "TRANSFER_OUT"}:
        return False

    if detailed in INCLUDED_PFC_DETAILED:
        return True

    haystack = " ".join(
        part
        for part in (txn.merchant_name, txn.name, detailed, primary)
        if part
    ).lower()
    if any(keyword in haystack for keyword in CONTRIBUTION_KEYWORDS):
        return True

    if "deposit" in haystack and "payroll" not in haystack:
        return True
    return False


def _source_type(txn: TransactionRecord) -> str:
    detailed = (txn.pfc_detailed or "").lower()
    if "investment" in detailed or "retirement" in detailed:
        return "retirement_transfer"
    if "deposit" in detailed:
        return "deposit"
    return "contribution"


def analyze_contributions(
    transactions: list[TransactionRecord],
    *,
    account_owners: dict[str, str] | None = None,
    lookback_months: int = CONTRIBUTION_LOOKBACK_MONTHS,
) -> ContributionAnalysis:
    """Sum investment-related deposits over the lookback window and annualize."""
    account_owners = account_owners or {}
    eligible = [txn for txn in transactions if _looks_like_contribution(txn)]
    excluded = len(transactions) - len(eligible)

    if not eligible:
        return ContributionAnalysis(transactions_excluded=excluded)

    dates = [_parse_txn_date(txn.date) for txn in eligible]
    min_date = min(dates)
    max_date = max(dates)
    months = max(1.0, (max_date - min_date).days / 30.44)
    months = min(months, float(lookback_months))

    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    owners: dict[tuple[str, str], str] = {}
    types: dict[tuple[str, str], str] = {}

    for txn in eligible:
        merchant = re.sub(r"\s+", " ", (txn.merchant_name or txn.name).strip()) or "Unknown"
        owner = account_owners.get(txn.account_id, "unassigned")
        key = (merchant, owner)
        grouped[key].append(abs(txn.amount))
        owners[key] = owner
        types[key] = _source_type(txn)

    items: list[ContributionLineItem] = []
    for (merchant, owner), amounts in sorted(grouped.items(), key=lambda kv: sum(kv[1]), reverse=True):
        total = sum(amounts)
        annual = total * 12 / months
        items.append(
            ContributionLineItem(
                merchant=merchant,
                source_type=types[(merchant, owner)],
                monthly_amount=annual / 12,
                annual_amount=annual,
                occurrence_count=len(amounts),
                owner=owner,
            )
        )

    annual_household = sum(item.annual_amount for item in items)
    return ContributionAnalysis(
        items=items,
        annual_household=annual_household,
        months_analyzed=months,
        transactions_used=len(eligible),
        transactions_excluded=excluded,
    )
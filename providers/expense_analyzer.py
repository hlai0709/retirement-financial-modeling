"""Detect recurring bills and estimate household expenses from Plaid transactions."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median

from providers.plaid_provider import TransactionRecord

EXPENSE_CATEGORIES = (
    "mortgage",
    "utilities",
    "insurance",
    "subscriptions",
    "food",
    "dining",
    "transportation",
    "healthcare",
    "education",
    "property_tax",
    "travel",
    "discretionary",
    "other",
)

EXPENSE_ACCOUNT_TYPES = frozenset({"depository", "credit"})

EXCLUDED_PFC_PRIMARY = frozenset(
    {
        "INCOME",
        "TRANSFER_IN",
        "TRANSFER_OUT",
    }
)

EXCLUDED_PFC_DETAILED = frozenset(
    {
        "TRANSFER_IN_ACCOUNT_TRANSFER",
        "TRANSFER_OUT_ACCOUNT_TRANSFER",
        "TRANSFER_IN_DEPOSIT",
        "TRANSFER_OUT_WITHDRAWAL",
        "TRANSFER_OUT_PAYMENT",  # credit-card bill pays — spend already on the card
        "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
        "INCOME_WAGES",
        "INCOME_INTEREST_EARNED",
        "INCOME_DIVIDENDS",
        "INCOME_RETIREMENT_PENSION",
        "INCOME_TAX_REFUND",
        "INVESTMENT_BUY",
        "INVESTMENT_SELL",
    }
)

# Paying a credit card from checking double-counts purchases already on the card.
_CREDIT_CARD_PAYMENT_MARKERS = (
    "crcardpmt",
    "credit card payment",
    "creditcard payment",
    "card payment",
    "citicardap",
    "citi autopay",
    "capital one crcard",
    "american express ach pmt",
    "amex epayment",
    "amazon store card",
    "chase credit crd",
    "discover bank e-payment",
    "bank of america credit card",
    "thank you payment",
    "payment thank you",
    "online payment from chk",
)

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mortgage": (
        "mortgage",
        "rocket mortgage",
        "freedom mortgage",
        "mr cooper",
        "mr. cooper",
        "quicken loans",
        "pennymac",
        "loandepot",
        "wells fargo home",
        "home loan",
        "heloc",
    ),
    "utilities": (
        "electric",
        "power",
        "gas company",
        "water",
        "utility",
        "internet",
        "comcast",
        "xfinity",
        "at&t",
        "att ",
        "verizon",
        "t-mobile",
        "tmobile",
        "pge",
        "pg&e",
        "con ed",
        "trash",
        "waste management",
        "republic services",
    ),
    "insurance": (
        "insurance",
        "geico",
        "state farm",
        "allstate",
        "progressive",
        "aetna",
        "umbrella",
    ),
    "subscriptions": (
        "netflix",
        "spotify",
        "hulu",
        "disney+",
        "apple.com/bill",
        "amazon prime",
        "subscription",
        "youtube premium",
        "icloud",
    ),
    "food": (
        "safeway",
        "whole foods",
        "trader joe",
        "costco",
        "grocery",
        "kroger",
        "albertsons",
        "sprouts",
        "walmart",
        "target",
    ),
    "dining": (
        "restaurant",
        "doordash",
        "uber eats",
        "grubhub",
        "starbucks",
        "cafe",
        "coffee",
    ),
    "transportation": (
        "shell",
        "chevron",
        "exxon",
        "gas station",
        "uber",
        "lyft",
        "parking",
        "dmv",
        "toyota",
        "honda",
        "ford credit",
        "carmax",
        "auto loan",
    ),
    "healthcare": (
        "pharmacy",
        "cvs",
        "walgreens",
        "dental",
        "medical",
        "hospital",
        "kaiser",
        "urgent care",
        "copay",
    ),
    "education": (
        "school",
        "tuition",
        "university",
        "college",
        "stratford",
        "daycare",
        "preschool",
        "campus",
    ),
    "property_tax": (
        "property tax",
        "county tax",
        "tax collector",
        "assessor",
        "prop tax",
    ),
    "travel": (
        "airline",
        "airlines",
        "united air",
        "delta air",
        "american air",
        "southwest",
        "frontier air",
        "hotel",
        "marriott",
        "hilton",
        "airbnb",
        "expedia",
        "booking.com",
    ),
}

RECURRING_MIN_OCCURRENCES = 3
RECURRING_INTERVAL_DAYS = (25, 35)
AMOUNT_TOLERANCE = 0.10
# Variable (non-recurring) lines above this annualized $ default to excluded from base
ONE_OFF_EXCLUDE_ANNUAL = 2_500.0


@dataclass
class ExpenseLineItem:
    merchant: str
    category: str
    expense_type: str
    monthly_amount: float
    annual_amount: float
    occurrence_count: int
    owner: str = "unassigned"
    included: bool = True

    def to_dict(self) -> dict:
        return {
            "merchant": self.merchant,
            "category": self.category,
            "expense_type": self.expense_type,
            "monthly_amount": self.monthly_amount,
            "annual_amount": self.annual_amount,
            "occurrence_count": self.occurrence_count,
            "owner": self.owner,
            "included": self.included,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExpenseLineItem:
        return cls(
            merchant=data["merchant"],
            category=data.get("category", "other"),
            expense_type=data.get("expense_type", "variable"),
            monthly_amount=float(data["monthly_amount"]),
            annual_amount=float(data["annual_amount"]),
            occurrence_count=int(data.get("occurrence_count", 0)),
            owner=data.get("owner", "unassigned"),
            included=bool(data.get("included", True)),
        )


@dataclass
class ExpenseAnalysis:
    items: list[ExpenseLineItem] = field(default_factory=list)
    monthly_recurring: float = 0.0
    monthly_variable: float = 0.0
    annual_household: float = 0.0
    months_analyzed: float = 0.0
    transactions_used: int = 0
    transactions_excluded: int = 0

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "monthly_recurring": self.monthly_recurring,
            "monthly_variable": self.monthly_variable,
            "annual_household": self.annual_household,
            "months_analyzed": self.months_analyzed,
            "transactions_used": self.transactions_used,
            "transactions_excluded": self.transactions_excluded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExpenseAnalysis:
        return cls(
            items=[ExpenseLineItem.from_dict(item) for item in data.get("items", [])],
            monthly_recurring=float(data.get("monthly_recurring", 0)),
            monthly_variable=float(data.get("monthly_variable", 0)),
            annual_household=float(data.get("annual_household", 0)),
            months_analyzed=float(data.get("months_analyzed", 0)),
            transactions_used=int(data.get("transactions_used", 0)),
            transactions_excluded=int(data.get("transactions_excluded", 0)),
        )

    def included_annual_total(self) -> float:
        return sum(item.annual_amount for item in self.items if item.included)

    def owner_annual_totals(self, mode: str) -> dict[str, float]:
        totals = {"self": 0.0, "wife": 0.0}
        assigned = False
        for item in self.items:
            if not item.included or item.owner not in totals:
                continue
            totals[item.owner] += item.annual_amount
            assigned = True
        if mode == "joint" and not assigned:
            half = self.included_annual_total() / 2
            return {"self": half, "wife": half}
        return totals


def _normalize_merchant(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip().lower())
    return cleaned or "unknown"


def _parse_txn_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _is_credit_card_payment(merchant: str, pfc_detailed: str | None) -> bool:
    hay = f"{merchant} {pfc_detailed or ''}".lower()
    detailed = (pfc_detailed or "").upper()
    if "CREDIT_CARD_PAYMENT" in detailed:
        return True
    if "LOAN_PAYMENTS" in detailed and "MORTGAGE" not in detailed and "CREDIT" in detailed:
        return True
    return any(marker in hay for marker in _CREDIT_CARD_PAYMENT_MARKERS)


def _categorize_merchant(merchant: str, pfc_primary: str | None, pfc_detailed: str | None) -> str:
    haystack = " ".join(
        part for part in (merchant, pfc_primary or "", pfc_detailed or "") if part
    ).lower()
    detailed = (pfc_detailed or "").upper()
    # True mortgage only — not generic LOAN_PAYMENTS (credit cards, auto, etc.)
    if "MORTGAGE" in detailed:
        return "mortgage"
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return category
    primary = (pfc_primary or "").upper()
    if primary == "FOOD_AND_DRINK" or "FOOD_AND_DRINK" in detailed:
        if any(k in haystack for k in ("restaurant", "coffee", "cafe", "doordash", "uber eats")):
            return "dining"
        return "food"
    if primary in {"ENTERTAINMENT", "GENERAL_MERCHANDISE"}:
        return "discretionary"
    if primary == "TRANSPORTATION" or "TRANSPORTATION" in detailed:
        return "transportation"
    if primary == "RENT_AND_UTILITIES" or "UTILITIES" in detailed:
        return "utilities"
    if "MEDICAL" in detailed or "HEALTHCARE" in detailed:
        return "healthcare"
    if primary == "LOAN_PAYMENTS":
        # Unlabeled loan payment — not mortgage unless keywords already matched
        return "other"
    return "other"


def _should_exclude(txn: TransactionRecord) -> bool:
    if txn.pending:
        return True
    if txn.amount <= 0:
        return True
    if txn.pfc_primary and txn.pfc_primary.upper() in EXCLUDED_PFC_PRIMARY:
        return True
    if txn.pfc_detailed and txn.pfc_detailed.upper() in EXCLUDED_PFC_DETAILED:
        return True
    name = (txn.merchant_name or txn.name).lower()
    if _is_credit_card_payment(name, txn.pfc_detailed):
        return True
    if "transfer" in name and "payment" not in name:
        return True
    return False


def _default_included(category: str, expense_type: str, annual_amount: float) -> bool:
    """Retirement base: recurring bills + lifestyle averages; not other/school/one-offs."""
    if category in {"education", "other", "mortgage"}:
        # Mortgage is a separate plan field; other/school ignored for Base for now
        return False
    if category in {"food", "dining", "entertainment", "travel"}:
        # Lifestyle averages — include so Apply can fold them into Base
        return True
    if expense_type == "variable" and annual_amount >= ONE_OFF_EXCLUDE_ANNUAL:
        return False
    return expense_type == "recurring"


def _amounts_similar(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= AMOUNT_TOLERANCE


def _is_recurring_pattern(amounts: list[float], dates: list[date]) -> bool:
    if len(amounts) < RECURRING_MIN_OCCURRENCES:
        return False
    intervals: list[int] = []
    for idx in range(1, len(dates)):
        intervals.append((dates[idx] - dates[idx - 1]).days)
    if not intervals:
        return False
    monthly_like = sum(RECURRING_INTERVAL_DAYS[0] <= gap <= RECURRING_INTERVAL_DAYS[1] for gap in intervals)
    if monthly_like < len(intervals) * 0.6:
        return False
    reference = amounts[0]
    similar = sum(_amounts_similar(reference, amount) for amount in amounts[1:])
    return similar >= len(amounts) - 2


def _dedupe_household_merchants(items: list[ExpenseLineItem]) -> list[ExpenseLineItem]:
    """Collapse duplicate merchant lines paid from multiple accounts (joint double-count guard)."""
    grouped: dict[str, list[ExpenseLineItem]] = defaultdict(list)
    for item in items:
        grouped[item.merchant].append(item)

    deduped: list[ExpenseLineItem] = []
    for merchant, merchant_items in grouped.items():
        if len(merchant_items) == 1:
            deduped.append(merchant_items[0])
            continue
        recurring = [item for item in merchant_items if item.expense_type == "recurring"]
        pool = recurring or merchant_items
        pool.sort(key=lambda item: (item.occurrence_count, item.monthly_amount), reverse=True)
        primary = pool[0]
        duplicates = [
            item
            for item in pool[1:]
            if _amounts_similar(item.monthly_amount, primary.monthly_amount)
        ]
        if duplicates:
            deduped.append(primary)
        else:
            deduped.extend(merchant_items)
    return deduped


def _summarize_items(
    items: list[ExpenseLineItem],
    *,
    months_analyzed: float,
    transactions_used: int,
    transactions_excluded: int,
) -> ExpenseAnalysis:
    monthly_recurring = sum(
        item.monthly_amount for item in items if item.expense_type == "recurring"
    )
    monthly_variable = sum(
        item.monthly_amount for item in items if item.expense_type == "variable"
    )
    annual_household = (monthly_recurring + monthly_variable) * 12
    sorted_items = sorted(
        items,
        key=lambda item: (
            0 if item.expense_type == "recurring" else 1,
            -item.annual_amount,
            item.merchant,
        ),
    )
    return ExpenseAnalysis(
        items=sorted_items,
        monthly_recurring=monthly_recurring,
        monthly_variable=monthly_variable,
        annual_household=annual_household,
        months_analyzed=months_analyzed,
        transactions_used=transactions_used,
        transactions_excluded=transactions_excluded,
    )


def merge_expense_user_overrides(
    analysis: ExpenseAnalysis,
    prior: ExpenseAnalysis | None,
) -> ExpenseAnalysis:
    """Preserve user include / type / owner choices across a Plaid re-analyze."""
    if prior is None or not prior.items:
        return analysis
    prior_by_merchant: dict[str, ExpenseLineItem] = {}
    for item in prior.items:
        prior_by_merchant.setdefault(item.merchant, item)
    for item in analysis.items:
        prev = prior_by_merchant.get(item.merchant)
        if prev is None:
            continue
        item.included = prev.included
        if prev.expense_type in {"recurring", "variable"}:
            item.expense_type = prev.expense_type
        if prev.owner in {"self", "wife", "unassigned", "exclude"}:
            item.owner = prev.owner
        if prev.category:
            item.category = prev.category
    return analysis


def analyze_expenses(
    transactions: list[TransactionRecord],
    *,
    account_owners: dict[str, str] | None = None,
    expense_account_ids: set[str] | None = None,
    dedupe_household: bool = True,
) -> ExpenseAnalysis:
    """Build itemized recurring + variable expense estimate from transactions."""
    account_owners = account_owners or {}
    expense_txns: list[TransactionRecord] = []
    excluded = 0
    for txn in transactions:
        if expense_account_ids and txn.account_id not in expense_account_ids:
            excluded += 1
            continue
        if _should_exclude(txn):
            excluded += 1
            continue
        expense_txns.append(txn)

    if not expense_txns:
        return ExpenseAnalysis(transactions_excluded=excluded)

    dated = sorted(
        ((_parse_txn_date(txn.date), txn) for txn in expense_txns),
        key=lambda pair: pair[0],
    )
    min_date = dated[0][0]
    max_date = dated[-1][0]
    months_analyzed = max(1.0, (max_date - min_date).days / 30.44)

    by_key: dict[tuple[str, str], list[TransactionRecord]] = defaultdict(list)
    for _, txn in dated:
        merchant = _normalize_merchant(txn.merchant_name or txn.name)
        owner = account_owners.get(txn.account_id, "unassigned")
        by_key[(merchant, owner)].append(txn)

    items: list[ExpenseLineItem] = []
    variable_total = 0.0

    for (merchant, owner), txns in sorted(by_key.items()):
        amounts = [txn.amount for txn in txns]
        dates = [_parse_txn_date(txn.date) for txn in txns]
        sample = txns[0]
        category = _categorize_merchant(
            merchant,
            sample.pfc_primary,
            sample.pfc_detailed,
        )

        if _is_recurring_pattern(amounts, dates):
            monthly_amount = float(median(amounts))
            expense_type = "recurring"
        else:
            monthly_amount = sum(amounts) / months_analyzed
            expense_type = "variable"
            variable_total += sum(amounts)

        annual_amount = monthly_amount * 12
        items.append(
            ExpenseLineItem(
                merchant=merchant,
                category=category,
                expense_type=expense_type,
                monthly_amount=monthly_amount,
                annual_amount=annual_amount,
                occurrence_count=len(txns),
                owner=owner,
                included=_default_included(category, expense_type, annual_amount),
            )
        )

    if dedupe_household:
        items = _dedupe_household_merchants(items)

    return _summarize_items(
        items,
        months_analyzed=months_analyzed,
        transactions_used=len(expense_txns),
        transactions_excluded=excluded,
    )
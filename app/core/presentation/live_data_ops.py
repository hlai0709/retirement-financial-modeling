"""Live data status, review payloads, and apply helpers — no UI toolkit."""

from __future__ import annotations

from typing import Any

from app.core.planning_snapshot import PlanningSnapshot
from config.model_fields import FIELDS_BY_KEY, field_with_model_role, fields_with_data_source
from providers.csv_provider import CsvBalanceRow, owner_totals_from_rows
from security.keychain import list_item_ids
from services.data_service import (
    OWNER_ATTRIBUTION_CHOICES,
    VALID_OWNERS,
    ContributionSnapshot,
    DataService,
    ExpenseSnapshot,
    MarketSnapshot,
    RefreshSnapshot,
)
from settings import get_settings


def _format_balance(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def live_status_text() -> str:
    if not get_settings().plaid_configured:
        return "Plaid not configured."
    items = list_item_ids()
    if not items:
        return "No institutions connected — click Connect to link via Plaid."
    try:
        service = DataService()
        cached = service.load_cached_snapshot()
        expense_cached = service.load_cached_expense_snapshot()
    except Exception:
        return (
            f"{len(items)} institution(s) connected — balance cache unavailable; "
            "refresh balances to rebuild."
        )
    if cached and cached.as_of:
        status = (
            f"Last refresh: {cached.as_of}  |  "
            f"{len(items)} institution(s)  |  "
            f"self {_format_balance(cached.owner_totals.get('self', 0))}  "
            f"wife {_format_balance(cached.owner_totals.get('wife', 0))}"
        )
        if expense_cached and expense_cached.as_of:
            status += (
                f"  |  expenses {_format_balance(expense_cached.analysis.annual_household)}"
                f" ({expense_cached.as_of[:10]})"
            )
        return status
    return f"{len(items)} institution(s) connected — refresh balances to import totals."


def market_status_text(market: MarketSnapshot) -> str:
    parts = ["Market data updated."]
    if market.is_stale():
        parts.append("Warning: cached data is older than 24 hours.")
    if market.errors:
        parts.append("Issues: " + "; ".join(market.errors))
    return " ".join(parts)


def _live_data_cards() -> list[dict[str, Any]]:
    return [
        {
            "id": "accounts",
            "title": "Institutions",
            "description": "Link accounts via Plaid.",
            "actions": ["Connect Accounts", "Manage Accounts"],
            "requires_plaid": True,
        },
        {
            "id": "balances",
            "title": "Portfolio balances",
            "description": "Import current asset totals into Initial Assets.",
            "actions": ["Refresh Balances", "Import CSV"],
            "requires_plaid": False,
        },
        {
            "id": "expenses",
            "title": "Base expenses",
            "description": "Estimate annual spending from recent transactions.",
            "actions": ["Refresh Expenses"],
            "requires_plaid": True,
        },
        {
            "id": "contributions",
            "title": "Contributions",
            "description": "Detect recurring investment deposits.",
            "actions": ["Refresh Contributions"],
            "requires_plaid": True,
        },
        {
            "id": "market",
            "title": "Market assumptions",
            "description": "Refresh FRED/market inputs.",
            "actions": ["Refresh Market"],
            "requires_plaid": False,
        },
    ]


def build_live_data_payload(snapshot: PlanningSnapshot) -> dict[str, Any]:
    cards = _live_data_cards()
    connections = build_plaid_connections_payload()
    return {
        "status": live_status_text(),
        "plaid_ready": get_settings().plaid_configured,
        "cards": cards,
        "connect_text": (
            "Link your accounts to pull current balances automatically. Read-only access — we never move money."
        ),
        "institutions": connections.get("institutions") or [],
        "institutions_label": connections.get("label") or "",
        "institution_count": connections.get("count") or 0,
    }


def serialize_csv_rows(rows: list[CsvBalanceRow]) -> list[dict[str, Any]]:
    return [
        {
            "account_name": row.account_name,
            "balance": row.balance,
            "owner": row.owner,
        }
        for row in rows
    ]


def owner_totals_from_balance_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"self": 0.0, "wife": 0.0}
    for row in rows:
        owner = str(row.get("owner", ""))
        if row.get("include") and owner in totals:
            totals[owner] += float(row.get("balance", 0))
    return totals


def _count_unassigned_line_items(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("included") and str(row.get("owner", "")) == "unassigned"
    )


def owner_totals_from_line_items(
    rows: list[dict[str, Any]],
    household_mode: str,
    *,
    exclude_categories: frozenset[str] | set[str] | None = None,
    base_living_only: bool = False,
) -> dict[str, float]:
    """Sum included line items into owner totals.

    When ``base_living_only`` is True (expense → Base Expenses apply), only count:
    recurring bills (utilities/insurance/…) + food + dining + entertainment + travel.
    Mortgage, property tax, healthcare, other, and education are ignored even if checked.
    """
    from app.core.presentation.spending_builder import (
        BASE_EXPENSE_CATEGORIES,
        BASE_LIFESTYLE_CATEGORIES,
        BASE_RECURRING_CATEGORIES,
        IGNORE_FOR_BASE_CATEGORIES,
        _PLAID_TO_BUILDER,
        counts_toward_base,
    )

    skip = frozenset(exclude_categories or {"mortgage"})
    if base_living_only:
        skip = skip | IGNORE_FOR_BASE_CATEGORIES
    totals = {"self": 0.0, "wife": 0.0}
    assigned = False
    included_annual = 0.0
    for row in rows:
        if not row.get("included"):
            continue
        cat = str(row.get("category") or "").lower()
        builder_cat = _PLAID_TO_BUILDER.get(cat, cat)
        if cat in skip or builder_cat in skip:
            continue
        if base_living_only:
            expense_type = str(row.get("expense_type") or "").lower()
            in_lifestyle = cat in BASE_LIFESTYLE_CATEGORIES or builder_cat in BASE_LIFESTYLE_CATEGORIES
            in_recurring_cat = (
                cat in BASE_RECURRING_CATEGORIES or builder_cat in BASE_RECURRING_CATEGORIES
            )
            # Healthcare and property tax have their own plan fields. One-off
            # travel is not a bill. A checked insurance or utility row still
            # counts when the detector called it variable.
            if not counts_toward_base(builder_cat):
                continue
            if (builder_cat == "travel" or cat == "travel") and expense_type != "recurring":
                continue
            if in_lifestyle or in_recurring_cat:
                pass
            elif builder_cat not in BASE_EXPENSE_CATEGORIES and cat not in BASE_EXPENSE_CATEGORIES:
                continue
        annual = float(row.get("annual_amount", 0))
        included_annual += annual
        owner = str(row.get("owner", ""))
        if owner in totals:
            totals[owner] += annual
            assigned = True
    if household_mode == "joint" and not assigned:
        half = included_annual / 2
        return {"self": half, "wife": half}
    return totals


def base_living_category_breakdown(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Annual totals by base-living category (for review UI / messages)."""
    from app.core.presentation.spending_builder import (
        BASE_LIFESTYLE_CATEGORIES,
        BASE_RECURRING_CATEGORIES,
        IGNORE_FOR_BASE_CATEGORIES,
        _PLAID_TO_BUILDER,
        counts_toward_base,
    )

    by_cat: dict[str, float] = {}
    for row in rows:
        if not row.get("included"):
            continue
        cat = str(row.get("category") or "other").lower()
        builder_cat = _PLAID_TO_BUILDER.get(cat, cat)
        if builder_cat in IGNORE_FOR_BASE_CATEGORIES or cat in IGNORE_FOR_BASE_CATEGORIES:
            continue
        expense_type = str(row.get("expense_type") or "").lower()
        in_lifestyle = builder_cat in BASE_LIFESTYLE_CATEGORIES
        in_recurring_cat = builder_cat in BASE_RECURRING_CATEGORIES
        if not counts_toward_base(builder_cat):
            continue
        if builder_cat == "travel" and expense_type != "recurring":
            continue
        if in_lifestyle or in_recurring_cat:
            key = builder_cat
        else:
            continue
        by_cat[key] = by_cat.get(key, 0.0) + float(row.get("annual_amount") or 0)
    return {k: round(v, 2) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])}


def serialize_connected_institutions(
    service: DataService,
    snapshot: RefreshSnapshot | None = None,
) -> list[dict[str, Any]]:
    """Institutions with a live Plaid token (Keychain), plus account names when known."""
    config = service.load_accounts_config()
    item_ids = list_item_ids()
    institution_by_item = {inst.item_id: inst for inst in config.institutions}

    # Fall back to account_mappings for name/count when institutions[] is empty
    name_from_mappings: dict[str, str] = {}
    accounts_from_mappings: dict[str, list[str]] = {}
    for mapping in config.account_mappings:
        iid = str(mapping.item_id or "")
        if not iid:
            continue
        if mapping.institution and iid not in name_from_mappings:
            name_from_mappings[iid] = mapping.institution
        accounts_from_mappings.setdefault(iid, [])
        label = mapping.name or mapping.account_id
        if label and label not in accounts_from_mappings[iid]:
            accounts_from_mappings[iid].append(label)

    account_counts: dict[str, int] = {}
    account_names: dict[str, list[str]] = {k: list(v) for k, v in accounts_from_mappings.items()}
    if snapshot:
        for account in snapshot.accounts:
            if not account.item_id:
                continue
            account_counts[account.item_id] = account_counts.get(account.item_id, 0) + 1
            account_names.setdefault(account.item_id, [])
            if account.name and account.name not in account_names[account.item_id]:
                account_names[account.item_id].append(account.name)

    institutions: list[dict[str, Any]] = []
    for item_id in item_ids:
        record = institution_by_item.get(item_id)
        names = account_names.get(item_id) or []
        count = account_counts.get(item_id) or len(names)
        name = (
            (record.institution_name if record else "")
            or name_from_mappings.get(item_id)
            or "(unknown institution)"
        )
        institutions.append(
            {
                "item_id": item_id,
                "name": name,
                "account_count": count,
                "account_names": names,
                "connected_at": record.connected_at if record else "",
                "active": True,
            }
        )
    return institutions


def build_plaid_connections_payload() -> dict[str, Any]:
    """Summary of live Plaid links for UI status panels."""
    if not get_settings().plaid_configured:
        return {
            "plaid_ready": False,
            "count": 0,
            "institutions": [],
            "label": "Plaid not configured",
        }
    try:
        service = DataService()
        institutions = serialize_connected_institutions(service)
    except Exception:
        institutions = []
        for item_id in list_item_ids():
            institutions.append(
                {
                    "item_id": item_id,
                    "name": "(linked)",
                    "account_count": 0,
                    "account_names": [],
                    "connected_at": "",
                    "active": True,
                }
            )
    names = [str(i.get("name") or "Institution") for i in institutions]
    if not institutions:
        label = "No institutions connected"
    elif len(institutions) == 1:
        inst = institutions[0]
        accts = inst.get("account_names") or []
        extra = f" — {', '.join(accts[:4])}" if accts else ""
        label = f"Connected: {inst.get('name')}{extra}"
    else:
        label = f"{len(institutions)} institutions: " + ", ".join(names)
    return {
        "plaid_ready": True,
        "count": len(institutions),
        "institutions": institutions,
        "label": label,
    }


def disconnect_plaid_institution(service: DataService, item_id: str) -> dict[str, Any]:
    from security.keychain import delete_token

    item_id = item_id.strip()
    if not item_id:
        return {"ok": False, "message": "item_id is required"}

    config = service.load_accounts_config()
    record = next((inst for inst in config.institutions if inst.item_id == item_id), None)
    name = record.institution_name if record else item_id[:8]

    warning = ""
    try:
        service.disconnect_item(item_id)
    except Exception as exc:
        delete_token(item_id)
        config = service.load_accounts_config()
        config.institutions = [inst for inst in config.institutions if inst.item_id != item_id]
        config.account_mappings = [
            mapping for mapping in config.account_mappings if mapping.item_id != item_id
        ]
        service.save_accounts_config(config)
        warning = f" Plaid API error ({exc}); removed locally."

    return {
        "ok": True,
        "message": f"Disconnected {name}.{warning}",
        "status": live_status_text(),
    }


def serialize_balance_review(
    snapshot: RefreshSnapshot,
    *,
    mode: str,
    service: DataService | None = None,
) -> dict[str, Any]:
    mapping_only = mode == "manage"
    if mapping_only:
        if snapshot.as_of:
            header = (
                "Assign each account to self, wife, or exclude. "
                f"Balances shown are from the last refresh ({snapshot.as_of[:19]}). "
                "Use Refresh Balances for current amounts."
            )
        else:
            header = (
                "Assign each account to self, wife, or exclude. "
                "Balances are not cached yet — refresh balances after mapping."
            )
    else:
        header = "Confirm balances and owner mapping before updating Initial Assets."

    rows = []
    for account in snapshot.accounts:
        role = classify_plaid_account_role(
            type_=account.type,
            subtype=account.subtype,
            name=account.name,
        )
        # Smart default: credit/loans off assets; HSA/investments/cash on
        include = account.include_in_assets
        if role["bucket"] == "skip":
            include = False
        rows.append(
            {
                "account_id": account.account_id,
                "institution": account.institution,
                "account": account.name,
                "balance": account.balance,
                "owner": account.owner,
                "include": include,
                "type": account.type,
                "subtype": account.subtype,
                "bucket": role["bucket"],
                "role_label": role["role_label"],
                "is_spending": role["is_spending"],
                "is_asset": role["is_asset"],
            }
        )
    totals = owner_totals_from_balance_rows(rows)
    buckets = owner_bucket_totals_from_balance_rows(rows)
    payload: dict[str, Any] = {
        "mode": mode,
        "header": header,
        "as_of": snapshot.as_of,
        "rows": rows,
        "owner_choices": sorted(VALID_OWNERS),
        "totals": totals,
        "buckets": buckets,
        "totals_label": (
            f"Owner totals — self {_format_balance(totals.get('self', 0))}  "
            f"wife {_format_balance(totals.get('wife', 0))}"
        ),
        "bucket_hint": (
            "Smart roles: credit cards → spending only (not assets). "
            "HSA → traditional assets (money you own). "
            "IRA/401k/PCRA → traditional; Roth* → Roth; brokerage/cash → taxable. "
            "Apply balances to fill Initial Assets + those buckets."
        ),
        "errors": list(snapshot.errors),
        "allow_apply_assets": not mapping_only,
    }
    if mapping_only and service is not None:
        payload["institutions"] = serialize_connected_institutions(service, snapshot)
        payload["institutions_note"] = (
            "Disconnect revokes Plaid access and removes local tokens. "
            "On a Trial plan, disconnected slots may not free up for new links."
        )
    return payload


def serialize_expense_review(snapshot: ExpenseSnapshot, household_mode: str) -> dict[str, Any]:
    from providers.expense_analyzer import EXPENSE_CATEGORIES

    analysis = snapshot.analysis
    rows = [
        {
            "merchant": item.merchant,
            "owner": item.owner,
            "category": item.category,
            "expense_type": item.expense_type,
            "monthly_amount": item.monthly_amount,
            "annual_amount": item.annual_amount,
            "occurrence_count": item.occurrence_count,
            "included": item.included,
        }
        for item in analysis.items
    ]
    totals = owner_totals_from_line_items(rows, household_mode, base_living_only=True)
    raw_checked = owner_totals_from_line_items(rows, household_mode, base_living_only=False)
    breakdown = base_living_category_breakdown(rows)
    unassigned = _count_unassigned_line_items(rows)
    recurring_n = sum(1 for r in rows if r.get("expense_type") == "recurring")
    one_off_n = sum(1 for r in rows if r.get("expense_type") != "recurring")
    excluded_n = sum(1 for r in rows if not r.get("included"))
    breakdown_bits = ", ".join(
        f"{k} {_format_balance(v)}" for k, v in list(breakdown.items())[:6]
    ) or "none yet"
    return {
        "header": (
            f"Analyzed {analysis.transactions_used} transactions over "
            f"{analysis.months_analyzed:.1f} months. "
            f"{recurring_n} recurring · {one_off_n} variable/one-off "
            f"({excluded_n} unchecked — credit-card payments omitted). "
            "Base Expenses = recurring bills + food + dining + entertainment + travel. "
            "Property tax is kept separate, as are healthcare, mortgage, other, and education."
        ),
        "as_of": snapshot.as_of,
        "rows": rows,
        "category_choices": list(EXPENSE_CATEGORIES),
        "owner_choices": list(OWNER_ATTRIBUTION_CHOICES),
        "totals": totals,
        "raw_checked_totals": raw_checked,
        "base_breakdown": breakdown,
        "totals_label": (
            f"Base living (Apply uses this) — self {_format_balance(totals.get('self', 0))}  "
            f"wife {_format_balance(totals.get('wife', 0))} "
            f"· {breakdown_bits}. "
            f"All checked (not used for Base): self {_format_balance(raw_checked.get('self', 0))}."
        ),
        "unassigned_count": unassigned,
        "unassigned_warning": (
            f"{unassigned} included line(s) still unassigned — assign owners or they will be excluded."
            if unassigned
            else ""
        ),
        "errors": list(snapshot.errors),
    }


def serialize_contribution_review(snapshot: ContributionSnapshot, household_mode: str) -> dict[str, Any]:
    analysis = snapshot.analysis
    rows = [
        {
            "merchant": item.merchant,
            "owner": item.owner,
            "source_type": item.source_type,
            "monthly_amount": item.monthly_amount,
            "annual_amount": item.annual_amount,
            "occurrence_count": item.occurrence_count,
            "included": item.included,
        }
        for item in analysis.items
    ]
    totals = owner_totals_from_line_items(rows, household_mode)
    unassigned = _count_unassigned_line_items(rows)
    return {
        "header": (
            f"Detected {analysis.transactions_used} contribution deposits over "
            f"{analysis.months_analyzed:.1f} months. "
            f"Estimated annual household: {_format_balance(analysis.annual_household)}"
        ),
        "as_of": snapshot.as_of,
        "rows": rows,
        "owner_choices": list(OWNER_ATTRIBUTION_CHOICES),
        "totals": totals,
        "totals_label": (
            f"Selected annual — self {_format_balance(totals.get('self', 0))}  "
            f"wife {_format_balance(totals.get('wife', 0))}"
        ),
        "unassigned_count": unassigned,
        "unassigned_warning": (
            f"{unassigned} included line(s) still unassigned — assign owners or they will be excluded."
            if unassigned
            else ""
        ),
        "errors": list(snapshot.errors),
    }


def classify_plaid_asset_bucket(
    *,
    type_: str | None,
    subtype: str | None,
    name: str | None = None,
) -> str:
    """Map a Plaid account into taxable | traditional | roth | skip.

    - Credit cards / loans → skip (liabilities; used for spending analysis instead)
    - HSA → traditional (pre-tax money you own; user preference)
    - IRA / 401k / PCRA → traditional
    - Roth* → roth
    - Brokerage / checking / savings / crypto → taxable
    """
    typ = str(type_ or "").strip().lower()
    sub = str(subtype or "").strip().lower()
    nam = str(name or "").strip().lower()

    if (
        typ in {"credit", "loan"}
        or "credit card" in sub
        or sub in {"paypal", "line of credit", "mortgage", "student", "auto"}
        or "credit card" in nam
    ):
        return "skip"

    # Health savings accounts — assets you own (pre-tax contributions)
    if sub == "hsa" or " hsa" in f" {nam}" or nam.startswith("hsa") or "health savings" in nam:
        return "traditional"

    if "roth" in sub or "roth" in nam:
        return "roth"

    traditional_hints = (
        "ira",
        "401k",
        "401(k)",
        "403b",
        "403(b)",
        "457",
        "sep",
        "simple",
        "pension",
        "retirement",
        "keogh",
        "profit sharing",
        "pcra",  # Schwab PCRA / 401(k) window often typed as ira
    )
    if any(h in sub or h in nam for h in traditional_hints):
        return "traditional"

    if typ in {"investment", "brokerage", "depository"} or sub in {
        "brokerage",
        "mutual fund",
        "crypto exchange",
        "checking",
        "savings",
        "money market",
        "cd",
        "cash management",
    }:
        return "taxable"
    if typ in {"unknown", ""} and not sub:
        return "taxable"
    return "taxable"


def classify_plaid_account_role(
    *,
    type_: str | None,
    subtype: str | None,
    name: str | None = None,
) -> dict[str, Any]:
    """Human-facing role flags for the balance review UI."""
    typ = str(type_ or "").strip().lower()
    sub = str(subtype or "").strip().lower()
    bucket = classify_plaid_asset_bucket(type_=type_, subtype=subtype, name=name)
    is_credit = typ == "credit" or "credit card" in sub or "credit card" in str(name or "").lower()
    is_spending = is_credit or typ == "depository" or sub in {"checking", "savings", "paypal"}
    is_asset = bucket != "skip"
    return {
        "bucket": bucket,
        "is_asset": is_asset,
        "is_spending": is_spending,
        "role_label": (
            "Spending (credit)"
            if is_credit
            else (
                "Spending + cash"
                if typ == "depository"
                else (
                    f"Asset · {bucket}"
                    if is_asset
                    else "Skip"
                )
            )
        ),
    }


def owner_bucket_totals_from_balance_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per-owner totals split by tax bucket from balance-review rows."""
    empty = {"taxable": 0.0, "traditional": 0.0, "roth": 0.0, "total": 0.0}
    out: dict[str, dict[str, float]] = {
        "self": dict(empty),
        "wife": dict(empty),
    }
    for row in rows:
        if not row.get("include"):
            continue
        owner = str(row.get("owner") or "")
        if owner not in out:
            continue
        try:
            balance = float(row.get("balance") or 0)
        except (TypeError, ValueError):
            continue
        if balance == 0:
            continue
        bucket = str(row.get("bucket") or "").strip().lower()
        if not bucket or bucket == "skip":
            bucket = classify_plaid_asset_bucket(
                type_=str(row.get("type") or ""),
                subtype=str(row.get("subtype") or ""),
                name=str(row.get("account") or row.get("name") or ""),
            )
        if bucket == "skip":
            continue
        if bucket not in {"taxable", "traditional", "roth"}:
            bucket = "taxable"
        out[owner][bucket] += balance
        out[owner]["total"] += balance
    return out


def apply_owner_totals(snapshot: PlanningSnapshot, totals: dict[str, float]) -> None:
    asset_field = next(
        (f for f in fields_with_data_source("plaid", scope="person") if f.key == "initial_assets"),
        None,
    )
    if asset_field is None:
        return
    for person in ("self", "wife"):
        value = totals.get(person, 0.0)
        if value:
            snapshot.set_raw(asset_field, str(value), person=person)
            snapshot.set_source(person, asset_field.key, "Plaid balances")


def apply_owner_asset_buckets(
    snapshot: PlanningSnapshot,
    buckets: dict[str, dict[str, float]],
) -> None:
    """Write Plaid totals into taxable / traditional IRA·401k / Roth buckets."""
    from config.model_fields import FIELDS_BY_KEY

    field_map = {
        "taxable": "initial_taxable_assets",
        "traditional": "initial_traditional_ira",
        "roth": "initial_roth_ira",
    }
    for person in ("self", "wife"):
        person_buckets = buckets.get(person) or {}
        total = float(person_buckets.get("total") or 0)
        if total <= 0:
            continue
        for bucket_key, field_key in field_map.items():
            field = FIELDS_BY_KEY.get(field_key)
            if field is None:
                continue
            amount = float(person_buckets.get(bucket_key) or 0)
            snapshot.set_raw(field, str(amount), person=person)
            snapshot.set_source(person, field_key, f"Plaid balances ({bucket_key})")


def apply_owner_expenses(
    snapshot: PlanningSnapshot,
    totals: dict[str, float],
    *,
    expense_snapshot: ExpenseSnapshot | None = None,
) -> None:
    expense_field = field_with_model_role("base_expense")
    if expense_field is None:
        return
    source = (
        f"Plaid transactions ({expense_snapshot.as_of[:10]})"
        if expense_snapshot and expense_snapshot.as_of
        else "Plaid transactions"
    )
    for person in ("self", "wife"):
        value = totals.get(person, 0.0)
        if value:
            snapshot.set_raw(expense_field, str(value), person=person)
            snapshot.set_source(person, expense_field.key, source)
        else:
            snapshot.set_source(person, expense_field.key, "Manual")


def apply_owner_contributions(
    snapshot: PlanningSnapshot,
    totals: dict[str, float],
    *,
    as_of: str = "",
) -> None:
    field = FIELDS_BY_KEY.get("annual_contributions")
    if field is None:
        return
    source = f"Plaid deposits ({as_of[:10]})" if as_of else "Plaid deposits"
    for person in ("self", "wife"):
        value = totals.get(person, 0.0)
        if value:
            snapshot.set_raw(field, str(value), person=person)
            snapshot.set_source(person, field.key, source)


def apply_market_snapshot(snapshot: PlanningSnapshot, market: MarketSnapshot) -> None:
    for field in fields_with_data_source("market", scope="shared"):
        attr = field.market_snapshot_attribute()
        if not attr:
            continue
        market_field = getattr(market, attr, None)
        if market_field is None:
            continue
        snapshot.set_raw(field, str(market_field.value))
        snapshot.set_source(None, field.key, market_field.display_label())


def apply_balance_review(
    planning: PlanningSnapshot,
    service: DataService,
    rows: list[dict[str, Any]],
    *,
    apply_assets: bool,
) -> dict[str, Any]:
    updates = [
        (str(row["account_id"]), str(row.get("owner", "unassigned")), bool(row.get("include")))
        for row in rows
    ]
    service.apply_account_mapping_review(updates)
    totals = owner_totals_from_balance_rows(rows)
    buckets = owner_bucket_totals_from_balance_rows(rows)
    if apply_assets:
        apply_owner_totals(planning, totals)
        apply_owner_asset_buckets(planning, buckets)
        self_b = buckets.get("self") or {}
        message = (
            f"Assets updated — self {_format_balance(totals.get('self', 0))} "
            f"(taxable {_format_balance(self_b.get('taxable', 0))}, "
            f"trad/401k {_format_balance(self_b.get('traditional', 0))}, "
            f"Roth {_format_balance(self_b.get('roth', 0))}); "
            f"wife {_format_balance(totals.get('wife', 0))}"
        )
    else:
        message = "Account mappings saved."
    return {
        "ok": True,
        "message": message,
        "totals": totals,
        "buckets": buckets,
        "status": live_status_text(),
    }


def totals_from_import_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    parsed = [
        CsvBalanceRow(
            account_name=str(row["account_name"]),
            balance=float(row["balance"]),
            owner=str(row.get("owner", "self")),
        )
        for row in rows
    ]
    return owner_totals_from_rows(parsed)
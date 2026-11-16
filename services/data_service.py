"""
Orchestrates live data refresh: Plaid → account mapping → owner totals → encrypted vault.

Phase 2 adds market/macro data (yfinance, FRED) cached alongside balances in the vault.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from providers.contribution_analyzer import ContributionAnalysis, analyze_contributions
from providers.csv_provider import CsvBalanceRow, owner_totals_from_rows, parse_balance_csv
from providers.expense_analyzer import ExpenseAnalysis, ExpenseLineItem, analyze_expenses
from providers.fred_provider import get_cpi_yoy
from providers.market_data import get_annualized_return, get_current_price, get_sp500_10yr_cagr
from providers.plaid_provider import (
    AccountSnapshot,
    HoldingSnapshot,
    PlaidProvider,
    TransactionRecord,
)
from security.keychain import list_item_ids
from security.vault import Vault
from settings import (
    accounts_path,
    ensure_app_data_dir,
    get_settings,
    refresh_log_path,
    transaction_cursors_path,
)

logger = logging.getLogger(__name__)

FILE_MODE = 0o600
VAULT_VERSION = 1
MARKET_STALE_HOURS = 24
TSLA_SYMBOL = "TSLA"

VALID_OWNERS = frozenset({"self", "wife", "exclude", "unassigned"})
OWNER_ATTRIBUTION_CHOICES = ("self", "wife", "unassigned")


@dataclass
class AccountMapping:
    account_id: str
    institution: str
    name: str
    owner: str = "unassigned"
    include_in_assets: bool = True
    item_id: str | None = None


@dataclass
class InstitutionRecord:
    item_id: str
    institution_name: str
    connected_at: str


@dataclass
class AccountsConfig:
    institutions: list[InstitutionRecord] = field(default_factory=list)
    account_mappings: list[AccountMapping] = field(default_factory=list)


@dataclass
class MappedAccount:
    account_id: str
    name: str
    institution: str
    type: str
    subtype: str | None
    mask: str | None
    balance: float
    currency: str
    balance_source: str
    owner: str
    include_in_assets: bool
    item_id: str | None = None


@dataclass
class RefreshSnapshot:
    as_of: str
    accounts: list[MappedAccount]
    owner_totals: dict[str, float]
    items_refreshed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_vault_dict(self) -> dict[str, Any]:
        return {
            "version": VAULT_VERSION,
            "as_of": self.as_of,
            "accounts": [asdict(account) for account in self.accounts],
            "owner_totals": self.owner_totals,
            "items_refreshed": self.items_refreshed,
            "errors": self.errors,
        }

    @classmethod
    def from_vault_dict(cls, data: dict[str, Any]) -> RefreshSnapshot:
        accounts = [
            MappedAccount(**account)
            for account in data.get("accounts", [])
        ]
        return cls(
            as_of=data.get("as_of", ""),
            accounts=accounts,
            owner_totals=dict(data.get("owner_totals", {})),
            items_refreshed=list(data.get("items_refreshed", [])),
            errors=list(data.get("errors", [])),
        )


@dataclass
class MarketField:
    value: float
    source: str
    source_detail: str
    as_of: str
    confidence: str = "live"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketField:
        return cls(
            value=float(data["value"]),
            source=data.get("source", "unknown"),
            source_detail=data.get("source_detail", ""),
            as_of=data.get("as_of", ""),
            confidence=data.get("confidence", "cached"),
        )

    def display_label(self) -> str:
        as_of = self.as_of[:10] if self.as_of else "unknown date"
        prefix = self.confidence.capitalize()
        detail = f" ({self.source_detail})" if self.source_detail else ""
        return f"{prefix} ({self.source}, {as_of}){detail}"


@dataclass
class MarketSnapshot:
    as_of: str
    tsla_price: MarketField | None = None
    inflation_rate: MarketField | None = None
    investment_return_mean: MarketField | None = None
    errors: list[str] = field(default_factory=list)

    def to_vault_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": VAULT_VERSION,
            "as_of": self.as_of,
            "errors": self.errors,
        }
        for name in ("tsla_price", "inflation_rate", "investment_return_mean"):
            field_value = getattr(self, name)
            if field_value is not None:
                payload[name] = field_value.to_dict()
        return payload

    @classmethod
    def from_vault_dict(cls, data: dict[str, Any]) -> MarketSnapshot:
        fields = {}
        for name in ("tsla_price", "inflation_rate", "investment_return_mean"):
            raw = data.get(name)
            fields[name] = MarketField.from_dict(raw) if raw else None
        return cls(
            as_of=data.get("as_of", ""),
            errors=list(data.get("errors", [])),
            **fields,
        )

    def is_stale(self, *, now: datetime | None = None) -> bool:
        if not self.as_of:
            return True
        try:
            as_of_dt = datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))
        except ValueError:
            return True
        if as_of_dt.tzinfo is None:
            as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return current - as_of_dt > timedelta(hours=MARKET_STALE_HOURS)


@dataclass
class ContributionSnapshot:
    as_of: str
    analysis: ContributionAnalysis
    items_refreshed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_vault_dict(self) -> dict[str, Any]:
        return {
            "version": VAULT_VERSION,
            "as_of": self.as_of,
            "analysis": self.analysis.to_dict(),
            "items_refreshed": self.items_refreshed,
            "errors": self.errors,
        }

    @classmethod
    def from_vault_dict(cls, data: dict[str, Any]) -> ContributionSnapshot:
        return cls(
            as_of=data.get("as_of", ""),
            analysis=ContributionAnalysis.from_dict(data.get("analysis", {})),
            items_refreshed=list(data.get("items_refreshed", [])),
            errors=list(data.get("errors", [])),
        )


@dataclass
class ExpenseSnapshot:
    as_of: str
    analysis: ExpenseAnalysis
    items_refreshed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_vault_dict(self) -> dict[str, Any]:
        return {
            "version": VAULT_VERSION,
            "as_of": self.as_of,
            "analysis": self.analysis.to_dict(),
            "items_refreshed": self.items_refreshed,
            "errors": self.errors,
        }

    @classmethod
    def from_vault_dict(cls, data: dict[str, Any]) -> ExpenseSnapshot:
        return cls(
            as_of=data.get("as_of", ""),
            analysis=ExpenseAnalysis.from_dict(data.get("analysis", {})),
            items_refreshed=list(data.get("items_refreshed", [])),
            errors=list(data.get("errors", [])),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _set_file_mode(path: Path) -> None:
    if path.exists():
        os.chmod(path, FILE_MODE)


class DataService:
    """Coordinates Plaid refresh, account mapping, aggregation, and vault persistence."""

    def __init__(
        self,
        plaid: PlaidProvider | None = None,
        vault: Vault | None = None,
        accounts_file: Path | None = None,
    ) -> None:
        self._plaid = plaid
        self.vault = vault or Vault()
        self.accounts_file = accounts_file or accounts_path()

    @property
    def plaid(self) -> PlaidProvider:
        if self._plaid is None:
            self._plaid = PlaidProvider()
        return self._plaid

    def load_accounts_config(self) -> AccountsConfig:
        ensure_app_data_dir()
        if not self.accounts_file.exists():
            return AccountsConfig()

        data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        institutions = [
            InstitutionRecord(**item) for item in data.get("institutions", [])
        ]
        mappings = [
            AccountMapping(**item) for item in data.get("account_mappings", [])
        ]
        return AccountsConfig(institutions=institutions, account_mappings=mappings)

    def save_accounts_config(self, config: AccountsConfig) -> None:
        ensure_app_data_dir()
        payload = {
            "institutions": [asdict(item) for item in config.institutions],
            "account_mappings": [asdict(item) for item in config.account_mappings],
        }
        self.accounts_file.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _set_file_mode(self.accounts_file)

    def register_institution(self, item_id: str, institution_name: str) -> AccountsConfig:
        config = self.load_accounts_config()
        existing = {inst.item_id for inst in config.institutions}
        if item_id not in existing:
            config.institutions.append(
                InstitutionRecord(
                    item_id=item_id,
                    institution_name=institution_name,
                    connected_at=_utc_now_iso(),
                )
            )
            self.save_accounts_config(config)
        return config

    def _mapping_index(self, config: AccountsConfig) -> dict[str, AccountMapping]:
        return {mapping.account_id: mapping for mapping in config.account_mappings}

    def sync_account_mappings(self, item_id: str) -> AccountsConfig:
        """Add newly discovered Plaid accounts to ``accounts.json`` for owner mapping."""
        config = self.load_accounts_config()
        mapping_by_id = self._mapping_index(config)
        snapshots = self.plaid.fetch_account_snapshots(item_id)

        item_info = self.plaid.get_item(item_id)
        institution_name = item_info.get("item", {}).get("institution_name", "Unknown")

        self.register_institution(item_id, institution_name)

        for snapshot in snapshots:
            if snapshot.account_id in mapping_by_id:
                # Keep mappings in sync with smarter asset defaults for credit vs HSA
                existing = mapping_by_id[snapshot.account_id]
                if existing.name != snapshot.name and snapshot.name:
                    existing.name = snapshot.name
                continue
            config.account_mappings.append(
                AccountMapping(
                    account_id=snapshot.account_id,
                    institution=institution_name,
                    name=snapshot.name,
                    owner="unassigned",
                    include_in_assets=snapshot.include_in_assets,
                    item_id=item_id,
                )
            )

        self.save_accounts_config(config)
        return config

    def _apply_mapping(
        self,
        snapshot: AccountSnapshot,
        mapping: AccountMapping | None,
    ) -> MappedAccount:
        institution = snapshot.institution_name or (mapping.institution if mapping else "Unknown")
        owner = mapping.owner if mapping else "unassigned"
        include = mapping.include_in_assets if mapping else snapshot.include_in_assets

        if owner not in VALID_OWNERS:
            owner = "unassigned"

        if owner == "exclude":
            include = False

        return MappedAccount(
            account_id=snapshot.account_id,
            name=snapshot.name,
            institution=institution,
            type=snapshot.type,
            subtype=snapshot.subtype,
            mask=snapshot.mask,
            balance=snapshot.balance,
            currency=snapshot.currency,
            balance_source=snapshot.balance_source,
            owner=owner,
            include_in_assets=include and snapshot.include_in_assets,
            item_id=snapshot.item_id,
        )

    def aggregate_owner_totals(self, accounts: list[MappedAccount]) -> dict[str, float]:
        totals: dict[str, float] = {"self": 0.0, "wife": 0.0}
        for account in accounts:
            if not account.include_in_assets:
                continue
            if account.owner not in totals:
                continue
            totals[account.owner] += account.balance
        return totals

    def refresh_live_data(self, *, item_ids: list[str] | None = None) -> RefreshSnapshot:
        """
        Fetch balances for all connected Items, apply mappings, aggregate by owner,
        persist to vault, and append refresh log.
        """
        config = self.load_accounts_config()
        mapping_by_id = self._mapping_index(config)

        target_items = item_ids if item_ids is not None else list_item_ids()
        mapped_accounts: list[MappedAccount] = []
        errors: list[str] = []
        refreshed: list[str] = []

        for item_id in target_items:
            try:
                snapshots = self.plaid.fetch_account_snapshots(item_id)
                refreshed.append(item_id)
                for snapshot in snapshots:
                    mapping = mapping_by_id.get(snapshot.account_id)
                    mapped_accounts.append(self._apply_mapping(snapshot, mapping))
            except Exception as exc:
                message = f"item {item_id}: {exc}"
                logger.exception("Failed to refresh item %s", item_id)
                errors.append(message)

        owner_totals = self.aggregate_owner_totals(mapped_accounts)
        snapshot = RefreshSnapshot(
            as_of=_utc_now_iso(),
            accounts=mapped_accounts,
            owner_totals=owner_totals,
            items_refreshed=refreshed,
            errors=errors,
        )

        self.save_snapshot(snapshot)
        self._append_refresh_log(snapshot)
        return snapshot

    def _load_vault_data(self) -> dict[str, Any]:
        return self.vault.load() or {}

    def save_snapshot(self, snapshot: RefreshSnapshot) -> None:
        data = self._load_vault_data()
        data["balances"] = snapshot.to_vault_dict()
        self.vault.save(data)

    def load_cached_snapshot(self) -> RefreshSnapshot | None:
        data = self._load_vault_data()
        if not data:
            return None
        balances = data.get("balances")
        if balances:
            return RefreshSnapshot.from_vault_dict(balances)
        if "accounts" in data:
            return RefreshSnapshot.from_vault_dict(data)
        return None

    def _mapped_account_from_config(
        self,
        mapping: AccountMapping,
        cached: MappedAccount | None = None,
    ) -> MappedAccount:
        if cached is not None:
            return MappedAccount(
                account_id=mapping.account_id,
                name=mapping.name or cached.name,
                institution=mapping.institution or cached.institution,
                type=cached.type,
                subtype=cached.subtype,
                mask=cached.mask,
                balance=cached.balance,
                currency=cached.currency,
                balance_source=cached.balance_source,
                owner=mapping.owner,
                include_in_assets=mapping.include_in_assets,
                item_id=mapping.item_id or cached.item_id,
            )
        return MappedAccount(
            account_id=mapping.account_id,
            name=mapping.name,
            institution=mapping.institution,
            type="unknown",
            subtype=None,
            mask=None,
            balance=0.0,
            currency="USD",
            balance_source="mapping_config",
            owner=mapping.owner,
            include_in_assets=mapping.include_in_assets,
            item_id=mapping.item_id,
        )

    def snapshot_for_account_mapping(self) -> RefreshSnapshot:
        """
        Build an account-mapping review snapshot without calling Plaid.

        Uses ``accounts.json`` as the source of truth for owner/include flags and
        overlays cached vault balances when available.
        """
        config = self.load_accounts_config()
        if not config.account_mappings:
            raise ValueError(
                "No mapped accounts yet. Connect an institution first — "
                "accounts are added when Plaid Link completes."
            )

        cached = self.load_cached_snapshot()
        cached_by_id = {account.account_id: account for account in cached.accounts} if cached else {}
        accounts = [
            self._mapped_account_from_config(mapping, cached_by_id.get(mapping.account_id))
            for mapping in config.account_mappings
        ]
        return RefreshSnapshot(
            as_of=cached.as_of if cached else "",
            accounts=accounts,
            owner_totals=self.aggregate_owner_totals(accounts),
            items_refreshed=list(cached.items_refreshed) if cached else [],
            errors=[],
        )

    def apply_account_mapping_review(
        self,
        updates: list[tuple[str, str, bool]],
    ) -> RefreshSnapshot:
        """Persist owner/include edits from the mapping UI and refresh cached totals."""
        for account_id, owner, include_in_assets in updates:
            self.update_account_mapping(
                account_id,
                owner=owner,
                include_in_assets=include_in_assets,
            )
        snapshot = self.snapshot_for_account_mapping()
        if self.load_cached_snapshot() is not None or any(account.balance for account in snapshot.accounts):
            self.save_snapshot(snapshot)
        return snapshot

    def save_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        data = self._load_vault_data()
        data["market"] = snapshot.to_vault_dict()
        self.vault.save(data)

    def load_cached_market_snapshot(self) -> MarketSnapshot | None:
        data = self._load_vault_data()
        market = data.get("market")
        if not market:
            return None
        return MarketSnapshot.from_vault_dict(market)

    def _collect_holdings(self, item_ids: list[str]) -> list[HoldingSnapshot]:
        holdings: list[HoldingSnapshot] = []
        for item_id in item_ids:
            try:
                holdings.extend(self.plaid.fetch_holdings(item_id))
            except Exception as exc:
                logger.warning("Holdings fetch failed for %s: %s", item_id, exc)
        return [holding for holding in holdings if holding.value > 0]

    def get_portfolio_holdings(self) -> list[HoldingSnapshot]:
        """Return current Plaid investment holdings for allocation analysis."""
        item_ids = list_item_ids()
        if not item_ids:
            return []
        return self._collect_holdings(item_ids)

    def account_owner_map(self) -> dict[str, str]:
        config = self.load_accounts_config()
        return {
            mapping.account_id: mapping.owner
            for mapping in config.account_mappings
            if mapping.owner not in {"exclude", "unassigned"}
        }

    def expense_account_ids(self) -> set[str] | None:
        """Account IDs eligible for expense analysis (depository + credit, mapped owners)."""
        cached = self.load_cached_snapshot()
        if not cached:
            return None
        ids = {
            account.account_id
            for account in cached.accounts
            if account.type in ("depository", "credit")
            and account.owner not in {"exclude", "unassigned"}
        }
        return ids or None

    def clear_cached_data(self, *, include_transaction_cursors: bool = True) -> None:
        """Remove encrypted vault cache and optional transaction sync cursors."""
        self.vault.clear()
        if include_transaction_cursors:
            path = transaction_cursors_path()
            if path.exists():
                path.unlink()

    def _portfolio_weighted_return(self, holdings: list[HoldingSnapshot]) -> tuple[float, str]:
        total_value = sum(holding.value for holding in holdings)
        if total_value <= 0:
            sp500 = get_sp500_10yr_cagr()
            return sp500, "S&P 500 10yr CAGR (no Plaid holdings)"

        weighted = 0.0
        covered = 0.0
        tickers_used: list[str] = []
        for holding in holdings:
            if not holding.ticker:
                continue
            try:
                ticker_return = get_annualized_return(holding.ticker)
            except Exception as exc:
                logger.info("Return lookup failed for %s: %s", holding.ticker, exc)
                continue
            weight = holding.value / total_value
            weighted += weight * ticker_return
            covered += weight
            tickers_used.append(holding.ticker)

        if covered < 0.5:
            sp500 = get_sp500_10yr_cagr()
            return sp500, "S&P 500 10yr CAGR (insufficient ticker coverage)"

        detail = f"Plaid holdings weighted ({', '.join(sorted(set(tickers_used)))})"
        if covered < 0.99:
            detail += f"; {covered:.0%} of portfolio valued"
        return weighted, detail

    def refresh_market_data(self, *, use_cache_on_error: bool = True) -> MarketSnapshot:
        """Fetch TSLA price, CPI YoY inflation, and portfolio-weighted return."""
        as_of = _utc_now_iso()
        errors: list[str] = []
        fields: dict[str, MarketField | None] = {
            "tsla_price": None,
            "inflation_rate": None,
            "investment_return_mean": None,
        }

        try:
            tsla = get_current_price(TSLA_SYMBOL)
            fields["tsla_price"] = MarketField(
                value=tsla,
                source="yfinance",
                source_detail=TSLA_SYMBOL,
                as_of=as_of,
                confidence="live",
            )
        except Exception as exc:
            errors.append(f"TSLA price: {exc}")

        settings = get_settings()
        if settings.fred_api_key:
            try:
                cpi_yoy, obs_date = get_cpi_yoy(settings.fred_api_key)
                fields["inflation_rate"] = MarketField(
                    value=cpi_yoy,
                    source="fred",
                    source_detail=f"CPIAUCSL YoY ({obs_date})",
                    as_of=as_of,
                    confidence="live",
                )
            except Exception as exc:
                errors.append(f"Inflation (FRED): {exc}")
        else:
            errors.append(
                "Inflation (FRED): add a real FRED_API_KEY to .env "
                "(free at fred.stlouisfed.org/docs/api/api_key.html)"
            )

        try:
            item_ids = list_item_ids()
            holdings = self._collect_holdings(item_ids)
            portfolio_return, detail = self._portfolio_weighted_return(holdings)
            fields["investment_return_mean"] = MarketField(
                value=portfolio_return,
                source="yfinance",
                source_detail=detail,
                as_of=as_of,
                confidence="live",
            )
        except Exception as exc:
            errors.append(f"Portfolio return: {exc}")

        snapshot = MarketSnapshot(
            as_of=as_of,
            tsla_price=fields["tsla_price"],
            inflation_rate=fields["inflation_rate"],
            investment_return_mean=fields["investment_return_mean"],
            errors=errors,
        )

        if snapshot.tsla_price or snapshot.inflation_rate or snapshot.investment_return_mean:
            self.save_market_snapshot(snapshot)
            return snapshot

        cached = self.load_cached_market_snapshot()
        if use_cache_on_error and cached:
            cached.errors = errors + ["Using cached market data"]
            for field_name in ("tsla_price", "inflation_rate", "investment_return_mean"):
                field_value = getattr(cached, field_name)
                if field_value is not None:
                    field_value.confidence = "cached"
            return cached

        return snapshot

    def _load_transaction_cursors(self) -> dict[str, str]:
        path = transaction_cursors_path()
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_transaction_cursors(self, cursors: dict[str, str]) -> None:
        ensure_app_data_dir()
        path = transaction_cursors_path()
        path.write_text(json.dumps(cursors, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _set_file_mode(path)

    def _load_transaction_cache(self) -> dict[str, dict[str, Any]]:
        return dict(self._load_vault_data().get("transactions", {}))

    def _save_transaction_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        data = self._load_vault_data()
        data["transactions"] = cache
        self.vault.save(data)

    def _transaction_payload(self, record: TransactionRecord) -> dict[str, Any]:
        payload = asdict(record)
        payload["date"] = PlaidProvider._coerce_date(payload.get("date"))
        return payload

    def _merge_transactions(self, records: list[TransactionRecord]) -> list[TransactionRecord]:
        cache = self._load_transaction_cache()
        for record in records:
            cache[record.transaction_id] = self._transaction_payload(record)
        self._save_transaction_cache(cache)
        return [
            TransactionRecord(
                **{
                    **payload,
                    "date": PlaidProvider._coerce_date(payload.get("date")),
                }
            )
            for payload in cache.values()
            if payload.get("transaction_id")
        ]

    def save_expense_snapshot(self, snapshot: ExpenseSnapshot) -> None:
        data = self._load_vault_data()
        data["expenses"] = snapshot.to_vault_dict()
        self.vault.save(data)

    def save_contribution_snapshot(self, snapshot: ContributionSnapshot) -> None:
        data = self._load_vault_data()
        data["contributions"] = snapshot.to_vault_dict()
        self.vault.save(data)

    def load_cached_contribution_snapshot(self) -> ContributionSnapshot | None:
        data = self._load_vault_data()
        contributions = data.get("contributions")
        if not contributions:
            return None
        return ContributionSnapshot.from_vault_dict(contributions)

    def _load_cached_transactions(self) -> list[TransactionRecord]:
        cache = self._load_transaction_cache()
        return [
            TransactionRecord(
                **{
                    **payload,
                    "date": PlaidProvider._coerce_date(payload.get("date")),
                }
            )
            for payload in cache.values()
            if payload.get("transaction_id")
        ]

    def refresh_contributions(self) -> ContributionSnapshot:
        """Sync Plaid transactions (if connected) and estimate annual contributions."""
        item_ids = list_item_ids()
        errors: list[str] = []
        refreshed: list[str] = []
        new_records: list[TransactionRecord] = []

        if item_ids:
            cursors = self._load_transaction_cursors()
            cache_empty = not self._load_transaction_cache()
            for item_id in item_ids:
                cursor = "" if cache_empty else cursors.get(item_id, "")
                try:
                    records, next_cursor = self.plaid.sync_transactions(
                        item_id,
                        cursor=cursor,
                    )
                    cursors[item_id] = next_cursor
                    refreshed.append(item_id)
                    new_records.extend(records)
                except Exception as exc:
                    message = f"item {item_id}: {exc}"
                    logger.exception("Failed to sync transactions for %s", item_id)
                    errors.append(message)
            if refreshed:
                self._save_transaction_cursors(cursors)
            if new_records:
                self._merge_transactions(new_records)
        else:
            errors.append("No Plaid items connected — using cached transactions only.")

        transactions = self._load_cached_transactions()
        if not transactions:
            raise RuntimeError(
                "No transactions available. Connect Plaid and refresh expenses or contributions first."
            )

        analysis = analyze_contributions(
            transactions,
            account_owners=self.account_owner_map(),
        )
        unassigned = sum(
            1 for item in analysis.items if item.owner == "unassigned" and item.included
        )
        if unassigned:
            errors.append(
                f"{unassigned} contribution line(s) have unassigned owners — "
                "edit in review or map accounts first."
            )
        snapshot = ContributionSnapshot(
            as_of=_utc_now_iso(),
            analysis=analysis,
            items_refreshed=refreshed,
            errors=errors,
        )
        self.save_contribution_snapshot(snapshot)
        return snapshot

    def parse_balance_csv_file(self, path: Path) -> list[CsvBalanceRow]:
        return parse_balance_csv(path)

    def owner_totals_from_csv_rows(self, rows: list[CsvBalanceRow]) -> dict[str, float]:
        return owner_totals_from_rows(rows)

    def load_cached_expense_snapshot(self) -> ExpenseSnapshot | None:
        data = self._load_vault_data()
        expenses = data.get("expenses")
        if not expenses:
            return None
        return ExpenseSnapshot.from_vault_dict(expenses)

    def refresh_expenses(self) -> ExpenseSnapshot:
        """Sync Plaid transactions (if connected) and estimate annual household expenses."""
        item_ids = list_item_ids()
        errors: list[str] = []
        refreshed: list[str] = []
        new_records: list[TransactionRecord] = []

        if item_ids:
            cursors = self._load_transaction_cursors()
            cache_empty = not self._load_transaction_cache()
            for item_id in item_ids:
                cursor = "" if cache_empty else cursors.get(item_id, "")
                try:
                    records, next_cursor = self.plaid.sync_transactions(
                        item_id,
                        cursor=cursor,
                    )
                    cursors[item_id] = next_cursor
                    refreshed.append(item_id)
                    new_records.extend(records)
                except Exception as exc:
                    message = f"item {item_id}: {exc}"
                    logger.exception("Failed to sync transactions for %s", item_id)
                    errors.append(message)
            if refreshed:
                self._save_transaction_cursors(cursors)
            if new_records:
                self._merge_transactions(new_records)
        else:
            errors.append("No Plaid items connected — using cached transactions only.")

        transactions = self._load_cached_transactions()
        if not transactions:
            raise RuntimeError(
                "No transactions available. Connect Plaid and refresh expenses to sync history."
            )

        analysis = analyze_expenses(
            transactions,
            account_owners=self.account_owner_map(),
            expense_account_ids=self.expense_account_ids(),
        )
        prior = self.load_cached_expense_snapshot()
        if prior and prior.analysis:
            from providers.expense_analyzer import merge_expense_user_overrides

            analysis = merge_expense_user_overrides(analysis, prior.analysis)
        unassigned = sum(
            1 for item in analysis.items if item.owner == "unassigned" and item.included
        )
        if unassigned:
            errors.append(
                f"{unassigned} expense line(s) have unassigned owners — "
                "edit in review or map accounts first."
            )
        snapshot = ExpenseSnapshot(
            as_of=_utc_now_iso(),
            analysis=analysis,
            items_refreshed=refreshed,
            errors=errors,
        )
        self.save_expense_snapshot(snapshot)
        return snapshot

    def get_owner_asset_totals(self) -> dict[str, float]:
        cached = self.load_cached_snapshot()
        if cached:
            return cached.owner_totals
        return {"self": 0.0, "wife": 0.0}

    def update_account_mapping(
        self,
        account_id: str,
        *,
        owner: str | None = None,
        include_in_assets: bool | None = None,
    ) -> AccountsConfig:
        if owner is not None and owner not in VALID_OWNERS:
            raise ValueError(f"owner must be one of {sorted(VALID_OWNERS)}")

        config = self.load_accounts_config()
        updated = False
        for mapping in config.account_mappings:
            if mapping.account_id != account_id:
                continue
            if owner is not None:
                mapping.owner = owner
            if include_in_assets is not None:
                mapping.include_in_assets = include_in_assets
            updated = True
            break

        if not updated:
            raise KeyError(f"account_id not found in mappings: {account_id}")

        self.save_accounts_config(config)
        return config

    def disconnect_item(self, item_id: str) -> AccountsConfig:
        self.plaid.remove_item(item_id)
        config = self.load_accounts_config()
        config.institutions = [inst for inst in config.institutions if inst.item_id != item_id]
        config.account_mappings = [
            mapping for mapping in config.account_mappings if mapping.item_id != item_id
        ]
        self.save_accounts_config(config)
        return config

    def _append_refresh_log(self, snapshot: RefreshSnapshot) -> None:
        ensure_app_data_dir()
        log_path = refresh_log_path()
        history: list[dict[str, Any]] = []
        if log_path.exists():
            history = json.loads(log_path.read_text(encoding="utf-8"))

        history.append(
            {
                "as_of": snapshot.as_of,
                "source": "plaid",
                "items_refreshed": snapshot.items_refreshed,
                "owner_totals": snapshot.owner_totals,
                "account_count": len(snapshot.accounts),
                "error_count": len(snapshot.errors),
            }
        )
        log_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
        _set_file_mode(log_path)


if __name__ == "__main__":
    print("DataService smoke test")

    # Aggregation logic (no network)
    accounts = [
        MappedAccount(
            account_id="a1",
            name="Checking",
            institution="Bank A",
            type="depository",
            subtype="checking",
            mask="1234",
            balance=100000.0,
            currency="USD",
            balance_source="accounts_balance_get",
            owner="self",
            include_in_assets=True,
        ),
        MappedAccount(
            account_id="a2",
            name="Brokerage",
            institution="Brokerage B",
            type="investment",
            subtype="brokerage",
            mask="5678",
            balance=250000.0,
            currency="USD",
            balance_source="investments_holdings_get",
            owner="wife",
            include_in_assets=True,
        ),
        MappedAccount(
            account_id="a3",
            name="Credit Card",
            institution="Bank A",
            type="credit",
            subtype="credit card",
            mask="9012",
            balance=-5000.0,
            currency="USD",
            balance_source="accounts_balance_get",
            owner="self",
            include_in_assets=False,
        ),
    ]

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        svc = DataService(vault=Vault(data_dir=tmp_path), accounts_file=tmp_path / "accounts.json")
        totals = svc.aggregate_owner_totals(accounts)
        assert totals == {"self": 100000.0, "wife": 250000.0}

        snapshot = RefreshSnapshot(
            as_of=_utc_now_iso(),
            accounts=accounts,
            owner_totals=totals,
            items_refreshed=["item_test"],
        )
        svc.save_snapshot(snapshot)
        loaded = svc.load_cached_snapshot()
        assert loaded is not None
        assert loaded.owner_totals == totals
        assert len(loaded.accounts) == 3

    print("  aggregate / vault round-trip: OK")
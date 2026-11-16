"""
Plaid API wrapper — Link token, token exchange, balances, and holdings.

Plaid access tokens are stored in macOS Keychain via ``security.keychain``;
this module never writes tokens to disk or ``.env``.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import plaid
from plaid.api import plaid_api
from plaid.exceptions import ApiException
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.transactions_refresh_request import TransactionsRefreshRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_request_options import TransactionsSyncRequestOptions
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

from security.keychain import delete_token, get_token, store_token
from settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Require only ``transactions`` so credit-card-only institutions (Capital One, Amex,
# etc.) are not blocked. Brokerages still receive investments when supported.
# Balances are available via the Balance product (auto-enabled with transactions).
LINK_PRODUCTS = [Products("transactions")]
LINK_REQUIRED_IF_SUPPORTED = [Products("investments")]

ASSET_ACCOUNT_TYPES = frozenset({"depository", "investment"})

# Credit / loan balances are liabilities — use for spending analysis, not Initial Assets.
_LIABILITY_TYPES = frozenset({"credit", "loan"})
_LIABILITY_SUBTYPES = frozenset(
    {
        "credit card",
        "paypal",
        "line of credit",
        "mortgage",
        "student",
        "auto",
        "commercial",
        "consumer",
        "home equity",
    }
)


def default_include_in_assets(*, account_type: str, subtype: str | None, name: str | None = None) -> bool:
    """True when a Plaid account should count toward investable assets."""
    typ = str(account_type or "").strip().lower()
    sub = str(subtype or "").strip().lower()
    nam = str(name or "").strip().lower()
    if typ in _LIABILITY_TYPES or sub in _LIABILITY_SUBTYPES:
        return False
    if "credit card" in nam or nam.endswith(" card"):
        return False
    # HSA is money the user owns (often depository/investment subtype hsa)
    if sub == "hsa" or "health savings" in nam:
        return True
    return typ in ASSET_ACCOUNT_TYPES


TRANSACTION_HISTORY_DAYS = 730


@dataclass(frozen=True)
class LinkTokenResult:
    link_token: str
    expiration: str


@dataclass(frozen=True)
class ExchangeResult:
    item_id: str
    access_token: str


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    account_id: str
    date: str
    amount: float
    name: str
    merchant_name: str | None
    pending: bool
    pfc_primary: str | None = None
    pfc_detailed: str | None = None
    item_id: str | None = None
    institution_name: str | None = None


@dataclass(frozen=True)
class HoldingSnapshot:
    account_id: str
    security_id: str
    ticker: str | None
    name: str
    quantity: float
    value: float
    currency: str
    item_id: str | None = None
    institution_name: str | None = None


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    name: str
    official_name: str | None
    type: str
    subtype: str | None
    mask: str | None
    balance: float
    currency: str
    balance_source: str
    include_in_assets: bool
    institution_id: str | None = None
    institution_name: str | None = None
    item_id: str | None = None


def _plaid_host(plaid_env: str) -> str:
    if plaid_env == "production":
        return plaid.Environment.Production
    if plaid_env == "development":
        return "https://development.plaid.com"
    return plaid.Environment.Sandbox


def _response_to_dict(response: Any) -> dict[str, Any]:
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return dict(response)


class PlaidProvider:
    """Thin wrapper around the Plaid Python SDK."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings(require_plaid=True)
        configuration = plaid.Configuration(
            host=_plaid_host(self.settings.plaid_env),
            api_key={
                "clientId": self.settings.plaid_client_id,
                "secret": self.settings.plaid_secret,
                "plaidVersion": "2020-09-14",
            },
        )
        api_client = plaid.ApiClient(configuration)
        self.client = plaid_api.PlaidApi(api_client)

    def create_link_token(
        self,
        client_user_id: str | None = None,
        *,
        access_token: str | None = None,
    ) -> LinkTokenResult:
        """
        Create a Link token to start (or update) a Plaid Link session.

        Pass ``access_token`` when re-authenticating an existing Item.
        """
        user_id = client_user_id or f"user_{uuid.uuid4().hex}"
        request_kwargs: dict[str, Any] = {
            "client_name": "Retirement Financial Modeling",
            "language": "en",
            "country_codes": [CountryCode("US")],
            "user": LinkTokenCreateRequestUser(client_user_id=user_id),
        }

        if access_token:
            request_kwargs["access_token"] = access_token
        else:
            request_kwargs["products"] = LINK_PRODUCTS
            request_kwargs["required_if_supported_products"] = LINK_REQUIRED_IF_SUPPORTED

        response = self.client.link_token_create(LinkTokenCreateRequest(**request_kwargs))
        data = _response_to_dict(response)
        return LinkTokenResult(
            link_token=data["link_token"],
            expiration=data.get("expiration", ""),
        )

    def exchange_public_token(self, public_token: str) -> ExchangeResult:
        """Exchange a Link ``public_token`` and persist the access token in Keychain."""
        if not public_token or not public_token.strip():
            raise ValueError("public_token is required")

        request = ItemPublicTokenExchangeRequest(public_token=public_token.strip())
        response = self.client.item_public_token_exchange(request)
        data = _response_to_dict(response)

        item_id = data["item_id"]
        access_token = data["access_token"]
        store_token(item_id, access_token)

        return ExchangeResult(item_id=item_id, access_token=access_token)

    def resolve_access_token(self, item_id: str) -> str:
        token = get_token(item_id)
        if not token:
            raise KeyError(f"No Plaid access token stored for item_id={item_id!r}")
        return token

    def get_item(self, item_id: str) -> dict[str, Any]:
        access_token = self.resolve_access_token(item_id)
        response = self.client.item_get(ItemGetRequest(access_token=access_token))
        return _response_to_dict(response)

    def get_accounts(self, item_id: str) -> list[dict[str, Any]]:
        access_token = self.resolve_access_token(item_id)
        response = self.client.accounts_get(AccountsGetRequest(access_token=access_token))
        data = _response_to_dict(response)
        return list(data.get("accounts", []))

    def get_balances_raw(self, item_id: str) -> dict[str, Any]:
        access_token = self.resolve_access_token(item_id)
        response = self.client.accounts_balance_get(
            AccountsBalanceGetRequest(access_token=access_token)
        )
        return _response_to_dict(response)

    def get_holdings_raw(self, item_id: str) -> dict[str, Any]:
        access_token = self.resolve_access_token(item_id)
        response = self.client.investments_holdings_get(
            InvestmentsHoldingsGetRequest(access_token=access_token)
        )
        return _response_to_dict(response)

    def fetch_account_snapshots(self, item_id: str) -> list[AccountSnapshot]:
        """
        Return normalized balances per account with double-counting prevention.

        Investment accounts use summed holdings values; depository accounts use
        ``accounts_balance_get``. Credit accounts are excluded from assets.
        """
        item_info = self.get_item(item_id)
        institution_id = item_info.get("item", {}).get("institution_id")
        institution_name = item_info.get("item", {}).get("institution_name")

        balances_data = self.get_balances_raw(item_id)
        accounts = list(balances_data.get("accounts", []))

        holdings_by_account: dict[str, float] = {}
        try:
            holdings_data = self.get_holdings_raw(item_id)
            for holding in holdings_data.get("holdings", []):
                account_id = holding.get("account_id")
                if not account_id:
                    continue
                value = holding.get("institution_value")
                if value is None:
                    value = holding.get("value", 0.0)
                holdings_by_account[account_id] = holdings_by_account.get(account_id, 0.0) + float(
                    value or 0.0
                )
        except ApiException as exc:
            logger.info("Holdings unavailable for item %s: %s", item_id, exc)

        snapshots: list[AccountSnapshot] = []
        for account in accounts:
            account_id = account.get("account_id", "")
            account_type = account.get("type", "")
            subtype = account.get("subtype")
            balances = account.get("balances") or {}
            currency = balances.get("iso_currency_code") or balances.get("unofficial_currency_code") or "USD"

            if account_type == "investment" and account_id in holdings_by_account:
                balance = holdings_by_account[account_id]
                balance_source = "investments_holdings_get"
            else:
                balance = float(balances.get("current") or balances.get("available") or 0.0)
                balance_source = "accounts_balance_get"

            include_in_assets = default_include_in_assets(
                account_type=account_type,
                subtype=subtype,
                name=account.get("name"),
            )

            snapshots.append(
                AccountSnapshot(
                    account_id=account_id,
                    name=account.get("name", ""),
                    official_name=account.get("official_name"),
                    type=account_type,
                    subtype=subtype,
                    mask=account.get("mask"),
                    balance=balance,
                    currency=currency,
                    balance_source=balance_source,
                    include_in_assets=include_in_assets,
                    institution_id=institution_id,
                    institution_name=institution_name,
                    item_id=item_id,
                )
            )

        return snapshots

    def fetch_holdings(self, item_id: str) -> list[HoldingSnapshot]:
        """Return investment holdings with security tickers for portfolio return weighting."""
        item_info = self.get_item(item_id)
        institution_name = item_info.get("item", {}).get("institution_name")

        try:
            holdings_data = self.get_holdings_raw(item_id)
        except ApiException as exc:
            logger.info("Holdings unavailable for item %s: %s", item_id, exc)
            return []

        securities_by_id = {
            security.get("security_id"): security
            for security in holdings_data.get("securities", [])
        }

        holdings: list[HoldingSnapshot] = []
        for holding in holdings_data.get("holdings", []):
            security_id = holding.get("security_id", "")
            security = securities_by_id.get(security_id, {})
            value = holding.get("institution_value")
            if value is None:
                value = holding.get("value", 0.0)
            currency = security.get("iso_currency_code") or security.get("unofficial_currency_code") or "USD"
            holdings.append(
                HoldingSnapshot(
                    account_id=holding.get("account_id", ""),
                    security_id=security_id,
                    ticker=security.get("ticker_symbol"),
                    name=security.get("name") or security.get("ticker_symbol") or "Unknown",
                    quantity=float(holding.get("quantity") or 0.0),
                    value=float(value or 0.0),
                    currency=currency,
                    item_id=item_id,
                    institution_name=institution_name,
                )
            )
        return holdings

    def refresh_transactions(self, item_id: str) -> None:
        """Ask Plaid to pull latest transactions (useful in sandbox)."""
        access_token = self.resolve_access_token(item_id)
        try:
            self.client.transactions_refresh(
                TransactionsRefreshRequest(access_token=access_token)
            )
        except ApiException as exc:
            logger.info("transactions_refresh skipped for %s: %s", item_id, exc)

    @staticmethod
    def _coerce_date(value: Any) -> str:
        if not value:
            return ""
        if hasattr(value, "isoformat"):
            return value.isoformat()[:10]
        return str(value)[:10]

    def _normalize_transaction(
        self,
        txn: dict[str, Any],
        *,
        item_id: str,
        institution_name: str | None,
    ) -> TransactionRecord:
        pfc = txn.get("personal_finance_category") or {}
        return TransactionRecord(
            transaction_id=txn.get("transaction_id", ""),
            account_id=txn.get("account_id", ""),
            date=self._coerce_date(txn.get("date")),
            amount=float(txn.get("amount") or 0.0),
            name=txn.get("name", ""),
            merchant_name=txn.get("merchant_name"),
            pending=bool(txn.get("pending", False)),
            pfc_primary=pfc.get("primary"),
            pfc_detailed=pfc.get("detailed"),
            item_id=item_id,
            institution_name=institution_name,
        )

    def sync_transactions(
        self,
        item_id: str,
        *,
        cursor: str = "",
        days_requested: int = TRANSACTION_HISTORY_DAYS,
    ) -> tuple[list[TransactionRecord], str]:
        """
        Incrementally sync transactions via ``/transactions/sync``.

        Returns normalized transactions and the next cursor to persist.
        """
        access_token = self.resolve_access_token(item_id)
        item_info = self.get_item(item_id)
        institution_name = item_info.get("item", {}).get("institution_name")

        if not cursor:
            self.refresh_transactions(item_id)

        collected: dict[str, TransactionRecord] = {}
        next_cursor = cursor
        has_more = True

        while has_more:
            options = None
            if not cursor and not next_cursor:
                options = TransactionsSyncRequestOptions(
                    days_requested=days_requested,
                    include_personal_finance_category=True,
                )
            request_kwargs: dict[str, Any] = {
                "access_token": access_token,
                "cursor": next_cursor,
                "count": 500,
            }
            if options is not None:
                request_kwargs["options"] = options
            request = TransactionsSyncRequest(**request_kwargs)
            response = self.client.transactions_sync(request)
            data = _response_to_dict(response)

            for txn in data.get("added", []):
                record = self._normalize_transaction(
                    txn,
                    item_id=item_id,
                    institution_name=institution_name,
                )
                collected[record.transaction_id] = record
            for txn in data.get("modified", []):
                record = self._normalize_transaction(
                    txn,
                    item_id=item_id,
                    institution_name=institution_name,
                )
                collected[record.transaction_id] = record
            for txn_id in data.get("removed", []):
                if isinstance(txn_id, dict):
                    txn_id = txn_id.get("transaction_id", "")
                collected.pop(txn_id, None)

            next_cursor = data.get("next_cursor", next_cursor)
            has_more = bool(data.get("has_more", False))

        return list(collected.values()), next_cursor

    def remove_item(self, item_id: str) -> None:
        """Revoke Plaid access for an Item and delete its Keychain token."""
        access_token = self.resolve_access_token(item_id)
        try:
            self.client.item_remove(ItemRemoveRequest(access_token=access_token))
        finally:
            delete_token(item_id)


def default_link_user_id() -> str:
    """Stable-enough anonymous client user id for Link sessions on this Mac."""
    return f"retirement_planner_{int(time.time())}"


if __name__ == "__main__":
    print("PlaidProvider smoke test")
    settings = get_settings()
    if not settings.plaid_configured:
        print("  skipped: PLAID_CLIENT_ID / PLAID_SECRET not set in .env")
    else:
        provider = PlaidProvider(settings)
        result = provider.create_link_token(default_link_user_id())
        assert result.link_token
        print(f"  link_token_create: OK (expires {result.expiration})")
    print("  import / init: OK")
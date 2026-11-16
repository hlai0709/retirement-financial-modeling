"""Live and historical market data via Yahoo Finance."""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from functools import lru_cache

import yfinance as yf

logger = logging.getLogger(__name__)

SP500_SYMBOL = "^GSPC"
BOND_SYMBOL = "AGG"
DEFAULT_LOOKBACK_YEARS = 10


def _annualized_return(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    start, end = closes[0], closes[-1]
    if start <= 0:
        return None
    years = len(closes) / 252
    if years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def _valid_price(value: object) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _last_valid_close(history) -> float | None:
    if history is None or history.empty or "Close" not in history:
        return None
    closes = history["Close"].dropna()
    if closes.empty:
        return None
    for value in reversed(closes.tolist()):
        price = _valid_price(value)
        if price is not None:
            return price
    return None


def get_current_price(symbol: str) -> float:
    ticker = yf.Ticker(symbol)
    price = _valid_price(ticker.fast_info.get("last_price"))
    if price is None:
        price = _last_valid_close(ticker.history(period="10d"))
    if price is None:
        raise ValueError(f"No valid price data for {symbol}")
    return price


@lru_cache(maxsize=16)
def _annualized_return_cached(symbol: str, years: int, as_of: str) -> float:
    start = date.fromisoformat(as_of) - timedelta(days=int(years * 365.25))
    history = yf.download(
        symbol,
        start=start.isoformat(),
        progress=False,
        auto_adjust=True,
    )
    if history.empty:
        raise ValueError(f"No historical data for {symbol}")
    close = history["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    closes = [float(value) for value in close.dropna().tolist()]
    annualized = _annualized_return(closes)
    if annualized is None:
        raise ValueError(f"Could not compute return for {symbol}")
    return annualized


def get_annualized_return(symbol: str, years: int = DEFAULT_LOOKBACK_YEARS) -> float:
    return _annualized_return_cached(symbol, years, date.today().isoformat())


def get_sp500_10yr_cagr() -> float:
    return get_annualized_return(SP500_SYMBOL, DEFAULT_LOOKBACK_YEARS)


@lru_cache(maxsize=16)
def _yearly_close_series_cached(symbol: str, years: int, as_of: str):
    start = date.fromisoformat(as_of) - timedelta(days=int((years + 1) * 365.25))
    history = yf.download(
        symbol,
        start=start.isoformat(),
        progress=False,
        auto_adjust=True,
    )
    if history.empty:
        raise ValueError(f"No historical data for {symbol}")

    close = history["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]

    yearly = close.resample("YE").last().dropna()
    if len(yearly) < 2:
        raise ValueError(f"Not enough yearly data for {symbol}")
    return yearly


def _yearly_close_series(symbol: str, years: int):
    return _yearly_close_series_cached(symbol, years, date.today().isoformat())


def get_annual_returns_by_year(
    symbol: str = SP500_SYMBOL,
    years: int = DEFAULT_LOOKBACK_YEARS,
) -> list[tuple[int, float]]:
    """Return calendar-year total returns as (year, rate) pairs (oldest → newest)."""
    yearly = _yearly_close_series(symbol, years)
    pairs: list[tuple[int, float]] = []
    prior_value: float | None = None
    for timestamp, value in yearly.items():
        current = float(value)
        if prior_value is not None and prior_value > 0:
            pairs.append((int(timestamp.year), (current / prior_value) - 1.0))
        prior_value = current
    return pairs[-years:]


def get_annual_returns(symbol: str = SP500_SYMBOL, years: int = DEFAULT_LOOKBACK_YEARS) -> list[float]:
    """Return calendar-year total returns for bootstrap sampling (oldest → newest)."""
    return [rate for _, rate in get_annual_returns_by_year(symbol, years)]
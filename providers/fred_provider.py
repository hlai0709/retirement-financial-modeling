"""FRED macro data — CPI year-over-year inflation."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

CPI_SERIES_ID = "CPIAUCSL"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"


def get_cpi_yoy(api_key: str) -> tuple[float, str]:
    """
    Return YoY CPI inflation as a decimal (e.g. 0.032 for 3.2%) and observation date.
    """
    if not api_key or not api_key.strip():
        raise ValueError("FRED_API_KEY is required")

    response = requests.get(
        FRED_OBSERVATIONS_URL,
        params={
            "series_id": CPI_SERIES_ID,
            "api_key": api_key.strip(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 14,
        },
        timeout=30,
    )
    if response.status_code == 400:
        raise ValueError(
            "Invalid FRED_API_KEY — replace the placeholder in .env with a real key "
            "from https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    response.raise_for_status()
    observations = response.json().get("observations", [])
    values: list[tuple[str, float]] = []
    for row in observations:
        raw = row.get("value", ".")
        if raw in (".", "", None):
            continue
        values.append((row.get("date", ""), float(raw)))

    if len(values) < 13:
        raise ValueError("Not enough CPI observations for YoY calculation")

    latest_date, latest_value = values[0]
    _, year_ago_value = values[12]
    if year_ago_value <= 0:
        raise ValueError("Invalid CPI year-ago value")

    yoy = (latest_value / year_ago_value) - 1
    return yoy, latest_date


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@lru_cache(maxsize=8)
def _cpi_annual_inflation_history_cached(api_key: str, years: int, as_of: str) -> tuple[tuple[str, float], ...]:
    return tuple(_cpi_annual_inflation_history_uncached(api_key, years))


def get_cpi_annual_inflation_history(api_key: str, years: int = 10) -> list[tuple[str, float]]:
    return list(_cpi_annual_inflation_history_cached(api_key, years, date.today().isoformat()))


def _cpi_annual_inflation_history_uncached(api_key: str, years: int = 10) -> list[tuple[str, float]]:
    """
    Return YoY CPI inflation by calendar year as (year, decimal_rate) pairs.

    Uses the latest available month in each year vs the same month one year prior.
    """
    if not api_key or not api_key.strip():
        raise ValueError("FRED_API_KEY is required")

    limit = max(24, (years + 2) * 12)
    response = requests.get(
        FRED_OBSERVATIONS_URL,
        params={
            "series_id": CPI_SERIES_ID,
            "api_key": api_key.strip(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
        timeout=30,
    )
    if response.status_code == 400:
        raise ValueError(
            "Invalid FRED_API_KEY — replace the placeholder in .env with a real key "
            "from https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    response.raise_for_status()

    observations = response.json().get("observations", [])
    monthly: list[tuple[str, float]] = []
    for row in observations:
        raw = row.get("value", ".")
        if raw in (".", "", None):
            continue
        obs_date = row.get("date", "")
        if not obs_date:
            continue
        monthly.append((obs_date, float(raw)))

    if len(monthly) < 13:
        raise ValueError("Not enough CPI observations for annual inflation history")

    monthly.sort(key=lambda pair: pair[0])
    by_year_month: dict[tuple[int, int], float] = {}
    for obs_date, value in monthly:
        year_str, month_str, *_ = obs_date.split("-")
        by_year_month[(int(year_str), int(month_str))] = value

    annual_rates: list[tuple[str, float]] = []
    years_seen = sorted({year for year, _ in by_year_month})
    for year in years_seen:
        latest_month = max(
            (month for yr, month in by_year_month if yr == year),
            default=None,
        )
        if latest_month is None:
            continue
        current = by_year_month.get((year, latest_month))
        prior = by_year_month.get((year - 1, latest_month))
        if current is None or prior is None or prior <= 0:
            continue
        annual_rates.append((str(year), (current / prior) - 1))

    if not annual_rates:
        raise ValueError("Could not compute annual CPI inflation history")

    return annual_rates[-years:]
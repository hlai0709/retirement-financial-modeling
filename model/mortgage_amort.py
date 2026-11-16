"""Amortization helpers — estimate remaining term from balance, P&I, and rate."""

from __future__ import annotations

import math
from typing import Any


def months_to_payoff(
    principal: float,
    monthly_pi: float,
    annual_rate_pct: float,
) -> int | None:
    """Return remaining months for a level-payment amortizing loan, or None if invalid."""
    if principal <= 0 or monthly_pi <= 0:
        return None
    if annual_rate_pct < 0:
        return None
    if annual_rate_pct == 0:
        return max(1, int(math.ceil(principal / monthly_pi)))

    r = annual_rate_pct / 100.0 / 12.0
    interest_only = principal * r
    if monthly_pi <= interest_only + 1e-9:
        # Payment does not cover interest — never amortizes
        return None
    ratio = monthly_pi / (monthly_pi - interest_only)
    n = math.log(ratio) / math.log(1.0 + r)
    if not math.isfinite(n) or n <= 0:
        return None
    return max(1, int(math.ceil(n - 1e-9)))


def payoff_calendar_year(
    principal: float,
    monthly_pi: float,
    annual_rate_pct: float,
    *,
    as_of_year: int,
    as_of_month: int = 1,
) -> int | None:
    """Calendar year of the final payment (inclusive)."""
    months = months_to_payoff(principal, monthly_pi, annual_rate_pct)
    if months is None:
        return None
    month = max(1, min(12, int(as_of_month)))
    # months remaining including the current due payment
    end_index = (month - 1) + months  # 1-based month index from Jan of as_of_year
    year = int(as_of_year) + (end_index - 1) // 12
    if year < 2020 or year > 2080:
        return None
    return year


def amortization_summary(
    principal: float,
    monthly_pi: float,
    annual_rate_pct: float,
    *,
    as_of_year: int,
    as_of_month: int = 1,
) -> dict[str, Any] | None:
    months = months_to_payoff(principal, monthly_pi, annual_rate_pct)
    year = payoff_calendar_year(
        principal,
        monthly_pi,
        annual_rate_pct,
        as_of_year=as_of_year,
        as_of_month=as_of_month,
    )
    if months is None or year is None:
        return None
    return {
        "months_remaining": months,
        "years_remaining": round(months / 12.0, 1),
        "payoff_year": year,
        "principal": round(principal, 2),
        "monthly_pi": round(monthly_pi, 2),
        "annual_rate_pct": annual_rate_pct,
        "annual_payment": round(monthly_pi * 12.0, 2),
    }

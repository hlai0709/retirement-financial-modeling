"""Rule-based investment adviser — all recommendations derived from simulation inputs."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from config.model_fields import FIELDS_BY_KEY
from simulation.monte_carlo import MonteCarloResult, SimulationOutput, run_monte_carlo_simulation
from providers.plaid_provider import HoldingSnapshot
from settings import get_settings

RiskTolerance = Literal["conservative", "moderate", "aggressive"]

DISCLAIMER = (
    "This tool provides educational analysis only. It is not licensed financial, tax, or "
    "investment advice. Consult a qualified professional before making financial decisions."
)

TARGET_SUCCESS_RATES: dict[RiskTolerance, float] = {
    "conservative": 0.90,
    "moderate": 0.80,
    "aggressive": 0.70,
}

SINGLE_STOCK_BANDS: dict[RiskTolerance, tuple[float, float]] = {
    "conservative": (0.05, 0.10),
    "moderate": (0.15, 0.25),
    "aggressive": (0.25, 0.40),
}

CONTRIBUTION_BUMP = 5_000
QUICK_MC_PATHS = 500


@dataclass(frozen=True)
class WhatIfChange:
    field_key: str
    scope: Literal["shared", "person"]
    person: str | None
    new_value: float | int | str | bool
    label: str


@dataclass
class Recommendation:
    priority: int
    category: str
    title: str
    detail: str
    what_if: list[WhatIfChange] | None = None


@dataclass
class AdviserReport:
    goal_status: str
    success_rate: float | None
    target_success_rate: float
    asset_gap_estimate: float | None
    total_portfolio_value: float
    recommendations: list[Recommendation]
    checklist: list[str]
    holdings_summary: str | None = None
    disclaimer: str = DISCLAIMER


def _total_assets(params: dict) -> float:
    return sum(float(person.get("initial_assets", 0) or 0) for person in params["persons"].values())


def _primary_mc_key(params: dict, output: SimulationOutput) -> str | None:
    if not output.monte_carlo:
        return None
    if params["mode"] == "joint" and "joint" in output.monte_carlo:
        return "joint"
    for name in ("self", "wife"):
        if name in output.monte_carlo:
            return name
    return next(iter(output.monte_carlo))


def _primary_det_df(params: dict, output: SimulationOutput) -> pd.DataFrame | None:
    if not output.deterministic:
        return None
    key = "joint" if params["mode"] == "joint" else "self"
    if key in output.deterministic:
        return output.deterministic[key]
    return next(iter(output.deterministic.values()))


def _estimate_asset_gap(total_assets: float, success_rate: float, target_rate: float) -> float | None:
    if success_rate >= target_rate or success_rate <= 0:
        return None
    shortfall_ratio = (target_rate - success_rate) / success_rate
    return round(total_assets * shortfall_ratio, -3)  # nearest $1k


def _goal_recommendation(
    mc_result: MonteCarloResult | None,
    det_df: pd.DataFrame | None,
    *,
    total_assets: float,
    target_rate: float,
) -> tuple[str, float | None, Recommendation | None]:
    if mc_result is None:
        if det_df is not None and not det_df.empty:
            ending = float(det_df["Assets_End"].iloc[-1])
            if ending <= 0:
                status = "Deterministic projection depletes assets before life expectancy."
                return status, None, Recommendation(
                    priority=1,
                    category="goal_gap",
                    title="Assets may run out",
                    detail=(
                        "The deterministic path reaches $0 before life expectancy. "
                        "Consider higher contributions, lower retirement expenses, or delayed retirement."
                    ),
                )
            status = f"Deterministic ending balance: ${ending:,.0f} (no Monte Carlo run)."
            return status, None, None
        return "Run Monte Carlo simulation for goal analysis.", None, None

    success = mc_result.success_rate
    gap = _estimate_asset_gap(total_assets, success, target_rate)

    if success >= target_rate:
        status = (
            f"On track — {success:.0%} success rate meets your {target_rate:.0%} target "
            f"(median ending balance ${mc_result.median_ending_balance:,.0f})."
        )
        return status, gap, None

    gap_text = f" Estimated additional assets needed: ${gap:,.0f}." if gap else ""
    status = f"Below target — {success:.0%} success rate vs {target_rate:.0%} goal.{gap_text}"
    shortfall_note = ""
    if mc_result.median_depletion_age is not None:
        shortfall_note = (
            f" Failed paths typically deplete around age {mc_result.median_depletion_age:.0f}"
            f" ({mc_result.mean_shortfall_years:.1f} years underwater on average)."
        )
    rec = Recommendation(
        priority=1,
        category="goal_gap",
        title=f"Close the goal gap (~${gap:,.0f})" if gap else "Improve plan success rate",
        detail=(
            f"Monte Carlo shows {success:.0%} of paths succeed vs your {target_rate:.0%} target. "
            f"10th-percentile ending balance is ${mc_result.p10_ending_balance:,.0f}."
            + shortfall_note
            + (f" Rule-of-thumb: ~${gap:,.0f} more in starting assets may help close the gap." if gap else "")
        ),
    )
    return status, gap, rec


def _holdings_weights(holdings: list[HoldingSnapshot]) -> dict[str, float]:
    total = sum(holding.value for holding in holdings)
    if total <= 0:
        return {}
    weights: dict[str, float] = {}
    for holding in holdings:
        label = (holding.ticker or holding.name or "Unknown").upper()
        weights[label] = weights.get(label, 0.0) + holding.value / total
    return dict(sorted(weights.items(), key=lambda item: item[1], reverse=True))


def _allocation_recommendations(
    holdings: list[HoldingSnapshot] | None,
    *,
    risk_tolerance: RiskTolerance,
    total_assets: float,
) -> tuple[str | None, list[Recommendation]]:
    if not holdings:
        return None, []

    weights = _holdings_weights(holdings)
    if not weights:
        return None, []

    total_value = sum(holding.value for holding in holdings)
    top_lines = [f"{ticker} {weight:.1%}" for ticker, weight in list(weights.items())[:5]]
    summary = f"Portfolio ${total_value:,.0f} — " + ", ".join(top_lines)

    recs: list[Recommendation] = []
    low, high = SINGLE_STOCK_BANDS[risk_tolerance]

    for ticker, weight in weights.items():
        if weight < low:
            continue
        if weight > high:
            excess_value = (weight - high) * total_value
            trim_to = high * total_value
            recs.append(
                Recommendation(
                    priority=2,
                    category="rebalance",
                    title=f"Reduce {ticker} concentration ({weight:.0%})",
                    detail=(
                        f"{ticker} is {weight:.0%} of tracked holdings; "
                        f"a {risk_tolerance} profile typically keeps single stocks under {high:.0%}. "
                        f"Consider trimming ~${excess_value:,.0f} (target ~${trim_to:,.0f} in {ticker}) "
                        f"into broad index funds such as VTI/VXUS."
                    ),
                )
            )
        elif weight >= low:
            recs.append(
                Recommendation(
                    priority=3,
                    category="allocation",
                    title=f"Review {ticker} weight ({weight:.0%})",
                    detail=(
                        f"{ticker} is {weight:.0%} of holdings. "
                        f"Typical range for a {risk_tolerance} timeline: {low:.0%}–{high:.0%}. "
                        "Ensure this concentration matches your risk comfort."
                    ),
                )
            )
        break  # only flag the largest concentrated position

    return summary, recs


def _working_persons(params: dict) -> list[str]:
    names: list[str] = []
    for name, person in params["persons"].items():
        age = int(person.get("current_age", 0))
        retire = int(person.get("retirement_age", 65))
        if age < retire:
            names.append(name)
    return names


def _contribution_recommendation(
    params: dict,
    mc_result: MonteCarloResult | None,
    *,
    target_rate: float,
) -> Recommendation | None:
    if mc_result is None or mc_result.success_rate >= target_rate:
        return None

    workers = _working_persons(params)
    if not workers:
        return None

    bumped = copy.deepcopy(params)
    per_person = CONTRIBUTION_BUMP / len(workers)
    changes: list[WhatIfChange] = []
    for name in workers:
        current = float(bumped["persons"][name].get("annual_contributions", 0) or 0)
        new_value = current + per_person
        bumped["persons"][name]["annual_contributions"] = new_value
        field = FIELDS_BY_KEY["annual_contributions"]
        changes.append(
            WhatIfChange(
                field_key="annual_contributions",
                scope="person",
                person=name,
                new_value=new_value,
                label=f"{name.title()} {field.label}",
            )
        )

    try:
        settings = get_settings()
        key = "joint" if bumped["mode"] == "joint" else workers[0]
        quick = run_monte_carlo_simulation(
            bumped,
            num_paths=QUICK_MC_PATHS,
            seed=42,
            fred_api_key=settings.fred_api_key,
        )
        bumped_result = quick.get(key)
        if bumped_result is None:
            return None
        delta = bumped_result.success_rate - mc_result.success_rate
        if delta <= 0.005:
            return None
        return Recommendation(
            priority=2,
            category="contribution",
            title=f"+${CONTRIBUTION_BUMP:,}/yr contributions → {delta:+.0%} success rate",
            detail=(
                f"Quick estimate ({QUICK_MC_PATHS} paths): raising household contributions by "
                f"${CONTRIBUTION_BUMP:,}/yr moves success from {mc_result.success_rate:.0%} "
                f"to {bumped_result.success_rate:.0%}."
            ),
            what_if=changes,
        )
    except Exception:
        return None


def _depletion_recommendation(det_df: pd.DataFrame | None) -> Recommendation | None:
    if det_df is None or det_df.empty:
        return None
    zero_rows = det_df[det_df["Assets_End"] <= 0]
    if zero_rows.empty:
        return None
    age = int(zero_rows.iloc[0]["Age"])
    return Recommendation(
        priority=1,
        category="goal_gap",
        title=f"Assets deplete around age {age}",
        detail=(
            f"The deterministic projection runs out of assets at age {age}. "
            "Review retirement expenses, income timing, or consider working longer."
        ),
    )


def _build_checklist(recommendations: list[Recommendation], *, has_mc: bool) -> list[str]:
    items: list[str] = []
    if not has_mc:
        items.append("Run a Monte Carlo simulation to quantify plan success rate.")
    for rec in sorted(recommendations, key=lambda r: r.priority)[:5]:
        items.append(rec.title)
    if not items:
        items.append("Plan metrics look healthy — revisit assumptions annually.")
    return items


def analyze(
    params: dict,
    output: SimulationOutput,
    *,
    holdings: list[HoldingSnapshot] | None = None,
    risk_tolerance: RiskTolerance = "moderate",
) -> AdviserReport:
    """Build a rule-based adviser report from simulation results and live holdings."""
    target_rate = TARGET_SUCCESS_RATES[risk_tolerance]
    total_assets = _total_assets(params)

    mc_key = _primary_mc_key(params, output)
    mc_result = output.monte_carlo.get(mc_key) if mc_key else None
    det_df = _primary_det_df(params, output)

    goal_status, asset_gap, goal_rec = _goal_recommendation(
        mc_result,
        det_df,
        total_assets=total_assets,
        target_rate=target_rate,
    )

    recommendations: list[Recommendation] = []
    if goal_rec:
        recommendations.append(goal_rec)

    depletion_rec = _depletion_recommendation(det_df)
    if depletion_rec and not any(r.category == "goal_gap" for r in recommendations):
        recommendations.append(depletion_rec)

    holdings_summary, allocation_recs = _allocation_recommendations(
        holdings,
        risk_tolerance=risk_tolerance,
        total_assets=total_assets or sum(h.value for h in holdings) if holdings else 0,
    )
    recommendations.extend(allocation_recs)

    contrib_rec = _contribution_recommendation(params, mc_result, target_rate=target_rate)
    if contrib_rec:
        recommendations.append(contrib_rec)

    recommendations.sort(key=lambda rec: rec.priority)
    checklist = _build_checklist(recommendations, has_mc=mc_result is not None)

    return AdviserReport(
        goal_status=goal_status,
        success_rate=mc_result.success_rate if mc_result else None,
        target_success_rate=target_rate,
        asset_gap_estimate=asset_gap,
        total_portfolio_value=total_assets,
        recommendations=recommendations[:6],
        checklist=checklist,
        holdings_summary=holdings_summary,
    )
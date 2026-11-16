"""Serialize simulation output for web results UI."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.controllers.simulation_controller import SimulationRunResult
from simulation.monte_carlo import MonteCarloResult, SimulationOutput


def _json_num(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return int(number) if number == int(number) else number


def _col(df: pd.DataFrame, *names: str) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _row_value(row: pd.Series, *columns: str) -> Any:
    for column in columns:
        if column in row.index:
            return row[column]
    return None


def _dataframe_export_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        entry: dict[str, Any] = {}
        for column in df.columns:
            value = row[column]
            if pd.isna(value):
                entry[column] = None
            elif isinstance(value, (int, float)):
                entry[column] = _json_num(value)
            else:
                entry[column] = str(value)
        rows.append(entry)
    return rows


def _sample_series_rows(
    df: pd.DataFrame,
    *,
    max_points: int,
    build_point: Any,
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    step = max(1, len(df) // max_points)
    points: list[dict[str, Any]] = []
    for idx in range(0, len(df), step):
        points.append(build_point(df.iloc[idx], idx))
    last = df.iloc[-1]
    last_point = build_point(last, len(df) - 1)
    if not points or points[-1] != last_point:
        points.append(last_point)
    return points


def _sample_deterministic_chart(df: pd.DataFrame, *, max_points: int = 28) -> list[dict[str, Any]]:
    balance_col = _col(df, "Assets_End", "ending_balance", "total_assets")
    if balance_col is None:
        return []
    age_col = _col(df, "Age", "age")
    year_col = _col(df, "Year", "year")

    def build_point(row: pd.Series, idx: int) -> dict[str, Any]:
        return {
            "year": _json_num(_row_value(row, year_col) if year_col else None),
            "age": _json_num(_row_value(row, age_col) if age_col else idx),
            "balance": _json_num(_row_value(row, balance_col)) or 0,
        }

    return _sample_series_rows(df, max_points=max_points, build_point=build_point)


def _sample_table_rows(df: pd.DataFrame, *, max_rows: int = 12) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    year_col = _col(df, "Year", "year")
    age_col = _col(df, "Age", "age")
    assets_col = _col(df, "Assets_End", "ending_balance")
    expenses_col = _col(df, "Expenses", "expenses")
    ss_col = _col(df, "SS", "ss")

    if year_col is None and age_col is None:
        return []

    step = max(1, len(df) // max_rows)
    rows: list[dict[str, Any]] = []
    for idx in range(0, len(df), step):
        row = df.iloc[idx]
        rows.append(
            {
                "year": _json_num(_row_value(row, year_col) if year_col else None),
                "age": _json_num(_row_value(row, age_col) if age_col else None),
                "assets": _json_num(_row_value(row, assets_col) if assets_col else None),
                "expenses": _json_num(_row_value(row, expenses_col) if expenses_col else None),
                "ss": _json_num(_row_value(row, ss_col) if ss_col else None),
            }
        )
    return rows


def _deterministic_summary(df: pd.DataFrame) -> dict[str, Any]:
    balance_col = _col(df, "Assets_End", "ending_balance", "total_assets")
    start_col = _col(df, "Assets_Start", "assets_start")
    if balance_col is None:
        return {"ending_balance": 0, "chart": [], "table": [], "export_rows": []}

    ending = _json_num(df[balance_col].iloc[-1]) or 0
    starting = _json_num(df[start_col].iloc[0]) if start_col else ending
    peak = _json_num(df[balance_col].max()) or 0
    notes_col = _col(df, "Formula_Notes", "formula_notes")
    deceased = False
    if notes_col:
        deceased = "Deceased:" in str(df[notes_col].iloc[0])

    return {
        "starting_balance": starting or 0,
        "ending_balance": ending,
        "peak_balance": peak,
        "chart": _sample_deterministic_chart(df),
        "table": _sample_table_rows(df),
        "export_rows": _dataframe_export_rows(df),
        "deceased_at_start": deceased,
    }


def _sample_mc_chart(df: pd.DataFrame, *, max_points: int = 28) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    def build_point(row: pd.Series, idx: int) -> dict[str, Any]:
        return {
            "year": _json_num(row.get("Year")),
            "age": _json_num(row.get("Age")),
            "p10": _json_num(row.get("P10")) or 0,
            "p50": _json_num(row.get("P50")) or 0,
            "p90": _json_num(row.get("P90")) or 0,
        }

    return _sample_series_rows(df, max_points=max_points, build_point=build_point)


def _sample_mc_table(df: pd.DataFrame, *, max_rows: int = 12) -> list[dict[str, Any]]:
    chart = _sample_mc_chart(df, max_points=max_rows)
    return [
        {
            "year": point["year"],
            "age": point["age"],
            "p10": point["p10"],
            "p50": point["p50"],
            "p90": point["p90"],
        }
        for point in chart
    ]


def _mc_summary(mc: MonteCarloResult) -> dict[str, Any]:
    return {
        "success_rate": _json_num(mc.success_rate),
        "median_ending": _json_num(mc.median_ending_balance),
        "p10_ending": _json_num(mc.p10_ending_balance),
        "p90_ending": _json_num(mc.p90_ending_balance),
        "paths": int(mc.num_paths),
        "paths_successful": int(mc.paths_successful),
        "summary_lines": mc.summary_text().split("\n"),
        "median_depletion_age": _json_num(mc.median_depletion_age),
        "severe_failure_rate": _json_num(mc.severe_failure_rate),
        "return_model": mc.return_model,
        "inflation_model": mc.inflation_model,
        "sampling_model": mc.sampling_model,
        "longevity_model": mc.longevity_model,
        "healthcare_model": mc.healthcare_model,
        "chart": _sample_mc_chart(mc.percentile_paths),
        "table": _sample_mc_table(mc.percentile_paths),
        "export_rows": _dataframe_export_rows(mc.percentile_paths),
        "errors": list(mc.errors),
    }


def _serialize_what_if(changes: list[Any] | None) -> list[dict[str, Any]]:
    if not changes:
        return []
    rows: list[dict[str, Any]] = []
    for change in changes:
        rows.append(
            {
                "field_key": change.field_key,
                "scope": change.scope,
                "person": change.person,
                "new_value": change.new_value,
                "label": change.label,
            }
        )
    return rows


def _serialize_adviser(
    payload: SimulationRunResult,
    *,
    risk_tolerance: str,
    holdings: list[Any] | None = None,
) -> dict[str, Any]:
    from adviser.advisor_engine import analyze

    try:
        report = analyze(
            payload.params,
            payload.output,
            holdings=holdings,
            risk_tolerance=risk_tolerance,  # type: ignore[arg-type]
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "goal_status": report.goal_status,
        "holdings_summary": report.holdings_summary,
        "success_rate": _json_num(report.success_rate),
        "target_success_rate": _json_num(report.target_success_rate),
        "asset_gap_estimate": _json_num(report.asset_gap_estimate),
        "total_portfolio_value": _json_num(report.total_portfolio_value),
        "recommendations": [
            {
                "title": rec.title,
                "detail": rec.detail,
                "category": rec.category,
                "what_if": _serialize_what_if(rec.what_if),
            }
            for rec in report.recommendations
        ],
        "checklist": list(report.checklist),
        "disclaimer": report.disclaimer,
    }


RESULTS_TAB_LABELS: dict[str, str] = {
    "joint": "Household",
    "self": "You",
    "wife": "Spouse",
    "scenarios": "Scenarios",
    "household": "Household",
}


def results_tab_label(key: str) -> str:
    return RESULTS_TAB_LABELS.get(key, key.replace("_", " ").title())


def serialize_simulation_result(
    payload: SimulationRunResult,
    *,
    risk_tolerance: str = "moderate",
    holdings: list[Any] | None = None,
) -> dict[str, Any]:
    output: SimulationOutput = payload.output
    tab_keys = sorted(
        set(output.deterministic.keys()) | set(output.monte_carlo.keys()),
        key=lambda name: (name != "joint", name),
    )
    tabs: list[dict[str, Any]] = []
    alerts: list[str] = []

    for key in tab_keys:
        label = results_tab_label(key)
        tab: dict[str, Any] = {"key": key, "label": label}
        mc = output.monte_carlo.get(key)
        if mc is not None:
            tab["monte_carlo"] = _mc_summary(mc)
            if mc.errors:
                alerts.extend(mc.errors)
        det = output.deterministic.get(key)
        if det is not None and not det.empty:
            summary = _deterministic_summary(det)
            tab["deterministic"] = summary
            if summary.get("deceased_at_start"):
                alerts.append(
                    f"{label}: projection starts with all household members marked deceased. "
                    "Check per-person Life Expectancy fields (blank = use shared value)."
                )
            elif (summary.get("peak_balance") or 0) <= 0 and (summary.get("starting_balance") or 0) <= 0:
                alerts.append(f"{label}: assets stayed at zero — review income, assets, and ages.")
        tabs.append(tab)

    return {
        "mode": output.mode,
        "risk_tolerance": risk_tolerance,
        "tabs": tabs,
        "adviser": _serialize_adviser(payload, risk_tolerance=risk_tolerance, holdings=holdings),
        "warnings": payload.warnings,
        "alerts": alerts,
    }
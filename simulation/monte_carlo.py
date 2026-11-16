"""Monte Carlo retirement simulation with historical bootstrap sampling."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from threading import Event

import numpy as np
import pandas as pd

from model.projection import (
    _horizon_start_age,
    _projection_kwargs,
    _project_for_household,
    _project_for_person,
    _validate_params,
)
from model.aca import AcaPolicy
from model.medicare import MedicarePolicy
from providers.fred_provider import get_cpi_annual_inflation_history
from providers.market_data import (
    BOND_SYMBOL,
    SP500_SYMBOL,
    get_annual_returns,
    get_annual_returns_by_year,
)
from settings import get_settings
from simulation.cancel import SimulationCancelled

DEFAULT_NUM_PATHS = 1000
ProgressCallback = Callable[[int, int], None]
DEFAULT_LOOKBACK_YEARS = 10
DEFAULT_LONGEVITY_STD = 5.0
DEFAULT_HEALTHCARE_COST_SIGMA = 0.20
HEALTHCARE_MULTIPLIER_CLIP = (0.55, 1.85)
MIN_CORRELATED_PAIRS = 5
DEFAULT_STOCK_ALLOCATION_PCT = 100.0


@dataclass
class MonteCarloResult:
    success_rate: float
    median_ending_balance: float
    p10_ending_balance: float
    p90_ending_balance: float
    percentile_paths: pd.DataFrame
    num_paths: int
    paths_successful: int
    return_model: str
    inflation_model: str
    sampling_model: str = "Independent bootstrap"
    longevity_model: str = ""
    healthcare_model: str = ""
    median_depletion_age: float | None = None
    mean_shortfall_years: float = 0.0
    severe_failure_rate: float = 0.0
    healthcare_multiplier_p10: float = 1.0
    healthcare_multiplier_p90: float = 1.0
    errors: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"Success rate: {self.success_rate:.1%} "
            f"({self.paths_successful:,} / {self.num_paths:,} paths)",
            f"Ending balance — 10th: ${self.p10_ending_balance:,.0f}  "
            f"50th: ${self.median_ending_balance:,.0f}  "
            f"90th: ${self.p90_ending_balance:,.0f}",
        ]
        if self.median_depletion_age is not None:
            lines.append(
                f"Failed paths — median depletion age: {self.median_depletion_age:.0f}, "
                f"avg years underwater: {self.mean_shortfall_years:.1f}"
            )
        if self.severe_failure_rate > 0:
            lines.append(f"Severe failures (10+ yrs underwater): {self.severe_failure_rate:.1%}")
        if self.healthcare_model:
            lines.append(
                f"Healthcare band — 10th: {self.healthcare_multiplier_p10:.0%}  "
                f"90th: {self.healthcare_multiplier_p90:.0%} of baseline"
            )
        return "\n".join(lines)


@dataclass
class SimulationOutput:
    """Container for deterministic and/or Monte Carlo results."""

    mode: str
    deterministic: dict[str, pd.DataFrame] = field(default_factory=dict)
    monte_carlo: dict[str, MonteCarloResult] = field(default_factory=dict)


def _projection_targets(params: dict) -> list[tuple[str, dict | str, str]]:
    persons = params["persons"]
    if params["mode"] == "joint":
        return [("joint", persons, "household")]
    return [
        (name, {**person, "retirement_age": person.get("retirement_age", 65)}, "person")
        for name, person in persons.items()
    ]


def _normalize_stock_allocation_pct(value: object) -> float:
    try:
        pct = float(value if value not in (None, "") else DEFAULT_STOCK_ALLOCATION_PCT)
    except (TypeError, ValueError):
        pct = DEFAULT_STOCK_ALLOCATION_PCT
    return max(0.0, min(100.0, pct))


def _blend_returns_by_year(
    stock_history: list[tuple[int, float]],
    bond_history: list[tuple[int, float]],
    stock_allocation_pct: float,
) -> list[tuple[int, float]]:
    stock_weight = stock_allocation_pct / 100.0
    bond_by_year = {year: rate for year, rate in bond_history}
    blended: list[tuple[int, float]] = []
    for year, stock_rate in stock_history:
        bond_rate = bond_by_year.get(year)
        if bond_rate is None:
            continue
        blended.append((year, stock_weight * stock_rate + (1.0 - stock_weight) * bond_rate))
    return blended


def _load_return_bootstrap(
    *,
    symbol: str,
    lookback_years: int,
    fallback_mean: float,
    stock_allocation_pct: float = DEFAULT_STOCK_ALLOCATION_PCT,
) -> tuple[list[float], str, list[str]]:
    errors: list[str] = []
    stock_pct = _normalize_stock_allocation_pct(stock_allocation_pct)
    try:
        if stock_pct >= 99.9:
            samples = get_annual_returns(symbol, lookback_years)
            label = f"Historical bootstrap ({symbol}, {len(samples)} yrs)"
            return samples, label, errors
        stock_history = get_annual_returns_by_year(symbol, lookback_years)
        bond_history = get_annual_returns_by_year(BOND_SYMBOL, lookback_years)
        blended = _blend_returns_by_year(stock_history, bond_history, stock_pct)
        if not blended:
            raise ValueError("no overlapping stock/bond years")
        samples = [rate for _, rate in blended]
        label = (
            f"Blended bootstrap ({stock_pct:.0f}% {symbol} / "
            f"{100 - stock_pct:.0f}% {BOND_SYMBOL}, {len(samples)} yrs)"
        )
        return samples, label, errors
    except Exception as exc:
        errors.append(f"Return bootstrap unavailable ({exc}); using fixed mean")
        return [fallback_mean], f"Fixed mean ({fallback_mean:.2%})", errors


def _load_inflation_bootstrap(
    *,
    lookback_years: int,
    fallback_mean: float,
    fred_api_key: str | None,
) -> tuple[list[float], str, list[str]]:
    errors: list[str] = []
    if not fred_api_key:
        errors.append("FRED_API_KEY missing; using fixed inflation mean")
        return [fallback_mean], f"Fixed mean ({fallback_mean:.2%})", errors

    try:
        history = get_cpi_annual_inflation_history(fred_api_key, lookback_years)
        samples = [rate for _, rate in history]
        label = f"CPI bootstrap ({len(samples)} yrs)"
        return samples, label, errors
    except Exception as exc:
        errors.append(f"Inflation bootstrap unavailable ({exc}); using fixed mean")
        return [fallback_mean], f"Fixed mean ({fallback_mean:.2%})", errors


def _load_correlated_bootstrap(
    *,
    symbol: str,
    lookback_years: int,
    fallback_return: float,
    fallback_inflation: float,
    fred_api_key: str | None,
    stock_allocation_pct: float = DEFAULT_STOCK_ALLOCATION_PCT,
) -> tuple[np.ndarray, np.ndarray, str, list[str]]:
    """Align historical returns and CPI inflation by calendar year for joint sampling."""
    errors: list[str] = []
    stock_pct = _normalize_stock_allocation_pct(stock_allocation_pct)
    try:
        stock_history = get_annual_returns_by_year(symbol, lookback_years)
        if stock_pct >= 99.9:
            return_history = stock_history
        else:
            bond_history = get_annual_returns_by_year(BOND_SYMBOL, lookback_years)
            return_history = _blend_returns_by_year(stock_history, bond_history, stock_pct)
    except Exception as exc:
        errors.append(f"Return bootstrap unavailable ({exc}); using fixed mean")
        return (
            np.asarray([fallback_return], dtype=float),
            np.asarray([fallback_inflation], dtype=float),
            "Independent bootstrap (returns unavailable)",
            errors,
        )

    if not fred_api_key:
        errors.append("FRED_API_KEY missing; using independent inflation bootstrap")
        return (
            np.asarray([r for _, r in return_history] or [fallback_return], dtype=float),
            np.asarray([fallback_inflation], dtype=float),
            "Independent bootstrap (no FRED key)",
            errors,
        )

    try:
        inflation_history = get_cpi_annual_inflation_history(fred_api_key, lookback_years)
    except Exception as exc:
        errors.append(f"Inflation bootstrap unavailable ({exc}); using fixed mean")
        return (
            np.asarray([r for _, r in return_history] or [fallback_return], dtype=float),
            np.asarray([fallback_inflation], dtype=float),
            "Independent bootstrap (inflation unavailable)",
            errors,
        )

    inflation_by_year = {int(year): rate for year, rate in inflation_history}
    paired_returns: list[float] = []
    paired_inflation: list[float] = []
    for year, rate in return_history:
        inflation_rate = inflation_by_year.get(year)
        if inflation_rate is None:
            continue
        paired_returns.append(rate)
        paired_inflation.append(inflation_rate)

    if len(paired_returns) < MIN_CORRELATED_PAIRS:
        errors.append(
            f"Only {len(paired_returns)} overlapping return/inflation years; "
            "using independent bootstrap"
        )
        return (
            np.asarray([r for _, r in return_history] or [fallback_return], dtype=float),
            np.asarray([rate for _, rate in inflation_history] or [fallback_inflation], dtype=float),
            "Independent bootstrap (insufficient overlap)",
            errors,
        )

    blend_note = ""
    if stock_pct < 99.9:
        blend_note = f", {stock_pct:.0f}% stock / {100 - stock_pct:.0f}% bonds"
    label = f"Correlated bootstrap ({len(paired_returns)} paired yrs{blend_note})"
    return (
        np.asarray(paired_returns, dtype=float),
        np.asarray(paired_inflation, dtype=float),
        label,
        errors,
    )


def _sample_life_expectancies(
    rng: np.random.Generator,
    *,
    num_paths: int,
    base_life_expectancy: int,
    start_age: int,
    std: float = DEFAULT_LONGEVITY_STD,
) -> np.ndarray:
    floor = start_age + 5
    ceiling = 105
    sampled = rng.normal(base_life_expectancy, std, size=num_paths)
    rounded = np.rint(sampled).astype(int)
    return np.clip(rounded, floor, ceiling)


def _sample_healthcare_multipliers(
    rng: np.random.Generator,
    num_paths: int,
    *,
    sigma: float = DEFAULT_HEALTHCARE_COST_SIGMA,
) -> np.ndarray:
    low, high = HEALTHCARE_MULTIPLIER_CLIP
    multipliers = rng.lognormal(mean=0.0, sigma=sigma, size=num_paths)
    return np.clip(multipliers, low, high)


def _scale_healthcare_projection(
    *,
    persons: dict,
    medicare: MedicarePolicy,
    aca: AcaPolicy,
    multiplier: float,
) -> tuple[dict, MedicarePolicy, AcaPolicy]:
    if abs(multiplier - 1.0) < 1e-9:
        return persons, medicare, aca

    scaled_persons: dict = {}
    for name, person in persons.items():
        scaled = dict(person)
        for key in (
            "health_insurance_post_retire",
            "health_insurance_pre_medicare",
            "employer_health_monthly",
        ):
            raw = scaled.get(key)
            if raw not in (None, ""):
                scaled[key] = float(raw) * multiplier
        scaled_persons[name] = scaled

    scaled_medicare = replace(
        medicare,
        part_b_monthly=medicare.part_b_monthly * multiplier,
        part_d_monthly=medicare.part_d_monthly * multiplier,
    )
    selected = aca.selected_plan_monthly_age_21
    slcsp = aca.slcsp_monthly_age_21
    scaled_aca = replace(
        aca,
        benchmark_monthly_age_21=aca.benchmark_monthly_age_21 * multiplier,
        selected_plan_monthly_age_21=None if selected is None else selected * multiplier,
        slcsp_monthly_age_21=None if slcsp is None else slcsp * multiplier,
    )
    return scaled_persons, scaled_medicare, scaled_aca


def _mc_max_workers(num_paths: int) -> int:
    """Parallel MC path workers — tuned for Apple Silicon laptops (leave headroom for UI)."""
    raw = os.environ.get("RETIREMENT_MC_WORKERS", "").strip()
    if raw == "0":
        return 0
    if raw.isdigit():
        return max(0, min(int(raw), 8))
    if num_paths < 50:
        return 0
    cpus = os.cpu_count() or 4
    return max(1, min(4, cpus - 2))


@dataclass(frozen=True)
class _McPathOutcome:
    path_index: int
    path_assets: tuple[float, ...]
    ending_balance: float
    depletion_age: float | None
    years_underwater: float
    severe: bool


def _execute_mc_path(
    *,
    path_index: int,
    kind: str,
    settings: dict,
    target,
    return_rates: list[float],
    inflation_rates: list[float],
    life_expectancy: int,
    healthcare_multiplier: float,
    healthcare_uncertainty: bool,
) -> _McPathOutcome:
    path_years = len(return_rates)
    path_settings = dict(settings)
    path_target = target
    project = _project_for_household if kind == "household" else _project_for_person

    if healthcare_uncertainty:
        multiplier = float(healthcare_multiplier)
        if kind == "household":
            scaled_persons, scaled_medicare, scaled_aca = _scale_healthcare_projection(
                persons=target,
                medicare=settings["medicare"],
                aca=settings["aca"],
                multiplier=multiplier,
            )
            path_target = scaled_persons
            path_settings["medicare"] = scaled_medicare
            path_settings["aca"] = scaled_aca
        else:
            wrapped = {"self": target}
            scaled_persons, scaled_medicare, scaled_aca = _scale_healthcare_projection(
                persons=wrapped,
                medicare=settings["medicare"],
                aca=settings["aca"],
                multiplier=multiplier,
            )
            path_target = scaled_persons["self"]
            path_settings["medicare"] = scaled_medicare
            path_settings["aca"] = scaled_aca

    path_settings["life_expectancy"] = int(life_expectancy)
    df = project(
        path_target,
        **path_settings,
        annual_return_rates=return_rates,
        annual_inflation_rates=inflation_rates,
        include_notes=False,
    )
    path_assets = df["Assets_End"].to_numpy(dtype=float)
    depletion_age, years_underwater, severe = _path_shortfall_stats(df)
    return _McPathOutcome(
        path_index=path_index,
        path_assets=tuple(float(value) for value in path_assets),
        ending_balance=float(path_assets[-1]),
        depletion_age=depletion_age,
        years_underwater=years_underwater,
        severe=severe,
    )


def _execute_mc_path_spec(spec: dict) -> _McPathOutcome:
    return _execute_mc_path(**spec)


def _run_mc_paths_parallel(
    *,
    num_paths: int,
    kind: str,
    settings: dict,
    target,
    start_age: int,
    path_returns: np.ndarray,
    path_inflation: np.ndarray,
    path_life_expectancy: np.ndarray,
    path_healthcare: np.ndarray,
    healthcare_uncertainty: bool,
    cancel_event: Event | None,
    on_progress: ProgressCallback | None,
    target_index: int,
    total_targets: int,
) -> list[_McPathOutcome]:
    workers = _mc_max_workers(num_paths)
    chunk_size = max(25, num_paths // max(workers * 4, 1))
    specs: list[dict] = []
    for path_index in range(num_paths):
        path_years = int(path_life_expectancy[path_index] - start_age + 1)
        specs.append(
            {
                "path_index": path_index,
                "kind": kind,
                "settings": settings,
                "target": target,
                "return_rates": path_returns[path_index, :path_years].tolist(),
                "inflation_rates": path_inflation[path_index, :path_years].tolist(),
                "life_expectancy": int(path_life_expectancy[path_index]),
                "healthcare_multiplier": float(path_healthcare[path_index]),
                "healthcare_uncertainty": healthcare_uncertainty,
            }
        )

    outcomes: list[_McPathOutcome] = []
    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = []
        for start in range(0, len(specs), chunk_size):
            chunk = specs[start : start + chunk_size]
            futures.append(pool.submit(_run_mc_path_chunk, chunk))
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                raise SimulationCancelled()
            chunk_outcomes = future.result()
            outcomes.extend(chunk_outcomes)
            completed += len(chunk_outcomes)
            if on_progress:
                on_progress(
                    target_index * num_paths + completed,
                    total_targets * num_paths,
                )
    outcomes.sort(key=lambda item: item.path_index)
    return outcomes


def _run_mc_path_chunk(chunk: list[dict]) -> list[_McPathOutcome]:
    return [_execute_mc_path_spec(spec) for spec in chunk]


def _path_shortfall_stats(df: pd.DataFrame) -> tuple[float | None, float, bool]:
    """Return depletion age, years underwater, and severe-failure flag for one path."""
    assets = df["Assets_End"].to_numpy(dtype=float)
    ages = df["Age"].to_numpy(dtype=int)
    depleted = assets <= 0
    if not depleted.any():
        return None, 0.0, False

    first_idx = int(np.argmax(depleted))
    depletion_age = float(ages[first_idx])
    years_underwater = float(np.sum(depleted[first_idx:]))
    severe = years_underwater >= 10
    return depletion_age, years_underwater, severe


def _sample_macro_paths(
    rng: np.random.Generator,
    *,
    return_array: np.ndarray,
    inflation_array: np.ndarray,
    correlated: bool,
    num_paths: int,
    num_years: int,
) -> tuple[np.ndarray, np.ndarray]:
    if correlated:
        pool_size = len(return_array)
        indices = rng.integers(0, pool_size, size=(num_paths, num_years))
        return return_array[indices], inflation_array[indices]

    ret_indices = rng.integers(0, len(return_array), size=(num_paths, num_years))
    inf_indices = rng.integers(0, len(inflation_array), size=(num_paths, num_years))
    return return_array[ret_indices], inflation_array[inf_indices]


def run_monte_carlo_simulation(
    params: dict,
    *,
    num_paths: int = DEFAULT_NUM_PATHS,
    seed: int | None = None,
    return_symbol: str = SP500_SYMBOL,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
    fred_api_key: str | None = None,
    stochastic_longevity: bool = True,
    healthcare_uncertainty: bool = True,
    longevity_std: float = DEFAULT_LONGEVITY_STD,
    healthcare_sigma: float = DEFAULT_HEALTHCARE_COST_SIGMA,
    cancel_event: Event | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, MonteCarloResult]:
    """Run Monte Carlo paths and return percentile summaries per projection key."""
    _validate_params(params)
    if num_paths <= 0:
        raise ValueError("num_paths must be positive")

    settings = _projection_kwargs(params)
    projection_targets = _projection_targets(params)
    stock_allocation_pct = _normalize_stock_allocation_pct(params.get("mc_stock_allocation_pct"))
    return_pool, return_model, return_errors = _load_return_bootstrap(
        symbol=return_symbol,
        lookback_years=lookback_years,
        fallback_mean=settings["investment_return_mean"],
        stock_allocation_pct=stock_allocation_pct,
    )
    inflation_pool, inflation_model, inflation_errors = _load_inflation_bootstrap(
        lookback_years=lookback_years,
        fallback_mean=settings["inflation_rate"],
        fred_api_key=fred_api_key,
    )
    paired_returns, paired_inflation, sampling_model, sampling_errors = _load_correlated_bootstrap(
        symbol=return_symbol,
        lookback_years=lookback_years,
        fallback_return=settings["investment_return_mean"],
        fallback_inflation=settings["inflation_rate"],
        fred_api_key=fred_api_key,
        stock_allocation_pct=stock_allocation_pct,
    )
    correlated = sampling_model.startswith("Correlated")

    rng = np.random.default_rng(seed)
    return_array = np.asarray(return_pool, dtype=float)
    inflation_array = np.asarray(inflation_pool, dtype=float)
    longevity_model = (
        f"Normal(μ={settings['life_expectancy']}, σ={longevity_std:.0f} yrs)"
        if stochastic_longevity
        else f"Fixed horizon (age {settings['life_expectancy']})"
    )
    healthcare_model = (
        f"Lognormal(σ={healthcare_sigma:.2f}) clipped "
        f"{HEALTHCARE_MULTIPLIER_CLIP[0]:.0%}–{HEALTHCARE_MULTIPLIER_CLIP[1]:.0%}"
        if healthcare_uncertainty
        else "Fixed baseline healthcare costs"
    )

    results: dict[str, MonteCarloResult] = {}
    total_targets = len(projection_targets)
    for target_index, (key, target, kind) in enumerate(projection_targets):
        if cancel_event and cancel_event.is_set():
            raise SimulationCancelled()
        start_age = _horizon_start_age(target if kind == "person" else target)
        base_life_expectancy = int(settings["life_expectancy"])
        if stochastic_longevity:
            path_life_expectancy = _sample_life_expectancies(
                rng,
                num_paths=num_paths,
                base_life_expectancy=base_life_expectancy,
                start_age=start_age,
                std=longevity_std,
            )
            max_years = int(np.max(path_life_expectancy - start_age + 1))
        else:
            path_life_expectancy = np.full(num_paths, base_life_expectancy, dtype=int)
            max_years = base_life_expectancy - start_age + 1

        if healthcare_uncertainty:
            path_healthcare = _sample_healthcare_multipliers(
                rng,
                num_paths,
                sigma=healthcare_sigma,
            )
        else:
            path_healthcare = np.ones(num_paths, dtype=float)

        path_returns, path_inflation = _sample_macro_paths(
            rng,
            return_array=paired_returns if correlated else return_array,
            inflation_array=paired_inflation if correlated else inflation_array,
            correlated=correlated,
            num_paths=num_paths,
            num_years=max_years,
        )

        assets_by_path = np.full((num_paths, max_years), np.nan, dtype=float)
        ending_balances = np.zeros(num_paths, dtype=float)
        depletion_ages: list[float] = []
        shortfall_years: list[float] = []
        severe_failures = 0

        years: list[int] | None = None
        ages: list[int] | None = None
        use_parallel = _mc_max_workers(num_paths) > 0

        if use_parallel:
            outcomes = _run_mc_paths_parallel(
                num_paths=num_paths,
                kind=kind,
                settings=settings,
                target=target,
                start_age=start_age,
                path_returns=path_returns,
                path_inflation=path_inflation,
                path_life_expectancy=path_life_expectancy,
                path_healthcare=path_healthcare,
                healthcare_uncertainty=healthcare_uncertainty,
                cancel_event=cancel_event,
                on_progress=on_progress,
                target_index=target_index,
                total_targets=total_targets,
            )
            for outcome in outcomes:
                path_years = len(outcome.path_assets)
                if years is None:
                    years = list(range(settings["current_year"], settings["current_year"] + max_years))
                    ages = list(range(start_age, start_age + max_years))
                assets_by_path[outcome.path_index, :path_years] = np.asarray(
                    outcome.path_assets,
                    dtype=float,
                )
                ending_balances[outcome.path_index] = outcome.ending_balance
                if outcome.depletion_age is not None:
                    depletion_ages.append(outcome.depletion_age)
                    shortfall_years.append(outcome.years_underwater)
                    if outcome.severe:
                        severe_failures += 1
        else:
            for path_index in range(num_paths):
                if cancel_event and cancel_event.is_set():
                    raise SimulationCancelled()
                if on_progress and (path_index == 0 or path_index == num_paths - 1 or path_index % 25 == 0):
                    completed = target_index * num_paths + path_index + 1
                    on_progress(completed, total_targets * num_paths)
                path_years = int(path_life_expectancy[path_index] - start_age + 1)
                outcome = _execute_mc_path(
                    path_index=path_index,
                    kind=kind,
                    settings=settings,
                    target=target,
                    return_rates=path_returns[path_index, :path_years].tolist(),
                    inflation_rates=path_inflation[path_index, :path_years].tolist(),
                    life_expectancy=int(path_life_expectancy[path_index]),
                    healthcare_multiplier=float(path_healthcare[path_index]),
                    healthcare_uncertainty=healthcare_uncertainty,
                )
                if years is None:
                    years = list(range(settings["current_year"], settings["current_year"] + max_years))
                    ages = list(range(start_age, start_age + max_years))

                path_assets = np.asarray(outcome.path_assets, dtype=float)
                assets_by_path[path_index, :path_years] = path_assets
                ending_balances[path_index] = outcome.ending_balance
                if outcome.depletion_age is not None:
                    depletion_ages.append(outcome.depletion_age)
                    shortfall_years.append(outcome.years_underwater)
                    if outcome.severe:
                        severe_failures += 1

        paths_successful = int(np.sum(ending_balances > 0))
        percentile_paths = pd.DataFrame(
            {
                "Year": years,
                "Age": ages,
                "P10": np.nanpercentile(assets_by_path, 10, axis=0),
                "P50": np.nanpercentile(assets_by_path, 50, axis=0),
                "P90": np.nanpercentile(assets_by_path, 90, axis=0),
            }
        )

        median_depletion = float(np.median(depletion_ages)) if depletion_ages else None
        mean_shortfall = float(np.mean(shortfall_years)) if shortfall_years else 0.0

        results[key] = MonteCarloResult(
            success_rate=paths_successful / num_paths,
            median_ending_balance=float(np.percentile(ending_balances, 50)),
            p10_ending_balance=float(np.percentile(ending_balances, 10)),
            p90_ending_balance=float(np.percentile(ending_balances, 90)),
            percentile_paths=percentile_paths,
            num_paths=num_paths,
            paths_successful=paths_successful,
            return_model=return_model,
            inflation_model=inflation_model,
            sampling_model=sampling_model,
            longevity_model=longevity_model,
            healthcare_model=healthcare_model,
            median_depletion_age=median_depletion,
            mean_shortfall_years=mean_shortfall,
            severe_failure_rate=severe_failures / num_paths,
            healthcare_multiplier_p10=float(np.percentile(path_healthcare, 10)),
            healthcare_multiplier_p90=float(np.percentile(path_healthcare, 90)),
            errors=return_errors + inflation_errors + sampling_errors,
        )

    return results


def run_simulation(
    params: dict,
    *,
    simulation_mode: str = "deterministic",
    num_paths: int = DEFAULT_NUM_PATHS,
    seed: int | None = None,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
    cancel_event: Event | None = None,
    on_progress: ProgressCallback | None = None,
) -> SimulationOutput:
    """Run deterministic and/or Monte Carlo simulation based on ``simulation_mode``."""
    from model.projection import run_retirement_projection

    if simulation_mode not in {"deterministic", "monte_carlo", "both"}:
        raise ValueError("simulation_mode must be 'deterministic', 'monte_carlo', or 'both'")

    deterministic: dict[str, pd.DataFrame] = {}
    monte_carlo: dict[str, MonteCarloResult] = {}

    if simulation_mode in {"deterministic", "both"}:
        if cancel_event and cancel_event.is_set():
            raise SimulationCancelled()
        deterministic = run_retirement_projection(params)
        if on_progress:
            on_progress(1, 1)

    if simulation_mode in {"monte_carlo", "both"}:
        if cancel_event and cancel_event.is_set():
            raise SimulationCancelled()
        settings = get_settings()
        monte_carlo = run_monte_carlo_simulation(
            params,
            num_paths=num_paths,
            seed=seed,
            lookback_years=lookback_years,
            fred_api_key=settings.fred_api_key,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )

    return SimulationOutput(
        mode=simulation_mode,
        deterministic=deterministic,
        monte_carlo=monte_carlo,
    )
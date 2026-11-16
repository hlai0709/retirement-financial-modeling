"""Run simulations and validate tax returns from a PlanningSnapshot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from app.core.action_result import ActionResult
from app.core.planning_snapshot import PlanningSnapshot
from model import collect_param_warnings
from simulation.cancel import SimulationCancelled
from simulation.monte_carlo import SimulationOutput, run_simulation
from validation.tax_return_validator import TaxReturnValidationResult, validate_tax_return_file

ProgressCallback = Callable[[int, int], None]

# Map validation ValueError text → plan field + guided step for jump-to-edit UI
_INPUT_ERROR_FIELDS: list[tuple[str, str, str]] = [
    ("state_move_year", "state_move_year", "tax_benefits"),
    ("state_after_move", "state_after_move", "tax_benefits"),
    ("state_of_residence", "state_of_residence", "setup"),
    ("marriage_year", "marriage_year", "timeline"),
    ("filing_status", "filing_status", "tax_benefits"),
    ("tax_calculation", "tax_calculation", "tax_benefits"),
    ("ss_tax_method", "ss_tax_method", "tax_benefits"),
    ("state_tax_override", "state_tax_override_pct", "tax_benefits"),
    ("Monte Carlo paths", "num_paths", "assumptions"),
]


def _input_error_payload(message: str) -> dict | None:
    lower = message.lower()
    for needle, field_key, step_id in _INPUT_ERROR_FIELDS:
        if needle.lower() in lower or field_key.lower() in lower:
            return {
                "field_key": field_key,
                "step_id": step_id,
                "label": field_key.replace("_", " ").title(),
            }
    return None


@dataclass
class SimulationRunRequest:
    snapshot: PlanningSnapshot
    confirm_cpa_mc_paths: bool = True


@dataclass
class SimulationRunResult:
    output: SimulationOutput
    params: dict
    warnings: list[str]


class SimulationController:
    def collect_warnings(self, snapshot: PlanningSnapshot) -> list[str]:
        return collect_param_warnings(snapshot.to_simulation_params())

    def needs_cpa_mc_confirmation(self, snapshot: PlanningSnapshot) -> bool:
        sim = snapshot.simulation
        return (
            sim.planning_precision == "cpa"
            and sim.simulation_mode in {"monte_carlo", "both"}
            and sim.num_paths > 1500
        )

    def run(
        self,
        request: SimulationRunRequest,
        *,
        cancel_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ActionResult:
        snapshot = request.snapshot
        sim = snapshot.simulation

        if sim.num_paths <= 0:
            return ActionResult.failure("Input Error", "Monte Carlo paths must be positive.")

        if self.needs_cpa_mc_confirmation(snapshot) and not request.confirm_cpa_mc_paths:
            return ActionResult.failure(
                "CPA Precision + Monte Carlo",
                f"Consider 1,000 paths or fewer. Requested: {sim.num_paths:,}.",
                level="warning",
            )

        params = snapshot.to_simulation_params()
        warnings = self.collect_warnings(snapshot)
        try:
            output = run_simulation(
                params,
                simulation_mode=sim.simulation_mode,
                num_paths=sim.num_paths,
                seed=sim.seed,
                cancel_event=cancel_event,
                on_progress=on_progress,
            )
        except SimulationCancelled:
            return ActionResult.failure("Simulation Cancelled", "Simulation was cancelled.", level="warning")
        except ValueError as exc:
            msg = str(exc)
            return ActionResult.failure(
                "Input Error",
                f"Invalid input: {msg}",
                payload=_input_error_payload(msg),
            )
        except Exception as exc:
            return ActionResult.failure("Simulation Error", str(exc))

        return ActionResult.success(
            "Simulation Complete",
            payload=SimulationRunResult(output=output, params=params, warnings=warnings),
        )

    def validate_tax_return(self, path: Path) -> ActionResult:
        try:
            result = validate_tax_return_file(path, auto_activate_policy=True)
        except Exception as exc:
            return ActionResult.failure("Tax Return Validation Error", str(exc))

        title = "Tax Return Validation — PASS" if result.passed else "Tax Return Validation — FAIL"
        return ActionResult.success(title, result.summary_text(), payload=result)
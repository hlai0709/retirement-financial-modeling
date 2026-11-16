"""Run simulations without any UI toolkit."""

from __future__ import annotations

from threading import Event
from typing import Callable

from app.core.action_result import ActionResult
from app.core.controllers.simulation_controller import SimulationController, SimulationRunRequest
from app.core.planning_snapshot import PlanningSnapshot

ProgressCallback = Callable[[int, int], None]


class SimulationService:
    """UI-agnostic simulation runner."""

    def __init__(self) -> None:
        self._controller = SimulationController()

    def collect_warnings(self, snapshot: PlanningSnapshot) -> list[str]:
        return self._controller.collect_warnings(snapshot)

    def needs_cpa_mc_confirmation(self, snapshot: PlanningSnapshot) -> bool:
        return self._controller.needs_cpa_mc_confirmation(snapshot)

    def run(
        self,
        snapshot: PlanningSnapshot,
        *,
        confirm_cpa_mc_paths: bool = True,
        cancel_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ActionResult:
        request = SimulationRunRequest(snapshot=snapshot, confirm_cpa_mc_paths=confirm_cpa_mc_paths)
        return self._controller.run(request, cancel_event=cancel_event, on_progress=on_progress)
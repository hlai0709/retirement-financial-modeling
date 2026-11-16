"""Build Lazy Mode wizard screen payloads — section-by-section document guide."""

from __future__ import annotations

from typing import Any

from app.core.lazy_mode.sections import build_lazy_screen_at, lazy_section_count
from app.core.planning_snapshot import PlanningSnapshot
from app.core.workflow_definition import WorkflowDefinition


def build_lazy_screen(
    snapshot: PlanningSnapshot,
    workflow: WorkflowDefinition,
    *,
    index: int | None = None,
) -> dict[str, Any]:
    step_index = snapshot.wizard.lazy_step_index if index is None else index
    return build_lazy_screen_at(snapshot, workflow, step_index)


def lazy_total_steps(snapshot: PlanningSnapshot) -> int:
    return lazy_section_count(snapshot)
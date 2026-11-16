"""Ordered guided wizard steps derived from UI groups."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ui_groups import UI_GROUPS, UiGroup, fields_for_ui_group


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    label: str
    person_scope: str
    sort_order: int
    optional: bool
    expert_only: bool
    field_count: int

    @property
    def is_household(self) -> bool:
        return self.person_scope == "household"


class WorkflowDefinition:
    """Cached wizard step list — built once per household mode."""

    def __init__(self, *, household_mode: str = "joint", include_spouse: bool = True) -> None:
        self.household_mode = household_mode
        self.include_spouse = include_spouse
        self._steps = self._build_steps()

    def _build_steps(self) -> tuple[WorkflowStep, ...]:
        steps: list[WorkflowStep] = []
        for group in sorted(UI_GROUPS, key=lambda g: g.sort_order):
            if group.expert_only:
                continue
            if group.person_scope == "wife" and not self.include_spouse:
                continue
            if group.step_id in {"review"}:
                field_count = 0
            elif group.person_scope in ("self", "wife"):
                field_count = len(fields_for_ui_group(group.step_id))
            elif group.person_scope == "shared":
                field_count = len(fields_for_ui_group(group.step_id))
            else:
                field_count = 0

            steps.append(
                WorkflowStep(
                    step_id=group.step_id,
                    label=group.label,
                    person_scope=group.person_scope,
                    sort_order=group.sort_order,
                    optional=group.optional_in_guided,
                    expert_only=group.expert_only,
                    field_count=field_count,
                )
            )
        return tuple(steps)

    @property
    def steps(self) -> tuple[WorkflowStep, ...]:
        return self._steps

    def step_ids(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self._steps)

    def index_of(self, step_id: str) -> int:
        for idx, step in enumerate(self._steps):
            if step.step_id == step_id:
                return idx
        return 0

    def step_at(self, index: int) -> WorkflowStep:
        return self._steps[max(0, min(index, len(self._steps) - 1))]

    def next_step_id(self, step_id: str) -> str | None:
        idx = self.index_of(step_id)
        if idx + 1 >= len(self._steps):
            return None
        return self._steps[idx + 1].step_id

    def prev_step_id(self, step_id: str) -> str | None:
        idx = self.index_of(step_id)
        if idx <= 0:
            return None
        return self._steps[idx - 1].step_id
"""Weighted plan completeness and guided journey rail — UI-agnostic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.controllers.llm_research_controller import field_display_label
from app.core.planning_snapshot import PlanningSnapshot, _blank_field_text
from app.core.presentation.guided_screens import JOURNEY_LABELS
from app.core.ui_groups import ui_group_for_field
from app.core.workflow_definition import WorkflowDefinition
from app.core.presentation.field_validation import validate_snapshot_field
from config.field_values import parse_field_value
from config.model_fields import FIELDS_BY_KEY, SHARED_FIELDS, ModelField

_SHARED_BY_KEY: dict[str, ModelField] = {field.key: field for field in SHARED_FIELDS}

# Zero is a valid intentional answer when the user has engaged with the field or step.
_ZERO_OK_IF_ENGAGED = frozenset(
    {
        "ss_amount",
        "annual_earned_income",
        "health_insurance_pre_medicare",
    }
)


@dataclass(frozen=True)
class _CheckField:
    field_key: str
    person: str | None = None


@dataclass(frozen=True)
class _CheckGroup:
    id: str
    label: str
    weight: float
    step_id: str
    fields: tuple[_CheckField, ...]
    wife_step_id: str | None = None


def _timeline_fields(*, joint: bool) -> tuple[_CheckField, ...]:
    fields: list[_CheckField] = [
        _CheckField("current_age", "self"),
        _CheckField("birth_year", "self"),
        _CheckField("retirement_age", "self"),
        _CheckField("life_expectancy", None),
    ]
    if joint:
        fields.append(_CheckField("marriage_year", None))
    return tuple(fields)


def _groups_for_mode(household_mode: str) -> tuple[_CheckGroup, ...]:
    joint = household_mode == "joint"
    groups: list[_CheckGroup] = [
        _CheckGroup(
            "timeline",
            "Timeline & ages",
            0.18,
            "timeline",
            _timeline_fields(joint=joint),
            wife_step_id="timeline" if joint else None,
        ),
        _CheckGroup(
            "assets",
            "Initial assets",
            0.17,
            "assets_self",
            (_CheckField("initial_assets", "self"),),
            wife_step_id="assets_wife" if joint else None,
        ),
        _CheckGroup(
            "expenses",
            "Base expenses",
            0.17,
            "spending_self",
            (_CheckField("base_expenses", "self"),),
            wife_step_id="spending_wife" if joint else None,
        ),
        _CheckGroup(
            "ss_pension",
            "Social Security & pension",
            0.10,
            "tax_benefits",
            (
                _CheckField("ss_start_age", "self"),
                _CheckField("ss_amount", "self"),
            ),
            wife_step_id="tax_wife" if joint else None,
        ),
        _CheckGroup(
            "tax",
            "Tax assumptions",
            0.10,
            "tax_benefits",
            (
                _CheckField("pre_retire_tax_rate", None),
                _CheckField("post_retire_tax_rate", None),
            ),
        ),
        _CheckGroup(
            "healthcare",
            "Healthcare",
            0.10,
            "healthcare_self",
            (_CheckField("health_insurance_pre_medicare", "self"),),
            wife_step_id="healthcare_wife" if joint else None,
        ),
        _CheckGroup(
            "market",
            "Market assumptions",
            0.07,
            "market",
            (
                _CheckField("inflation_rate", None),
                _CheckField("investment_return_mean", None),
            ),
        ),
        _CheckGroup(
            "income",
            "Earned income",
            0.08,
            "income_self",
            (_CheckField("annual_earned_income", "self"),),
            wife_step_id="income_wife" if joint else None,
        ),
        _CheckGroup(
            "setup",
            "Household setup",
            0.03,
            "setup",
            (_CheckField("state_of_residence", None),),
        ),
    ]
    return tuple(groups)


def touch_field_key(field: ModelField, *, person: str | None) -> str:
    scope = person if field.scope == "person" else "shared"
    return f"{scope}:{field.key}"


def mark_field_touched(snapshot: PlanningSnapshot, field: ModelField, *, person: str | None) -> None:
    key = touch_field_key(field, person=person)
    if key not in snapshot.wizard.touched_fields:
        snapshot.wizard.touched_fields.append(key)


def mark_updates_touched(snapshot: PlanningSnapshot, updates: list[dict[str, Any]]) -> None:
    from app.core.presentation.field_derived import is_auto_derived_update

    for row in updates:
        field_key = row.get("key")
        if not field_key:
            continue
        if is_auto_derived_update(snapshot, row, updates=updates):
            continue
        field = resolve_completeness_field(str(field_key), person=row.get("person"))
        if field is not None:
            person = row.get("person") if field.scope == "person" else None
            mark_field_touched(snapshot, field, person=person)


def resolve_completeness_field(field_key: str, *, person: str | None) -> ModelField | None:
    if person is None and field_key in _SHARED_BY_KEY:
        return _SHARED_BY_KEY[field_key]
    return FIELDS_BY_KEY.get(field_key)


def seed_touched_fields_from_snapshot(snapshot: PlanningSnapshot) -> None:
    """Backfill touched flags for restored plans that predate touched_fields tracking."""
    if snapshot.wizard.touched_fields:
        return
    for group in _groups_for_mode(snapshot.household_mode):
        for spec, _step_id in _group_fields(group, joint=snapshot.household_mode == "joint"):
            field, person = _resolve_check_field(spec)
            if field is None:
                continue
            if _differs_from_blank_template(snapshot, field, person=person):
                mark_field_touched(snapshot, field, person=person)


def _wife_fields(group: _CheckGroup) -> tuple[_CheckField, ...]:
    if group.wife_step_id is None:
        return ()
    return tuple(
        _CheckField(field.field_key, "wife")
        for field in group.fields
        if field.person == "self"
    )


def _resolve_check_field(spec: _CheckField) -> tuple[ModelField | None, str | None]:
    """Map a completeness check to the correct ModelField + person scope."""
    if spec.person is None and spec.field_key in _SHARED_BY_KEY:
        return _SHARED_BY_KEY[spec.field_key], None
    field = FIELDS_BY_KEY.get(spec.field_key)
    if field is None:
        return None, None
    person = spec.person if field.scope == "person" else None
    return field, person


def _field_touched(snapshot: PlanningSnapshot, field: ModelField, *, person: str | None) -> bool:
    return touch_field_key(field, person=person) in snapshot.wizard.touched_fields


def _field_imported_or_live(snapshot: PlanningSnapshot, field: ModelField, *, person: str | None) -> bool:
    scope_person = person if field.scope == "person" else None
    return snapshot.source_key(scope_person, field.key) in snapshot.field_sources


def _differs_from_blank_template(
    snapshot: PlanningSnapshot, field: ModelField, *, person: str | None
) -> bool:
    blank = _blank_field_text(field, person=person).strip()
    return snapshot.get_raw(field, person=person).strip() != blank


def _user_engaged(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
    step_id: str,
) -> bool:
    return (
        _field_touched(snapshot, field, person=person)
        or step_id in snapshot.wizard.completed_steps
        or _field_imported_or_live(snapshot, field, person=person)
    )


def _engaged_value_satisfied(
    snapshot: PlanningSnapshot,
    field: ModelField,
    raw: str,
    *,
    person: str | None,
) -> bool:
    """After the user has engaged, accept intentional blanks and valid zeros."""
    if validate_snapshot_field(snapshot, field, person=person):
        return False
    if not raw.strip():
        return True
    try:
        parsed = parse_field_value(field, raw)
    except (TypeError, ValueError):
        return False
    if field.field_type in ("money", "rate_pct", "int", "age") and parsed == 0:
        return field.key in _ZERO_OK_IF_ENGAGED or field.field_type == "rate_pct"
    if field.field_type == "year" and parsed is None:
        return True
    return True


def _ss_benefit_inputs_satisfied(snapshot: PlanningSnapshot, *, person: str) -> bool:
    """True when SS mode has enough data (manual amount, PIA, AIME, or earnings path)."""
    person = person if person in {"self", "wife"} else "self"
    mode = str(snapshot.persons.get(person, {}).get("ss_benefit_mode") or "manual").strip().lower()
    values = snapshot.persons.get(person, {})

    def _positive_money(key: str) -> bool:
        try:
            return float(str(values.get(key) or "0").replace(",", "")) > 0
        except (TypeError, ValueError):
            return False

    if mode == "pia":
        return _positive_money("ss_pia_monthly")
    if mode in {"aime", "earnings"}:
        return (
            _positive_money("ss_aime")
            or _positive_money("ss_career_avg_earnings")
            or _positive_money("annual_earned_income")
        )
    return _positive_money("ss_amount")


def _completeness_satisfied(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
    step_id: str,
) -> bool:
    # Completeness list only checks ss_amount; honor PIA / AIME / earnings modes.
    if field.key == "ss_amount" and person in {"self", "wife"}:
        if _ss_benefit_inputs_satisfied(snapshot, person=person):
            return True

    raw = snapshot.get_raw(field, person=person).strip()

    if validate_snapshot_field(snapshot, field, person=person):
        return False

    if _user_engaged(snapshot, field, person=person, step_id=step_id):
        return _engaged_value_satisfied(snapshot, field, raw, person=person)

    if not raw:
        return False

    if not _differs_from_blank_template(snapshot, field, person=person):
        return False

    try:
        parsed = parse_field_value(field, raw)
    except (TypeError, ValueError):
        return False
    if field.field_type in ("money", "rate_pct", "int", "age") and parsed == 0:
        return False
    if field.field_type == "year" and parsed is None:
        return False
    return True


def _navigable_step_id(
    spec: _CheckField,
    *,
    fallback: str,
) -> str:
    """Map a completeness field to the wizard step where the user actually edits it."""
    field, person = _resolve_check_field(spec)
    if field is None:
        return fallback
    return ui_group_for_field(field, person=person)


def _group_fields(group: _CheckGroup, *, joint: bool) -> list[tuple[_CheckField, str]]:
    """Return (field spec, navigable step_id) pairs."""
    rows: list[tuple[_CheckField, str]] = [
        (field, _navigable_step_id(field, fallback=group.step_id)) for field in group.fields
    ]
    if joint:
        for field in _wife_fields(group):
            rows.append(
                (
                    field,
                    _navigable_step_id(field, fallback=group.wife_step_id or group.step_id),
                )
            )
    return rows


def compute_completeness(snapshot: PlanningSnapshot, workflow: WorkflowDefinition) -> dict[str, Any]:
    joint = snapshot.household_mode == "joint"
    groups_out: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    earned = 0.0
    total_weight = 0.0

    for group in _groups_for_mode(snapshot.household_mode):
        specs = _group_fields(group, joint=joint)
        if not specs:
            continue
        filled = 0
        for spec, step_id in specs:
            field, person = _resolve_check_field(spec)
            if field is None:
                continue
            if _completeness_satisfied(snapshot, field, person=person, step_id=step_id):
                filled += 1
            else:
                missing.append(
                    {
                        "field_key": spec.field_key,
                        "person": person,
                        "label": field_display_label(field, person=person),
                        "step_id": step_id,
                        "step_index": workflow.index_of(step_id),
                        "group": group.label,
                        "group_id": group.id,
                    }
                )
        ratio = filled / len(specs)
        earned += group.weight * ratio
        total_weight += group.weight
        groups_out.append(
            {
                "id": group.id,
                "label": group.label,
                "weight": group.weight,
                "percent": round(ratio * 100),
                "filled": filled,
                "total": len(specs),
                "step_id": group.step_id,
            }
        )

    percent = round((earned / total_weight * 100) if total_weight else 0)
    missing.sort(key=lambda row: (row["step_index"], row["label"]))

    missing_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in missing:
        missing_by_group.setdefault(str(row.get("group_id", "")), []).append(row)

    for group in groups_out:
        gid = str(group["id"])
        open_rows = missing_by_group.get(gid, [])
        group["missing_count"] = len(open_rows)
        if open_rows:
            group["jump_step_index"] = open_rows[0]["step_index"]
        else:
            group["jump_step_index"] = workflow.index_of(str(group["step_id"]))

    return {
        "percent": percent,
        "groups": groups_out,
        "missing": missing,
        "missing_count": len(missing),
        "ready": len(missing) == 0,
    }


def collect_invalid_fields(
    snapshot: PlanningSnapshot,
    workflow: WorkflowDefinition,
) -> list[dict[str, Any]]:
    """High-impact fields with values that fail semantic validation."""
    joint = snapshot.household_mode == "joint"
    invalid: list[dict[str, Any]] = []

    for group in _groups_for_mode(snapshot.household_mode):
        for spec, step_id in _group_fields(group, joint=joint):
            field, person = _resolve_check_field(spec)
            if field is None:
                continue
            message = validate_snapshot_field(snapshot, field, person=person)
            if not message:
                continue
            invalid.append(
                {
                    "field_key": spec.field_key,
                    "person": person,
                    "label": field_display_label(field, person=person),
                    "message": message,
                    "step_id": step_id,
                    "step_index": workflow.index_of(step_id),
                    "group": group.label,
                    "group_id": group.id,
                }
            )

    invalid.sort(key=lambda row: (row["step_index"], row["label"]))
    return invalid


def run_plan_readiness(snapshot: PlanningSnapshot, workflow: WorkflowDefinition) -> dict[str, Any]:
    """Gate Run plan on high-impact completeness and valid field values."""
    comp = compute_completeness(snapshot, workflow)
    invalid = collect_invalid_fields(snapshot, workflow)
    invalid_keys = {(row["field_key"], row.get("person")) for row in invalid}
    missing_only = [
        row
        for row in comp["missing"]
        if (row["field_key"], row.get("person")) not in invalid_keys
    ]
    open_sections = [
        {
            "id": group["id"],
            "label": group["label"],
            "percent": group["percent"],
            "missing_count": group.get("missing_count", 0),
            "jump_step_index": group.get("jump_step_index"),
            "fields": [
                {"label": row["label"], "step_index": row["step_index"]}
                for row in comp["missing"]
                if row.get("group_id") == group["id"]
            ],
            "invalid_fields": [
                {"label": row["label"], "message": row["message"], "step_index": row["step_index"]}
                for row in invalid
                if row.get("group_id") == group["id"]
            ],
        }
        for group in comp["groups"]
        if group.get("missing_count", 0) > 0 or any(row.get("group_id") == group["id"] for row in invalid)
    ]
    ready = comp["missing_count"] == 0 and not invalid
    return {
        **comp,
        "invalid": invalid,
        "invalid_count": len(invalid),
        "missing_only": missing_only,
        "missing_only_count": len(missing_only),
        "ready": ready,
        "open_sections": open_sections,
    }


def build_journey_rail(
    workflow: WorkflowDefinition,
    snapshot: PlanningSnapshot,
    *,
    current_index: int,
) -> list[dict[str, Any]]:
    blocks: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []

    for index, step in enumerate(workflow.steps):
        journey = JOURNEY_LABELS.get(step.step_id, "Your plan")
        if journey not in blocks:
            blocks[journey] = []
            order.append(journey)
        blocks[journey].append(
            {
                "step_id": step.step_id,
                "label": step.label,
                "index": index,
                "optional": step.optional,
                "completed": step.step_id in snapshot.wizard.completed_steps,
                "skipped": step.step_id in snapshot.wizard.skipped_steps,
                "current": index == current_index,
            }
        )

    return [{"journey": name, "steps": blocks[name]} for name in order]
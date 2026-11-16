"""Field-level validation for planning inputs — shared by web UI, Tk, and run gates."""

from __future__ import annotations

from typing import Any

from app.core.controllers.llm_research_controller import field_display_label
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.micro_screens import fields_for_micro_screen
from config.field_values import parse_field_value
from config.model_fields import ModelField, field_choice_values

# Zero is valid when the user explicitly enters it on these keys.
_ZERO_OK_KEYS = frozenset(
    {
        "ss_amount",
        "annual_earned_income",
        "health_insurance_pre_medicare",
        "initial_taxable_assets",
        "initial_traditional_ira",
        "initial_roth_ira",
        "annual_contributions",
        "pension_amount",
        "employer_health_monthly",
        "medicare_part_b_monthly",
        "medicare_part_d_monthly",
        "medigap_monthly",
        "aca_benchmark_monthly_age_21",
        "aca_slcsp_monthly_age_21",
        "aca_selected_plan_monthly_age_21",
    }
)

# Stored rate_pct values are fractions; user-facing entry is 0–100%.
_EXCLUSIVE_MAX_RATE_KEYS = frozenset(
    {
        "pre_retire_tax_rate",
        "post_retire_tax_rate",
        "roth_portfolio_pct",
        "contribution_roth_pct",
        "state_tax_override_pct",
        "ss_taxable_fraction",
        "asset_share_pct",
    }
)

_MAX_SS_TAXABLE_FRACTION = 0.85

# Blank is acceptable on Continue — format rules apply only when the user enters a value.
_OPTIONAL_FIELD_KEYS = frozenset(
    {
        "birth_year",
        "state_after_move",
        "state_move_year",
        "filing_status_before_marriage",
        "filing_status_after_marriage",
        "initial_taxable_assets",
        "initial_traditional_ira",
        "initial_roth_ira",
        "pension_start_age",
        "pension_amount",
        "ss_pia_monthly",
        "ss_aime",
        "ss_career_avg_earnings",
        "aca_benchmark_monthly_age_21",
        "aca_slcsp_monthly_age_21",
        "aca_selected_plan_monthly_age_21",
        "tsla_target_price",
        "tsla_target_year",
        "doomsday_drop_year",
        "state_tax_override_pct",
    }
)


def _normalize_raw(field: ModelField, raw: str) -> str:
    value = raw.strip()
    if field.field_type == "money":
        return value.replace(",", "").replace("$", "").strip()
    if field.field_type == "rate_pct":
        return value.replace("%", "").strip()
    return value


def _parse_error_message(field: ModelField) -> str:
    if field.field_type in ("age", "year", "int"):
        return "Enter a whole number"
    if field.field_type == "money":
        return "Enter a valid dollar amount"
    if field.field_type == "rate_pct":
        return "Enter a valid percentage"
    return "Invalid value"


def _validate_parsed(field: ModelField, parsed: Any) -> str | None:
    key = field.key

    if field.field_type == "age":
        age = int(parsed)
        if age <= 0:
            return "Age must be at least 1"
        if age > 120:
            return "Age must be 120 or less"
        return None

    if field.field_type == "year":
        if parsed is None:
            return "Enter a valid year (not a dollar balance)"
        year = int(parsed)
        if year < 1900 or year > 2100:
            return "Year must be between 1900 and 2100 (not a loan balance)"
        if key == "mortgage_payoff_year" and (year < 2020 or year > 2080):
            return "Mortgage payoff year looks wrong — use maturity year (e.g. 2045), not principal"
        return None

    if field.field_type == "int":
        val = int(parsed)
        if key == "life_expectancy":
            if val <= 0:
                return "Life expectancy must be positive"
            if val > 120:
                return "Life expectancy must be 120 or less"
        elif key == "medicare_start_age":
            if val < 65 or val > 70:
                return "Medicare start age must be between 65 and 70"
        elif key == "medicare_irmaa_lookback_years":
            if val < 0 or val > 3:
                return "Lookback years must be between 0 and 3"
        elif key in ("cobra_months", "ltc_duration_years", "num_paths") and val < 0:
            return "Cannot be negative"
        return None

    if field.field_type == "money":
        val = float(parsed)
        if val < 0:
            return "Amount cannot be negative"
        if val == 0 and key not in _ZERO_OK_KEYS:
            return None
        return None

    if field.field_type == "rate_pct":
        val = float(parsed)
        if val < 0:
            return "Rate cannot be negative"
        if key in _EXCLUSIVE_MAX_RATE_KEYS:
            if val >= 1:
                return "Rate must be less than 100%"
            if key == "ss_taxable_fraction" and val > _MAX_SS_TAXABLE_FRACTION:
                return "Rate must be 85% or less"
        elif val > 1:
            return "Rate must be 100% or less"
        return None

    return None


def validate_field_raw(
    field: ModelField,
    raw: str,
    *,
    person: str | None = None,
    required: bool = False,
) -> str | None:
    """Return an error message, or None if the value is acceptable."""
    _ = person
    normalized = _normalize_raw(field, raw)

    if not normalized:
        if required:
            return "This field is required"
        return None

    choices = field_choice_values(field)
    if choices and normalized not in choices:
        return "Choose one of the listed options"

    if field.field_type == "bool":
        if normalized.lower() not in ("true", "false"):
            return "Enter True or False"
        return None

    try:
        parsed = parse_field_value(field, normalized)
    except (TypeError, ValueError):
        return _parse_error_message(field)

    semantic = _validate_parsed(field, parsed)
    if semantic:
        return semantic

    if required and field.field_type in ("age", "int") and int(parsed) <= 0:
        return _validate_parsed(field, parsed) or "Enter a positive whole number"

    if required and field.field_type == "year" and (parsed is None or int(parsed) <= 0):
        return "Enter a valid year"

    return None


def _update_lookup_key(key: str, person: str | None) -> str:
    return f"{person or 'shared'}:{key}"


def _is_field_optional(field: ModelField, snapshot: PlanningSnapshot) -> bool:
    if field.key == "marriage_year":
        return snapshot.household_mode != "joint"
    return field.key in _OPTIONAL_FIELD_KEYS


def _is_effectively_empty(
    field: ModelField,
    raw: str,
    *,
    snapshot: PlanningSnapshot | None = None,
) -> bool:
    normalized = _normalize_raw(field, raw)
    if not normalized:
        return True
    if normalized in {"0", "0.0", "0.00"}:
        if snapshot is not None and not _is_field_optional(field, snapshot):
            return True
        if field.key in _OPTIONAL_FIELD_KEYS:
            return True
    return False


def _wizard_step_for_field(field: ModelField, *, person: str | None) -> str:
    from app.core.ui_groups import ui_group_for_field

    return ui_group_for_field(field, person=person)


def _required_fields_for_step(snapshot: PlanningSnapshot, step_id: str) -> set[tuple[str, str | None]]:
    from app.core.presentation.plan_completeness import _group_fields, _groups_for_mode, _resolve_check_field

    joint = snapshot.household_mode == "joint"
    required: set[tuple[str, str | None]] = set()
    for group in _groups_for_mode(snapshot.household_mode):
        for spec, _group_step_id in _group_fields(group, joint=joint):
            field, person = _resolve_check_field(spec)
            if field is None or _is_field_optional(field, snapshot):
                continue
            if _wizard_step_for_field(field, person=person) != step_id:
                continue
            required.add((field.key, person))
    if step_id == "timeline" and joint:
        required.add(("marriage_year", None))
    return required


def validate_updates(
    updates: list[dict[str, Any]],
    *,
    required: bool = False,
) -> list[dict[str, Any]]:
    """Validate a batch of field updates. Returns structured field_error dicts."""
    from app.core.presentation.field_editor import resolve_field

    errors: list[dict[str, Any]] = []
    for row in updates:
        key = row.get("key")
        if not key:
            continue
        field = resolve_field(str(key), scope=row.get("scope"), person=row.get("person"))
        if field is None:
            errors.append(
                {
                    "key": str(key),
                    "person": row.get("person"),
                    "label": str(key),
                    "message": f"Unknown field: {key}",
                }
            )
            continue
        person = row.get("person") if field.scope == "person" else None
        if field.scope == "person" and person not in ("self", "wife"):
            errors.append(
                {
                    "key": field.key,
                    "person": person,
                    "label": field.label,
                    "message": f"Person required for {field.key}",
                }
            )
            continue
        msg = validate_field_raw(
            field,
            str(row.get("value", "")),
            person=person,
            required=required,
        )
        if msg:
            errors.append(
                {
                    "key": field.key,
                    "person": person,
                    "label": field_display_label(field, person=person),
                    "message": msg,
                }
            )
    return errors


def validate_snapshot_field(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
) -> str | None:
    raw = snapshot.get_raw(field, person=person)
    if _is_effectively_empty(field, raw, snapshot=snapshot):
        if not _is_field_optional(field, snapshot) and not raw.strip():
            return "This field is required"
        return None
    return validate_field_raw(field, raw, person=person, required=False)


def visible_fields_for_step(
    snapshot: PlanningSnapshot,
    step_id: str,
) -> list[tuple[ModelField, str | None]]:
    """Return (field, person) pairs shown on the current wizard step."""
    from app.core.presentation.field_editor import person_for_step

    if step_id in {"setup", "live_data", "llm_assist", "import_documents", "review"}:
        return []
    person = person_for_step(step_id)
    sub_index = snapshot.wizard.sub_step_positions.get(
        step_id,
        snapshot.wizard.sub_step_index,
    )
    rows: list[tuple[ModelField, str | None]] = []
    for field in fields_for_micro_screen(step_id, sub_index):
        field_person = person if field.scope == "person" else None
        if field.scope == "person" and field_person is None:
            field_person = "self"
        rows.append((field, field_person))
    return rows


def _cross_field_errors(
    snapshot: PlanningSnapshot,
    step_id: str,
    values: dict[str, str],
    *,
    visible: list[tuple[ModelField, str | None]],
) -> list[dict[str, Any]]:
    """Step-level checks involving multiple fields — only when those fields are on this screen."""
    from app.core.presentation.field_editor import person_for_step, resolve_field

    visible_keys = {(field.key, person) for field, person in visible}
    errors: list[dict[str, Any]] = []

    def _age_value(key: str, person: str | None) -> int | None:
        field = resolve_field(key, person=person)
        if field is None:
            return None
        if (key, person) not in visible_keys:
            raw = snapshot.get_raw(field, person=person)
            if _is_effectively_empty(field, raw, snapshot=snapshot):
                return None
        raw = values.get(_update_lookup_key(key, person))
        if raw is None:
            raw = snapshot.get_raw(field, person=person)
        if _is_effectively_empty(field, raw, snapshot=snapshot):
            return None
        normalized = _normalize_raw(field, raw)
        if not normalized:
            return None
        try:
            parsed = int(parse_field_value(field, normalized))
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    if step_id == "timeline":
        person = person_for_step(step_id) or "self"
        current = _age_value("current_age", person)
        life_field = resolve_field("life_expectancy")
        if life_field is not None and ("life_expectancy", None) in visible_keys:
            raw = values.get(_update_lookup_key("life_expectancy", None))
            if raw is None:
                raw = snapshot.get_raw(life_field)
            if not _is_effectively_empty(life_field, raw, snapshot=snapshot):
                normalized = _normalize_raw(life_field, raw)
                if normalized:
                    try:
                        life = int(parse_field_value(life_field, normalized))
                        if current is not None and life <= current:
                            errors.append(
                                {
                                    "key": "life_expectancy",
                                    "person": None,
                                    "label": field_display_label(life_field),
                                    "message": "Life expectancy must be greater than current age",
                                }
                            )
                    except (TypeError, ValueError):
                        pass
        return errors

    if step_id in {"income_self", "income_wife"}:
        person = person_for_step(step_id) or "self"
        if ("retirement_age", person) not in visible_keys:
            return errors
        current = _age_value("current_age", person)
        retirement = _age_value("retirement_age", person)
        if current is not None and retirement is not None and retirement < current:
            field = resolve_field("retirement_age", person=person)
            if field:
                errors.append(
                    {
                        "key": "retirement_age",
                        "person": person,
                        "label": field_display_label(field, person=person),
                        "message": "Retirement age cannot be less than current age",
                    }
                )

    return errors


def validate_guided_step(
    snapshot: PlanningSnapshot,
    step_id: str,
    updates: list[dict[str, Any]] | None = None,
    *,
    required: bool = True,
) -> dict[str, Any]:
    """Validate visible fields on a guided step (Continue gate)."""
    from app.core.presentation.field_editor import resolve_field

    if step_id == "setup":
        return {"ok": True, "errors": [], "field_errors": []}

    visible = visible_fields_for_step(snapshot, step_id)
    if not visible:
        return {"ok": True, "errors": [], "field_errors": []}

    value_map: dict[str, str] = {}
    if updates:
        for row in updates:
            key = row.get("key")
            if not key:
                continue
            field = resolve_field(str(key), scope=row.get("scope"), person=row.get("person"))
            if field is None:
                continue
            person = row.get("person") if field.scope == "person" else None
            value_map[_update_lookup_key(str(key), person)] = str(row.get("value", ""))

    required_fields = _required_fields_for_step(snapshot, step_id) if required else set()
    field_errors: list[dict[str, Any]] = []
    for field, person in visible:
        raw = value_map.get(_update_lookup_key(field.key, person))
        if raw is None:
            raw = snapshot.get_raw(field, person=person)
        if _is_effectively_empty(field, raw, snapshot=snapshot):
            if required and (field.key, person) in required_fields:
                msg = "This field is required"
            else:
                msg = None
        else:
            must_fill = required and (field.key, person) in required_fields
            msg = validate_field_raw(field, raw, person=person, required=must_fill)
        if msg:
            field_errors.append(
                {
                    "key": field.key,
                    "person": person,
                    "label": field_display_label(field, person=person),
                    "message": msg,
                }
            )

    field_errors.extend(_cross_field_errors(snapshot, step_id, value_map, visible=visible))

    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for row in field_errors:
        token = (row["key"], row.get("person"))
        if token in seen:
            continue
        seen.add(token)
        deduped.append(row)

    errors = [f"{row['label']}: {row['message']}" for row in deduped]
    return {"ok": not deduped, "errors": errors, "field_errors": deduped}
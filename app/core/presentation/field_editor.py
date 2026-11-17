"""Read/write catalog fields on PlanningSnapshot (no Tk / web imports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.guided_catalog import guided_help_for_field, guided_question_for_field
from app.core.planning_snapshot import PlanningSnapshot
from app.core.ui_groups import _UI_GROUP_BY_ID, fields_for_ui_group
from config.field_values import parse_field_value
from config.model_fields import ALL_FIELDS, ModelField, default_display_value, field_choice_values

# Tokens that mean different things on different fields.
_FIELD_CHOICE_LABELS: dict[tuple[str, str], str] = {
    ("expense_phase_mode", "flat"): "Stay flat",
    ("expense_phase_mode", "age_banded"): "Change with age",
    ("tax_calculation", "federal_brackets"): "Federal tax brackets",
    ("tax_calculation", "flat"): "One flat rate",
    ("ss_benefit_mode", "manual"): "I'll enter the annual amount",
    ("aca_premium_mode", "manual"): "I'll enter the premium",
    ("medigap_plan", "none"): "No Medigap plan",
    ("ltc_mode", "none"): "No long-term care",
    ("ltc_mode", "annual"): "One annual cost",
    ("ltc_mode", "scenario"): "A care scenario",
    ("employer_health_mode", "none"): "No employer plan",
    ("roth_conversion_mode", "none"): "No Roth conversions",
}

_CHOICE_LABELS: dict[str, str] = {
    "True": "Yes",
    "False": "No",
    "": "Not set",
    "head_of_household": "Head of household",
    "single": "Single",
    "mfj": "Married filing jointly",
    "mfs": "Married filing separately",
    "irs_provisional": "IRS formula",
    "flat_fraction": "Flat percent of the benefit",
    "tax_efficient": "Tax-efficient order",
    "proportional": "Same share from each account",
    "standard": "Standard",
    "cpa": "CPA detail",
    "aca_subsidized": "ACA with subsidy",
    "employer": "Employer plan",
    "cobra": "COBRA",
    "plan_g": "Plan G",
    "plan_n": "Plan N",
    "plan_f": "Plan F",
    "home_health_aide": "Home health aide",
    "assisted_living": "Assisted living",
    "nursing_home": "Nursing home",
    "pia": "From the monthly PIA",
    "aime": "From average indexed earnings",
    "earnings": "From career earnings",
    "fixed": "A fixed amount each year",
    "bracket_fill": "Fill a tax bracket",
    "73": "Age 73",
    "75": "Age 75",
    "12": "12%",
    "22": "22%",
    "24": "24%",
    "32": "32%",
}

_CAPTION_OVERRIDES: dict[str, str] = {
    "current_age": "Age at the start of the plan.",
    "retirement_age": "Earned income and retirement savings stop at this age.",
    "life_expectancy": "The plan runs through this age.",
    "rental_income_start": "Annual rent received today, before it grows.",
    "rental_growth_rate": "How fast that rent rises each year.",
    "pension_start_age": "The age the pension check begins.",
    "ss_start_age": "The age the Social Security check begins.",
    "mortgage_annual": "Principal and interest only, not escrow.",
    "mortgage_second_annual": "Principal and interest only, not escrow.",
    "property_tax_annual": "What you pay the county. This is separate from the tax deduction.",
    "mortgage_payoff_year": "Leave blank if this payment continues for the whole plan.",
    "mortgage_second_payoff_year": "Leave blank if this payment continues for the whole plan.",
    "mortgage_payoff_month": "Leave blank to charge the full payoff year. Use 1–12.",
    "mortgage_second_payoff_month": "Leave blank to charge the full payoff year. Use 1–12.",
}

_EXAMPLE_CAPTION = "Example — replace with your number."


def choice_label(field_key: str, value: str) -> str:
    specific = _FIELD_CHOICE_LABELS.get((field_key, value))
    if specific:
        return specific
    if value in _CHOICE_LABELS:
        return _CHOICE_LABELS[value]
    pretty = value.replace("_", " ").strip()
    if not pretty:
        return "Not set"
    return pretty[:1].upper() + pretty[1:]


def one_line_caption(field: ModelField) -> str:
    override = _CAPTION_OVERRIDES.get(field.key)
    if override:
        return override
    text = " ".join((field.description or "").split())
    if not text:
        return ""
    sentence = text
    for mark in (". ", "; "):
        if mark in sentence:
            sentence = sentence.split(mark, 1)[0].rstrip(".") + "."
            break
    if len(sentence) > 160:
        sentence = sentence[:157].rstrip() + "…"
    return sentence


def field_is_example(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
) -> bool:
    """True when a personal amount or age is still the catalog sample.

    Submitting the page marks the field touched. That does not retire the
    sample: it stays an example until the number changes or a document sets it.
    """
    if field.field_type not in {"money", "age"}:
        return False
    scope_person = person if field.scope == "person" else None
    source = snapshot.field_sources.get(snapshot.source_key(scope_person, field.key), "")
    if source and source != "Manual":
        return False
    raw = snapshot.get_raw(field, person=scope_person).strip().replace(",", "").replace("$", "")
    default = str(default_display_value(field, person=scope_person) or "").strip().replace(",", "")
    if field.field_type == "money":
        try:
            default_num = float(default or "0")
            raw_num = float(raw or "0")
        except ValueError:
            return False
        return default_num != 0 and raw_num == default_num
    return bool(default) and default not in {"0"} and raw == default


def resolve_field(
    key: str,
    *,
    scope: str | None = None,
    person: str | None = None,
) -> ModelField | None:
    """Resolve catalog field when keys exist in both shared and person scopes."""
    matches = [field for field in ALL_FIELDS if field.key == key]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if scope in ("shared", "person"):
        for field in matches:
            if field.scope == scope:
                return field
    if person in ("self", "wife"):
        for field in matches:
            if field.scope == "person":
                return field
    for field in matches:
        if field.scope == "shared":
            return field
    return matches[0]


def fields_for_wizard_step(step_id: str):
    if step_id in {"setup", "live_data", "llm_assist", "import_documents", "review"}:
        return ()
    return fields_for_ui_group(step_id)


@dataclass(frozen=True)
class FieldUpdate:
    key: str
    value: str
    person: str | None = None
    scope: str | None = None


def person_for_step(step_id: str) -> str | None:
    group = _UI_GROUP_BY_ID.get(step_id)
    if group is None:
        return None
    if group.person_scope in ("self", "wife"):
        return group.person_scope
    return None


def control_type_for(field: ModelField) -> str:
    choices = field_choice_values(field)
    if choices:
        if field.field_type == "bool" and len(choices) <= 2:
            return "radio"
        return "select"
    if field.field_type == "year":
        return "year"
    if field.field_type in ("age", "int"):
        return "narrow"
    if field.field_type == "money":
        return "dollar"
    if field.field_type == "rate_pct":
        return "medium"
    return "medium"


def display_value(snapshot: PlanningSnapshot, field: ModelField, *, person: str | None) -> str:
    raw = snapshot.get_raw(field, person=person) or ""
    if not raw and field.field_type != "bool":
        return ""
    if field.field_type == "rate_pct":
        try:
            parsed = parse_field_value(field, raw)
            return f"{float(parsed) * 100:g}"
        except (TypeError, ValueError):
            return raw
    if field.field_type == "money" and raw:
        try:
            num = float(str(raw).replace(",", ""))
            if num == int(num):
                return f"{int(num):,}"
            return f"{num:,.2f}".rstrip("0").rstrip(".")
        except ValueError:
            return raw
    return raw


def field_to_spec(
    snapshot: PlanningSnapshot,
    field: ModelField,
    *,
    person: str | None,
    index: int,
) -> dict[str, Any]:
    """Serializable field row for any UI shell."""
    field_person = person if field.scope == "person" else None
    if field.scope == "person" and field_person is None:
        field_person = "self"
    choices = field_choice_values(field)
    value = display_value(snapshot, field, person=field_person)
    spec: dict[str, Any] = {
        "key": field.key,
        "scope": field.scope,
        "person": field_person,
        "type": control_type_for(field),
        "label": guided_question_for_field(
            field,
            person=field_person,
            household_mode=snapshot.household_mode,
        ),
        "value": value,
        "caption": one_line_caption(field),
        "captionTight": field.field_type in ("year", "int", "age"),
    }
    if field_is_example(snapshot, field, person=field_person):
        spec["example"] = True
        spec["caption"] = _EXAMPLE_CAPTION
    if choices:
        spec["options"] = list(choices)
        spec["optionLabels"] = [choice_label(field.key, option) for option in choices]
        if spec["type"] == "radio":
            spec["on"] = list(choices).index(value) if value in choices else -1
    if field.field_type == "rate_pct":
        spec["suffix"] = "%"
    return spec


def help_items_for_step(step_id: str) -> list[tuple[str, str]]:
    if step_id == "setup":
        return [
            ("Question 1", "Together (joint) — shared assets, combined taxes.\n\nSeparately — independent projections."),
            ("Question 2", "State of residence sets your state tax tables."),
        ]
    person = person_for_step(step_id)
    items: list[tuple[str, str]] = []
    for idx, field in enumerate(fields_for_wizard_step(step_id), start=1):
        field_person = person if field.scope == "person" else None
        if field.scope == "person" and field_person is None:
            field_person = "self"
        question = guided_question_for_field(field, person=field_person)
        body = guided_help_for_field(field) or "No additional help for this field."
        items.append((f"{idx} · {question}", body))
    return items


def apply_updates(snapshot: PlanningSnapshot, updates: list[FieldUpdate]) -> list[dict[str, Any]]:
    """Validate and persist field edits. Returns structured field errors (empty if ok)."""
    from app.core.controllers.llm_research_controller import field_display_label
    from app.core.presentation.field_validation import validate_field_raw

    errors: list[dict[str, Any]] = []
    for update in updates:
        field = resolve_field(update.key, scope=update.scope, person=update.person)
        if field is None:
            errors.append(
                {
                    "key": update.key,
                    "person": update.person,
                    "label": update.key,
                    "message": f"Unknown field: {update.key}",
                }
            )
            continue
        person = update.person if field.scope == "person" else None
        if field.scope == "person" and person not in ("self", "wife"):
            errors.append(
                {
                    "key": field.key,
                    "person": person,
                    "label": field.label,
                    "message": f"Person required for {update.key}",
                }
            )
            continue
        raw = update.value.strip()
        if field.field_type == "money":
            raw = raw.replace(",", "").replace("$", "").strip()
        if field.field_type == "rate_pct":
            raw = raw.replace("%", "").strip()
        msg = validate_field_raw(field, raw, person=person, required=False)
        if msg:
            errors.append(
                {
                    "key": field.key,
                    "person": person,
                    "label": field_display_label(field, person=person),
                    "message": msg,
                }
            )
            continue
        try:
            parsed = parse_field_value(field, raw)
            snapshot.set_parsed(field, parsed, person=person)
            if field.key == "mortgage_annual" and person in {"self", "wife"}:
                from app.core.presentation.spending_builder import (
                    sync_mortgage_into_spending_breakdown,
                )

                sync_mortgage_into_spending_breakdown(snapshot, person=person)
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "key": field.key,
                    "person": person,
                    "label": field_display_label(field, person=person),
                    "message": str(exc),
                }
            )
    return errors
"""Build guided wizard screen payloads — UI shells only render these dicts."""

from __future__ import annotations

from typing import Any

from app.core.planning_snapshot import PlanningSnapshot
from app.core.guided_catalog import STEP_GUIDED_INTROS
from app.core.presentation.field_editor import (
    display_value,
    field_is_example,
    field_to_spec,
    fields_for_wizard_step,
    help_items_for_step,
    person_for_step,
)
from app.core.presentation.marriage_answer import marriage_answer
from app.core.presentation.micro_screens import fields_for_micro_screen, micro_screen_meta
from app.core.ui_groups import _UI_GROUP_BY_ID
from app.core.workflow_definition import WorkflowDefinition, WorkflowStep
from config.model_fields import FIELDS_BY_KEY, ModelField, field_choice_values


JOURNEY_LABELS: dict[str, str] = {
    "setup": "Get started",
    "timeline": "Get started",
    "income_self": "Income & savings",
    "income_wife": "Income & savings",
    "assets_self": "What you own",
    "assets_wife": "What you own",
    "spending_self": "Spending",
    "spending_wife": "Spending",
    "housing": "Housing",
    "healthcare_self": "Healthcare",
    "healthcare_wife": "Healthcare",
    "tax_benefits": "Taxes & benefits",
    "tax_wife": "Taxes & benefits",
    "market": "Economy",
    "import_documents": "Import",
    "live_data": "Connect",
    "llm_assist": "Connect",
    "review": "Review & run",
}

_SIM_LABELS = ("Deterministic", "Monte Carlo", "Both")
_SIM_TO_MODE = {
    "Deterministic": "deterministic",
    "Single": "deterministic",
    "Monte Carlo": "monte_carlo",
    "Both": "both",
    "deterministic": "deterministic",
    "monte_carlo": "monte_carlo",
    "both": "both",
}
_MODE_TO_LABEL = {v: k for k, v in _SIM_TO_MODE.items() if k in _SIM_LABELS}


def _step_meta(step: WorkflowStep, *, index: int, total: int) -> dict[str, Any]:
    return {
        "num": index + 1,
        "stepId": step.step_id,
        "title": step.label.title() if step.step_id != "setup" else "Household Setup",
        "journey": JOURNEY_LABELS.get(step.step_id, "Your plan"),
        "optional": step.optional,
        "helpTitle": f"Help — {step.label}",
        "totalSteps": total,
    }


def _setup_screen(snapshot: PlanningSnapshot, step: WorkflowStep, *, index: int, total: int) -> dict[str, Any]:
    state_field = FIELDS_BY_KEY["state_of_residence"]
    states = list(field_choice_values(state_field) or ("CA",))
    household_on = 0 if snapshot.household_mode == "joint" else 1
    screen = _step_meta(step, index=index, total=total)
    screen["fields"] = [
        {
            "key": "household_mode",
            "type": "radio",
            "label": "How are you modeling your household?",
            "options": ["Together (joint)", "Separately"],
            "on": household_on,
        },
        {
            "key": "state_of_residence",
            "type": "select",
            "label": "Where do you live?",
            "value": snapshot.assumption_state,
            "options": states,
            "caption": "",
        },
    ]
    screen["help"] = [{"h": h, "p": p} for h, p in help_items_for_step("setup")]
    screen["footer"] = {"backDisabled": index <= 0}
    return screen


_EXAMPLE_REVIEW_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("current_age", "self", "Your age"),
    ("current_age", "wife", "Spouse's age"),
    ("retirement_age", "self", "Your retirement age"),
    ("retirement_age", "wife", "Spouse's retirement age"),
    ("initial_assets", "self", "Your assets"),
    ("initial_assets", "wife", "Spouse's assets"),
    ("base_expenses", "self", "Your spending"),
    ("base_expenses", "wife", "Spouse's spending"),
    ("annual_earned_income", "self", "Your income"),
    ("annual_earned_income", "wife", "Spouse's income"),
    ("ss_amount", "self", "Your Social Security"),
    ("ss_amount", "wife", "Spouse's Social Security"),
)


def guided_step_field_rows(
    snapshot: PlanningSnapshot,
    step_id: str,
) -> list[tuple[ModelField, str | None]]:
    """(field, person) rows for one guided step, including the spouse's age."""
    person = person_for_step(step_id)
    sub_index = snapshot.wizard.sub_step_positions.get(
        step_id,
        snapshot.wizard.sub_step_index,
    )
    rows: list[tuple[ModelField, str | None]] = []
    joint = snapshot.household_mode == "joint"
    for field in fields_for_micro_screen(step_id, sub_index):
        if joint and step_id == "timeline" and field.key in {"current_age", "birth_year"}:
            rows.append((field, "self"))
            rows.append((field, "wife"))
            continue
        field_person = person if field.scope == "person" else None
        if field.scope == "person" and field_person is None:
            field_person = "self"
        rows.append((field, field_person))
    return rows


def _already_married_spec(snapshot: PlanningSnapshot) -> dict[str, Any]:
    answer = marriage_answer(snapshot)
    on = {"yes": 0, "no": 1}.get(answer, -1)
    return {
        "key": "already_married",
        "scope": "shared",
        "type": "radio",
        "label": "Are you married?",
        "options": ["yes", "no"],
        "optionLabels": ["Yes", "Not yet"],
        "on": on,
        "value": answer,
        "caption": "Yes files jointly every year. Not yet asks for the year you plan to marry.",
    }


def _example_review_rows(snapshot: PlanningSnapshot) -> list[dict[str, Any]]:
    from app.core.ui_groups import ui_group_for_field

    joint = snapshot.household_mode == "joint"
    rows: list[dict[str, Any]] = []
    for key, person, label in _EXAMPLE_REVIEW_FIELDS:
        if person == "wife" and not joint:
            continue
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            continue
        scope_person = person if field.scope == "person" else None
        if not field_is_example(snapshot, field, person=scope_person):
            continue
        shown = display_value(snapshot, field, person=scope_person)
        if field.field_type == "money" and shown:
            shown = f"${shown}"
        rows.append(
            {
                "field_key": key,
                "person": scope_person,
                "h": label,
                "p": f"{shown} is still the sample number",
                "step_id": ui_group_for_field(field, person=scope_person),
            }
        )
    return rows


def _review_screen(snapshot: PlanningSnapshot, step: WorkflowStep, *, index: int, total: int) -> dict[str, Any]:
    params = snapshot.to_simulation_params()
    self_p = params["persons"]["self"]
    wife_p = params["persons"].get("wife", {})
    self_assets = self_p.get("initial_assets", 0)
    wife_assets = wife_p.get("initial_assets", 0)
    total_assets = float(self_assets) + float(wife_assets)
    retire_self = self_p.get("retirement_age", "—")
    retire_wife = wife_p.get("retirement_age", "—")
    inflation = params.get("inflation_rate", 0)
    returns = params.get("investment_return_mean", 0)

    screen = _step_meta(step, index=index, total=total)
    screen["layout"] = "review"
    screen["summary"] = [
        {
            "h": "Household",
            "p": f"{snapshot.household_mode.title()} · {snapshot.assumption_state} · retire {retire_self} / {retire_wife}",
            "step_id": "setup",
        },
        {
            "h": "Assets",
            "p": f"${total_assets:,.0f} total",
            "step_id": "assets_self",
        },
        {
            "h": "Spending",
            "p": f"${float(self_p.get('base_expenses', 0)):,.0f}/yr base (you)",
            "step_id": "spending_self",
        },
        {
            "h": "Economy",
            "p": f"{float(inflation) * 100:g}% inflation · {float(returns) * 100:g}% return",
            "step_id": "market",
        },
    ]
    screen["examples"] = _example_review_rows(snapshot)
    mode = snapshot.simulation.simulation_mode
    _modes = ("deterministic", "monte_carlo", "both")
    screen["simOptions"] = list(_SIM_LABELS)
    screen["simOn"] = _modes.index(mode) if mode in _modes else 2
    screen["help"] = [
        {"h": "Summary", "p": "Quick snapshot of key inputs. Go back to any step to edit."},
        {
            "h": "Simulation mode",
            "p": "<strong>Deterministic</strong> — one best-estimate path.<br><br>"
            "<strong>Monte Carlo</strong> — range of possible outcomes.<br><br>"
            "<strong>Both</strong> — run both analyses side by side.",
        },
    ]
    screen["footer"] = {"showRun": True}
    return screen


def _special_screen(
    snapshot: PlanningSnapshot,
    step_id: str,
    step: WorkflowStep,
    *,
    index: int,
    total: int,
) -> dict[str, Any] | None:
    screen = _step_meta(step, index=index, total=total)
    if step_id == "import_documents":
        from app.core.document_ingestion.types import supported_types_payload

        history = snapshot.import_history or []
        last = history[-1] if history else None
        status = "No documents imported yet."
        if last:
            status = (
                f"Last import: {last.get('label', 'document')} "
                f"({last.get('fields_applied', 0)} fields applied)"
            )
        screen["layout"] = "import"
        screen["import"] = {
            "status": status,
            "types": supported_types_payload(),
            "template_hint": "Use validation/tax_return_template.json as a starting point.",
        }
        screen["help"] = [
            {"h": "What works today", "p": "Upload a Form 1040 PDF export or structured JSON — wages, deductions, and filing details map into your plan after review."},
            {"h": "Scanned returns", "p": "Image-only PDFs need LLM Settings (Expert → AI Assistant). Text PDFs from TurboTax or H&R Block work without AI."},
            {"h": "Skip", "p": "Optional — enter values manually on other steps."},
        ]
        screen["footer"] = {"skipProminent": True}
        return screen
    if step_id == "live_data":
        from app.core.presentation.live_data_ops import build_live_data_payload

        screen["layout"] = "connect"
        screen["live_data"] = build_live_data_payload(snapshot)
        screen["connectText"] = (
            "Link your accounts to pull current balances automatically. Read-only access — we never move money."
        )
        screen["help"] = [
            {"h": "Why connect?", "p": "Import live balances instead of typing them manually."},
            {"h": "Manage accounts", "p": "After linking, use Manage accounts to map each account to self or wife, or disconnect an institution."},
            {"h": "Expenses", "p": "Refresh expenses analyzes up to two years of card and checking transactions and suggests annual base spending."},
            {"h": "Skip", "p": "Optional — you can enter assets on earlier screens."},
        ]
        screen["footer"] = {"skipProminent": True}
        return screen
    if step_id == "llm_assist":
        from app.core.presentation.llm_panel import build_llm_panel_payload

        screen["layout"] = "ai"
        screen["title"] = "AI Estimate"
        screen["helpTitle"] = "Help — AI Estimate"
        screen["aiText"] = (
            "Let AI fill in reasonable estimates for unanswered fields. "
            "Review everything on the next screen before running your plan."
        )
        screen["llm"] = build_llm_panel_payload(snapshot)
        screen["help"] = [
            {"h": "What it does", "p": "AI can suggest values for fields you skipped."},
            {"h": "Skip", "p": "Optional. You can fill everything manually."},
        ]
        screen["footer"] = {"skipProminent": True}
        return screen
    return None


def _attach_documents_panel(
    screen: dict[str, Any],
    snapshot: PlanningSnapshot,
    step_id: str,
) -> dict[str, Any]:
    from app.core.document_ingestion.step_documents import build_documents_panel

    screen["documents_panel"] = build_documents_panel(snapshot, step_id=step_id)
    return screen


def build_guided_screen(
    snapshot: PlanningSnapshot,
    workflow: WorkflowDefinition,
    *,
    index: int,
) -> dict[str, Any]:
    """Single source of truth for guided step content (any UI)."""
    total = len(workflow.steps)
    index = max(0, min(index, total - 1))
    step = workflow.step_at(index)

    if step.step_id == "setup":
        return _attach_documents_panel(
            _setup_screen(snapshot, step, index=index, total=total),
            snapshot,
            step.step_id,
        )
    if step.step_id == "review":
        return _attach_documents_panel(
            _review_screen(snapshot, step, index=index, total=total),
            snapshot,
            step.step_id,
        )

    special = _special_screen(snapshot, step.step_id, step, index=index, total=total)
    if special is not None:
        return _attach_documents_panel(special, snapshot, step.step_id)

    screen = _step_meta(step, index=index, total=total)
    fields: list[dict[str, Any]] = []
    for field, field_person in guided_step_field_rows(snapshot, step.step_id):
        if (
            snapshot.household_mode == "joint"
            and step.step_id == "timeline"
            and field.key == "marriage_year"
        ):
            fields.append(_already_married_spec(snapshot))
        spec = field_to_spec(snapshot, field, person=field_person, index=len(fields))
        if snapshot.household_mode == "joint" and field.key == "marriage_year":
            spec["showWhen"] = {"key": "already_married", "value": "no"}
            spec["caption"] = "Use this year or a later year. If you are already married, choose Yes."
        fields.append(spec)
    screen["fields"] = fields
    screen["help"] = [{"h": h, "p": p} for h, p in help_items_for_step(step.step_id)]
    screen["wide"] = step.step_id in {"timeline", "market", "housing"}
    screen["intro"] = STEP_GUIDED_INTROS.get(step.step_id, "")
    sub_index = snapshot.wizard.sub_step_positions.get(
        step.step_id,
        snapshot.wizard.sub_step_index,
    )
    micro = micro_screen_meta(step.step_id, sub_index)
    if micro:
        screen.update(micro)
        if micro.get("intro"):
            screen["intro"] = micro["intro"]
    screen["footer"] = {}
    return _attach_documents_panel(screen, snapshot, step.step_id)


def simulation_mode_from_label(label: str) -> str | None:
    return _SIM_TO_MODE.get(label)


def simulation_label_from_mode(mode: str) -> str:
    return _MODE_TO_LABEL.get(mode, "Both")
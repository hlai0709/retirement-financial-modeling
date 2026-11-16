"""Expert mode screen payloads — UI shells render these dicts."""

from __future__ import annotations

from typing import Any

from app.core.presentation.field_editor import field_to_spec
from app.core.presentation.guided_screens import _SIM_LABELS, simulation_label_from_mode
from app.core.presentation.llm_panel import build_llm_panel_payload
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.live_data_ops import build_live_data_payload
from config.model_fields import FIELDS_BY_KEY, field_choice_values
from app.core.presentation.catalog_sections import assumption_sections
from app.core.presentation.field_search import field_spec_search_text

EXPERT_SECTION_GUIDED_STEP: dict[str, str] = {
    "setup": "setup",
    "assumptions": "timeline",
    "llm": "llm_assist",
    "import_documents": "import_documents",
    "live_data": "live_data",
    "simulate": "review",
    "settings": "setup",
}

EXPERT_NAV = (
    ("setup", "Setup"),
    ("assumptions", "Assumptions"),
    ("llm", "AI Assistant"),
    ("live_data", "Live Data"),
    ("simulate", "Simulate"),
    ("settings", "Settings"),
)


def expert_nav() -> list[dict[str, str]]:
    return [{"id": nav_id, "label": label} for nav_id, label in EXPERT_NAV]


def _subtitle(section_id: str) -> str:
    return {
        "setup": "Household and filing assumptions",
        "assumptions": "All planning fields — grouped by topic",
        "llm": "Batch-estimate empty fields with AI",
        "live_data": "Plaid, CSV import, and market refresh",
        "simulate": "Run options and Monte Carlo settings",
        "settings": "LLM, Plaid, and export tools",
    }.get(section_id, "")


def _plan_save_detail(last_saved_at: str | None) -> str:
    if last_saved_at:
        return "Loads automatically when you reopen the app"
    return "Not saved yet — save to restore on next launch"


def build_expert_screen(
    snapshot: PlanningSnapshot,
    section_id: str,
    *,
    assumptions_scope: str | None = None,
    last_saved_at: str | None = None,
) -> dict[str, Any]:
    screen: dict[str, Any] = {
        "section_id": section_id,
        "title": dict(EXPERT_NAV).get(section_id, section_id.title()),
        "subtitle": _subtitle(section_id),
        "layout": section_id,
        "guided_step_id": EXPERT_SECTION_GUIDED_STEP.get(section_id, "setup"),
    }

    if section_id == "setup":
        state_field = FIELDS_BY_KEY["state_of_residence"]
        states = list(field_choice_values(state_field) or ("CA",))
        household_on = 0 if snapshot.household_mode == "joint" else 1
        screen["fields"] = [
            {
                "key": "household_mode",
                "type": "radio",
                "label": "Household mode",
                "options": ["Together (joint)", "Separately"],
                "on": household_on,
            },
            {
                "key": "state_of_residence",
                "type": "select",
                "label": "State of residence",
                "value": snapshot.assumption_state,
                "options": states,
            },
        ]
        return screen

    if section_id == "assumptions":
        if snapshot.household_mode == "joint":
            scope_options = ["shared", "self", "wife"]
            scope = assumptions_scope or "shared"
            if scope not in scope_options:
                scope = "shared"
        else:
            scope_options = ["self"]
            scope = "self"
        screen["scope_options"] = scope_options
        screen["scope_on"] = scope_options.index(scope)
        screen["scope"] = scope
        screen["groups"] = []
        for title, guided_step_id, fields in assumption_sections(scope=scope if scope != "shared" else "shared"):
            field_specs = []
            for i, field in enumerate(fields):
                spec = field_to_spec(snapshot, field, person=None if scope == "shared" else scope, index=i)
                spec["shortLabel"] = field.label
                spec["searchText"] = field_spec_search_text(spec)
                field_specs.append(spec)
            screen["groups"].append(
                {
                    "title": title,
                    "guided_step_id": guided_step_id,
                    "fields": field_specs,
                }
            )
        return screen

    if section_id == "llm":
        screen["title"] = "AI Estimate"
        screen["subtitle"] = (
            "Let AI fill in reasonable estimates for unanswered fields. "
            "Review every suggestion before it updates your plan."
        )
        screen["aiText"] = screen["subtitle"]
        screen["llm"] = build_llm_panel_payload(snapshot)
        return screen

    if section_id == "live_data":
        screen["live_data"] = build_live_data_payload(snapshot)
        return screen

    if section_id == "simulate":
        sim = snapshot.simulation
        modes = ("deterministic", "monte_carlo", "both")
        screen["sim_mode_on"] = modes.index(sim.simulation_mode) if sim.simulation_mode in modes else 2
        screen["precision_on"] = 0 if sim.planning_precision == "standard" else 1
        screen["fields"] = [
            {"key": "num_paths", "type": "narrow", "label": "MC paths", "value": str(sim.num_paths)},
            {
                "key": "mc_stock_allocation_pct",
                "type": "narrow",
                "label": "Stock allocation %",
                "value": str(int(sim.mc_stock_allocation_pct)),
            },
            {
                "key": "risk_tolerance",
                "type": "select",
                "label": "Risk tolerance",
                "value": sim.risk_tolerance,
                "options": ["conservative", "moderate", "aggressive"],
            },
        ]
        screen["sim_options"] = list(_SIM_LABELS)
        screen["sim_label"] = simulation_label_from_mode(sim.simulation_mode)
        return screen

    if section_id == "settings":
        from settings import get_settings, load_llm_settings

        llm = load_llm_settings()
        settings = get_settings()
        screen["plan_actions"] = {
            "save_detail": _plan_save_detail(last_saved_at),
            "new_detail": "Blank template (zeros) — your last saved plan stays on disk",
            "last_saved_at": last_saved_at,
        }
        screen["items"] = [
            {"id": "llm", "label": "LLM Settings", "detail": f"{llm.provider} / {llm.model or 'default'}"},
            {"id": "export_csv", "label": "Export Assumptions CSV", "detail": "Save all inputs to CSV"},
            {"id": "validate_tax", "label": "Validate Tax Return", "detail": "Run JSON tax return validator"},
        ]
        screen["last_saved_at"] = last_saved_at
        screen["plaid_configured"] = settings.plaid_configured
        return screen

    return screen
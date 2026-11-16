"""Master list of simulation inputs shown before Run plan."""

from __future__ import annotations

from typing import Any

from app.core.controllers.llm_research_controller import field_display_label
from app.core.planning_snapshot import PERSONS, PlanningSnapshot
from app.core.ui_groups import UI_GROUPS, ui_group_for_field
from config.field_values import parse_field_value
from config.model_fields import PERSON_FIELDS, SHARED_FIELDS, ModelField


# Section order for the pre-run checklist (matches planning journey)
_SECTION_ORDER = [
    "setup",
    "timeline",
    "income_self",
    "income_wife",
    "assets_self",
    "assets_wife",
    "spending_self",
    "spending_wife",
    "healthcare_self",
    "healthcare_wife",
    "tax_benefits",
    "tax_wife",
    "market",
    "simulation",
]

_SECTION_TITLES = {g.step_id: g.label for g in UI_GROUPS}
_SECTION_TITLES["simulation"] = "Simulation options"


def _format_display(field: ModelField, raw: str) -> str:
    text = (raw or "").strip()
    if text == "":
        return "—"
    try:
        parsed = parse_field_value(field, text)
    except (TypeError, ValueError):
        return text
    if field.field_type == "money":
        try:
            return f"${float(parsed):,.0f}"
        except (TypeError, ValueError):
            return text
    if field.field_type == "rate_pct":
        try:
            return f"{float(parsed) * 100:g}%"
        except (TypeError, ValueError):
            return text
    if field.field_type == "bool":
        return "Yes" if parsed else "No"
    return str(parsed) if parsed is not None else text


def _is_blankish(field: ModelField, raw: str) -> bool:
    text = (raw or "").strip()
    if text == "":
        return True
    if field.field_type == "year" and text in {"0", "0.0"}:
        return True
    return False


def build_pre_run_review(snapshot: PlanningSnapshot) -> dict[str, Any]:
    """Return grouped field rows that will feed the simulation."""
    joint = snapshot.household_mode == "joint"
    sections: dict[str, list[dict[str, Any]]] = {sid: [] for sid in _SECTION_ORDER}

    def add_row(
        field: ModelField,
        *,
        person: str | None,
        raw: str,
        step_id: str,
    ) -> None:
        source = snapshot.get_source(person if field.scope == "person" else None, field.key)
        if source == "Manual":
            source_label = "Manual"
        else:
            source_label = source or "Manual"
        blank = _is_blankish(field, raw)
        sections.setdefault(step_id, []).append(
            {
                "field_key": field.key,
                "person": person,
                "label": field_display_label(field, person=person),
                "value": "—" if blank else _format_display(field, raw),
                "raw": raw,
                "blank": blank,
                "source": source_label,
                "step_id": step_id,
                "description": (field.description or "")[:160],
            }
        )

    for field in SHARED_FIELDS:
        step = ui_group_for_field(field)
        if step not in sections and step not in _SECTION_ORDER:
            sections[step] = []
        raw = snapshot.get_raw(field)
        add_row(field, person=None, raw=raw, step_id=step)

    # Prefer shared catalog when the same key exists on both scopes (e.g. life_expectancy)
    shared_keys = {f.key for f in SHARED_FIELDS}

    persons = list(PERSONS) if joint else ["self"]
    for person in persons:
        for field in PERSON_FIELDS:
            if field.key in shared_keys:
                # Shared value is the household default; skip person duplicate unless set
                raw_person = snapshot.get_raw(field, person=person).strip()
                if not raw_person or raw_person in {"0", "0.0"}:
                    continue
            step = ui_group_for_field(field, person=person)
            if step not in sections and step not in _SECTION_ORDER:
                sections[step] = []
            raw = snapshot.get_raw(field, person=person)
            add_row(field, person=person, raw=raw, step_id=step)

    # Simulation options (not in model_fields catalog)
    sim = snapshot.simulation
    sim_rows = [
        ("simulation_mode", "Simulation mode", sim.simulation_mode),
        ("planning_precision", "Planning precision", sim.planning_precision),
        ("num_paths", "Monte Carlo paths", str(sim.num_paths)),
        ("risk_tolerance", "Risk tolerance", sim.risk_tolerance or "—"),
        ("mc_stock_allocation_pct", "MC stock allocation", f"{sim.mc_stock_allocation_pct:g}%"),
        ("household_mode", "Household mode", snapshot.household_mode),
    ]
    for key, label, value in sim_rows:
        sections["simulation"].append(
            {
                "field_key": key,
                "person": None,
                "label": label,
                "value": value or "—",
                "raw": str(value),
                "blank": not value and value != 0,
                "source": "Simulation",
                "step_id": "setup" if key == "household_mode" else "review",
                "description": "",
            }
        )

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sid in _SECTION_ORDER:
        rows = sections.get(sid) or []
        if not rows:
            continue
        # Skip spouse sections when not joint
        if not joint and sid.endswith("_wife"):
            continue
        ordered.append(
            {
                "id": sid,
                "title": _SECTION_TITLES.get(sid, sid.replace("_", " ").title()),
                "step_id": sid if sid != "simulation" else "review",
                "rows": rows,
                "filled": sum(1 for r in rows if not r.get("blank")),
                "total": len(rows),
            }
        )
        seen.add(sid)
    for sid, rows in sections.items():
        if sid in seen or not rows:
            continue
        if not joint and sid.endswith("_wife"):
            continue
        ordered.append(
            {
                "id": sid,
                "title": _SECTION_TITLES.get(sid, sid.replace("_", " ").title()),
                "step_id": sid,
                "rows": rows,
                "filled": sum(1 for r in rows if not r.get("blank")),
                "total": len(rows),
            }
        )

    total_rows = sum(s["total"] for s in ordered)
    filled_rows = sum(s["filled"] for s in ordered)
    return {
        "sections": ordered,
        "household_mode": snapshot.household_mode,
        "joint": joint,
        "row_count": total_rows,
        "filled_count": filled_rows,
        "summary": f"{filled_rows} of {total_rows} inputs have values",
    }

"""Core field gaps for AI Mode — what documents didn't fill."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.controllers.llm_research_controller import field_display_label, is_field_empty
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.field_editor import resolve_field
from app.core.presentation.plan_completeness import run_plan_readiness
from app.core.workflow_definition import WorkflowDefinition
from settings import PROJECT_ROOT

_SOURCES_PATH = PROJECT_ROOT / "config" / "ai_mode" / "field_document_sources.json"

# Core completeness-oriented gaps (joint doubles person fields in loop below).
# Social Security amount/PIA/AIME are handled separately by mode in _ss_core_checks.
_CORE_CHECKS: list[tuple[str, str | None, str]] = [
    ("state_of_residence", None, "setup"),
    ("current_age", "self", "timeline"),
    ("birth_year", "self", "timeline"),
    ("retirement_age", "self", "timeline"),
    ("life_expectancy", None, "timeline"),
    ("marriage_year", None, "timeline"),
    ("initial_assets", "self", "assets_self"),
    ("base_expenses", "self", "spending_self"),
    ("annual_earned_income", "self", "income_self"),
    ("ss_start_age", "self", "tax_benefits"),
    ("pre_retire_tax_rate", None, "tax_benefits"),
    ("post_retire_tax_rate", None, "tax_benefits"),
    ("health_insurance_pre_medicare", "self", "healthcare_self"),
    ("inflation_rate", None, "market"),
    ("investment_return_mean", None, "market"),
]

_SS_AMOUNT_FIELDS = frozenset({"ss_amount", "ss_pia_monthly", "ss_aime", "ss_career_avg_earnings"})


@lru_cache(maxsize=1)
def _load_sources() -> dict[str, Any]:
    if not _SOURCES_PATH.is_file():
        return {}
    return json.loads(_SOURCES_PATH.read_text(encoding="utf-8"))


def _ss_mode(snapshot: PlanningSnapshot, person: str) -> str:
    raw = str(snapshot.persons.get(person, {}).get("ss_benefit_mode") or "manual").strip().lower()
    if raw in {"manual", "pia", "aime", "earnings"}:
        return raw
    return "manual"


def _ss_income_satisfied(snapshot: PlanningSnapshot, person: str) -> bool:
    """True when the active SS benefit mode has enough inputs for the engine."""
    mode = _ss_mode(snapshot, person)
    if mode == "manual":
        field = resolve_field("ss_amount", person=person)
        return field is not None and not is_field_empty(snapshot, field, person=person)
    if mode == "pia":
        field = resolve_field("ss_pia_monthly", person=person)
        return field is not None and not is_field_empty(snapshot, field, person=person)
    if mode == "aime":
        field = resolve_field("ss_aime", person=person)
        return field is not None and not is_field_empty(snapshot, field, person=person)
    # earnings: AIME and/or career avg / earned income used to derive AIME
    for key in ("ss_aime", "ss_career_avg_earnings", "annual_earned_income"):
        field = resolve_field(key, person=person)
        if field is not None and not is_field_empty(snapshot, field, person=person):
            return True
    return False


def _ss_start_age_satisfied(snapshot: PlanningSnapshot, person: str) -> bool:
    """Claim age 62–70 counts once SS income inputs exist (catalog default is 67)."""
    field = resolve_field("ss_start_age", person=person)
    if field is None:
        return True
    if not is_field_empty(snapshot, field, person=person):
        return True
    raw = str(snapshot.get_raw(field, person=person) or "").strip()
    try:
        age = int(float(raw))
    except (TypeError, ValueError):
        return False
    if age < 62 or age > 70:
        return False
    # Default 67 is fine when benefits will be calculated from PIA/AIME/earnings/manual amount
    return _ss_income_satisfied(snapshot, person)


def _ss_required_field_key(snapshot: PlanningSnapshot, person: str) -> str | None:
    """Which SS amount field is still missing for this mode (if any)."""
    if _ss_income_satisfied(snapshot, person):
        return None
    mode = _ss_mode(snapshot, person)
    if mode == "manual":
        return "ss_amount"
    if mode == "pia":
        return "ss_pia_monthly"
    if mode == "aime":
        return "ss_aime"
    return "ss_aime"  # earnings path — prompt for AIME / statement


def _health_pre_medicare_satisfied(snapshot: PlanningSnapshot, person: str) -> bool:
    """Employer-premium path or explicit pre-Medicare annual premium counts as filled."""
    field = resolve_field("health_insurance_pre_medicare", person=person)
    if field is not None and not is_field_empty(snapshot, field, person=person):
        return True
    mode = str(snapshot.persons.get(person, {}).get("employer_health_mode") or "none").strip().lower()
    if mode not in {"employer", "cobra"}:
        return False
    monthly_field = resolve_field("employer_health_monthly", person=person)
    if monthly_field is None:
        return False
    return not is_field_empty(snapshot, monthly_field, person=person)


def _append_gap(
    gaps: list[dict[str, Any]],
    *,
    snapshot: PlanningSnapshot,
    workflow: WorkflowDefinition,
    field_key: str,
    person: str | None,
    step_id: str,
    doc_by_field: dict[str, list[str]],
    estimate_keys: set[str],
    note: str = "",
) -> None:
    field = resolve_field(field_key, person=person)
    if field is None:
        return
    field_person = person if field.scope == "person" else None
    jump_index = workflow.index_of(step_id) if step_id else -1
    docs = list(doc_by_field.get(field_key, []))
    if field_key in _SS_AMOUNT_FIELDS or field_key == "ss_start_age":
        if "ssa_1099" not in docs:
            docs = ["ssa_1099", *docs]
    entry = {
        "field_key": field_key,
        "person": field_person,
        "label": field_display_label(field, person=field_person),
        "guided_step_id": step_id,
        "guided_step_index": jump_index if jump_index >= 0 else None,
        "documents": docs,
        "user_specific": field_key not in estimate_keys,
    }
    if note:
        entry["note"] = note
    gaps.append(entry)


def build_ai_mode_gaps(
    snapshot: PlanningSnapshot,
    workflow: WorkflowDefinition,
) -> dict[str, Any]:
    joint = snapshot.household_mode == "joint"
    readiness = run_plan_readiness(snapshot, workflow)
    sources = _load_sources()
    doc_by_field: dict[str, list[str]] = {}
    for row in sources.get("user_fact_fields") or []:
        doc_by_field[str(row.get("field_key"))] = list(row.get("documents") or [])
    estimate_keys = set(sources.get("estimate_or_manual_fields") or [])

    gaps: list[dict[str, Any]] = []
    for field_key, person, step_id in _CORE_CHECKS:
        if field_key == "marriage_year" and not joint:
            continue
        # SS start age handled with mode-aware helper below
        if field_key == "ss_start_age":
            continue
        persons = [person]
        if joint and person == "self" and field_key in {
            "current_age",
            "birth_year",
            "retirement_age",
            "initial_assets",
            "base_expenses",
            "annual_earned_income",
            "health_insurance_pre_medicare",
        }:
            persons = ["self", "wife"]
            step_for = {
                "self": step_id,
                "wife": {
                    "assets_self": "assets_wife",
                    "spending_self": "spending_wife",
                    "income_self": "income_wife",
                    "healthcare_self": "healthcare_wife",
                    "tax_benefits": "tax_wife",
                    "timeline": "timeline",
                }.get(step_id, step_id),
            }
        else:
            step_for = {person: step_id}

        for pers in persons:
            field = resolve_field(field_key, person=pers)
            if field is None:
                continue
            field_person = pers if field.scope == "person" else None
            if field_key == "health_insurance_pre_medicare" and field_person in {"self", "wife"}:
                if _health_pre_medicare_satisfied(snapshot, field_person):
                    continue
            elif not is_field_empty(snapshot, field, person=field_person):
                continue
            jump = step_for.get(pers, step_id) if isinstance(step_for, dict) else step_id
            note = ""
            if field_key == "health_insurance_pre_medicare":
                note = (
                    "Upload a recent pay stub (Medical/Dental/Vision) or set Employer Health Monthly "
                    "in Guided — or enter annual pre-Medicare premium if you buy coverage yourself"
                )
            _append_gap(
                gaps,
                snapshot=snapshot,
                workflow=workflow,
                field_key=field_key,
                person=field_person,
                step_id=jump,
                doc_by_field=doc_by_field,
                estimate_keys=estimate_keys,
                note=note,
            )

    # Mode-aware Social Security (self + spouse when joint)
    ss_persons = ["self", "wife"] if joint else ["self"]
    for pers in ss_persons:
        step = "tax_wife" if pers == "wife" else "tax_benefits"
        mode = _ss_mode(snapshot, pers)
        if not _ss_start_age_satisfied(snapshot, pers):
            _append_gap(
                gaps,
                snapshot=snapshot,
                workflow=workflow,
                field_key="ss_start_age",
                person=pers,
                step_id=step,
                doc_by_field=doc_by_field,
                estimate_keys=estimate_keys,
                note="Claiming age (62–70)",
            )
        missing = _ss_required_field_key(snapshot, pers)
        if missing:
            mode_note = {
                "manual": "SS mode is Manual — need annual SS Amount (or switch mode / upload statement)",
                "pia": "SS mode is PIA — need monthly PIA from your Social Security statement",
                "aime": "SS mode is AIME — need Average Indexed Monthly Earnings",
                "earnings": "SS mode is Earnings — need AIME, career-average earnings, or a Social Security statement",
            }.get(mode, "")
            _append_gap(
                gaps,
                snapshot=snapshot,
                workflow=workflow,
                field_key=missing,
                person=pers,
                step_id=step,
                doc_by_field=doc_by_field,
                estimate_keys=estimate_keys,
                note=mode_note,
            )

    user_gaps = [g for g in gaps if g["field_key"] not in estimate_keys]
    estimate_gaps = [g for g in gaps if g["field_key"] in estimate_keys]

    return {
        "gaps": gaps,
        "user_gaps": user_gaps,
        "estimate_gaps": estimate_gaps,
        "gap_count": len(gaps),
        "user_gap_count": len(user_gaps),
        "estimate_gap_count": len(estimate_gaps),
        "completeness": readiness,
        "message": (
            f"{len(user_gaps)} personal field(s) still empty"
            + (f"; {len(estimate_gaps)} assumption(s) can use Estimates" if estimate_gaps else "")
            if gaps
            else "Core fields look filled — review and run when ready."
        ),
    }

"""Lazy Mode section wizard — guided-style document checklist by topic."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.lazy_mode.orchestrator import build_import_summary, build_lazy_checklist
from app.core.planning_snapshot import PlanningSnapshot
from app.core.presentation.plan_completeness import run_plan_readiness
from app.core.workflow_definition import WorkflowDefinition
from settings import PROJECT_ROOT

_SECTIONS_PATH = PROJECT_ROOT / "config" / "lazy_mode" / "lazy_sections.json"


@lru_cache(maxsize=1)
def load_lazy_sections() -> dict[str, Any]:
    return json.loads(_SECTIONS_PATH.read_text(encoding="utf-8"))


def lazy_sections_for_snapshot(snapshot: PlanningSnapshot) -> list[dict[str, Any]]:
    return list(load_lazy_sections().get("sections", []))


def lazy_section_count(snapshot: PlanningSnapshot) -> int:
    return len(lazy_sections_for_snapshot(snapshot))


def lazy_section_at(snapshot: PlanningSnapshot, index: int) -> dict[str, Any] | None:
    sections = lazy_sections_for_snapshot(snapshot)
    if not sections or index < 0 or index >= len(sections):
        return None
    return sections[index]


def _section_documents(section: dict[str, Any], checklist: dict[str, Any]) -> list[dict[str, Any]]:
    doc_ids = set(section.get("document_ids") or [])
    if not doc_ids:
        return []
    by_id = {doc["id"]: doc for doc in checklist.get("documents", [])}
    return [by_id[doc_id] for doc_id in section.get("document_ids", []) if doc_id in by_id]


def _section_complete(section: dict[str, Any], docs: list[dict[str, Any]]) -> bool:
    if section.get("layout") == "review" or section.get("id") == "welcome":
        return False
    if not docs:
        return True
    return all(doc.get("status") in {"parsed", "provided", "n/a"} for doc in docs)


def build_lazy_journey_rail(
    snapshot: PlanningSnapshot,
    *,
    current_index: int,
) -> list[dict[str, Any]]:
    checklist = build_lazy_checklist(snapshot)
    sections = lazy_sections_for_snapshot(snapshot)
    wizard = snapshot.wizard
    completed = set(wizard.lazy_completed_sections)
    skipped = set(wizard.lazy_skipped_sections)

    blocks: dict[str, list[dict[str, Any]]] = {}
    for index, section in enumerate(sections):
        journey = str(section.get("journey", "Documents"))
        docs = _section_documents(section, checklist)
        done = section["id"] in completed or (
            _section_complete(section, docs) and section.get("id") not in skipped
        )
        is_skipped = section["id"] in skipped
        blocks.setdefault(journey, []).append(
            {
                "index": index,
                "section_id": section["id"],
                "label": _rail_label(section),
                "current": index == current_index,
                "completed": done and not is_skipped,
                "skipped": is_skipped,
            }
        )

    return [{"journey": journey, "steps": steps} for journey, steps in blocks.items()]


def _rail_label(section: dict[str, Any]) -> str:
    labels = {
        "welcome": "Start",
        "connect": "Connect",
        "tax_income": "Taxes",
        "income": "Income",  # legacy id if old saves reference it
        "taxes": "Taxes",
        "spending": "Spending",
        "retirement_accounts": "Accounts",
        "social_security": "SS",
        "economy": "Economy",
        "wrap_up": "Gaps",
        "ai_estimates": "Estimates",
        "review": "Review",
    }
    return labels.get(str(section.get("id", "")), str(section.get("title", "Step"))[:12])


def build_lazy_screen_at(
    snapshot: PlanningSnapshot,
    workflow: WorkflowDefinition,
    index: int,
) -> dict[str, Any]:
    from app.core.ai_mode.gaps import build_ai_mode_gaps
    from app.core.presentation.llm_panel import build_llm_panel_payload

    sections = lazy_sections_for_snapshot(snapshot)
    total = len(sections)
    index = max(0, min(index, total - 1))
    section = sections[index]
    checklist = build_lazy_checklist(snapshot)
    readiness = run_plan_readiness(snapshot, workflow)
    docs = _section_documents(section, checklist)
    gaps = build_ai_mode_gaps(snapshot, workflow)

    screen: dict[str, Any] = {
        "layout": section.get("layout", "lazy_section"),
        "section_id": section["id"],
        "num": index + 1,
        "totalSteps": total,
        "step_index": index,
        "title": section.get("title", "Documents"),
        "journey": section.get("journey", "Documents"),
        "intro": section.get("intro", ""),
        "where_to_get": list(section.get("where_to_get") or []),
        "documents": docs,
        "optional": bool(section.get("optional")),
        "skip_label": section.get("skip_label"),
        "checklist": checklist,
        "completeness": readiness,
        "gaps": gaps,
        "footer": {
            "backDisabled": index <= 0,
            "skipProminent": bool(section.get("optional")),
            "showRun": section.get("id") == "review",
        },
        "help": _help_for_section(section),
        "helpTitle": f"Help — {section.get('title', 'AI Mode')}",
        "import_summary": build_import_summary(snapshot),
    }

    if section.get("id") == "review":
        screen["summary"] = _review_summary(checklist, readiness, snapshot, gaps)
    if section.get("id") in {"wrap_up", "ai_estimates"} or section.get("layout") == "ai_estimates":
        screen["layout"] = section.get("layout") or (
            "ai_estimates" if section.get("id") == "ai_estimates" else "gaps"
        )
    if section.get("id") == "ai_estimates" or section.get("layout") == "ai_estimates":
        screen["llm"] = build_llm_panel_payload(snapshot)
        screen["layout"] = "ai_estimates"

    return screen


def _review_summary(
    checklist: dict[str, Any],
    readiness: dict[str, Any],
    snapshot: PlanningSnapshot,
    gaps: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    docs = checklist.get("documents", [])
    provided = [doc for doc in docs if doc.get("status") in {"parsed", "provided"}]
    missing = [doc for doc in docs if doc.get("status") == "missing" and doc.get("required_for_core")]
    imports = build_import_summary(snapshot)
    rows = [
        {
            "h": "Documents provided",
            "p": f"{len(provided)} of {len(docs)} sources on file",
            "section_id": "review",
        },
        {
            "h": "Imported fields",
            "p": (
                f"{imports.get('total_fields', 0)} field(s) from {imports.get('entry_count', 0)} import(s)"
                if imports.get("has_imports")
                else "Nothing imported yet — upload your 1040 on Income & taxes"
            ),
            "section_id": "tax_income",
        },
        {
            "h": "Plan completeness",
            "p": f"{readiness.get('percent', 0)}% — {readiness.get('label', '')}",
            "section_id": "review",
        },
    ]
    if gaps and gaps.get("gap_count"):
        rows.append(
            {
                "h": "Still empty (core)",
                "p": gaps.get("message") or f"{gaps['gap_count']} field(s)",
                "section_id": "wrap_up",
            }
        )
    if missing:
        labels = ", ".join(doc.get("label", doc.get("id", "")) for doc in missing[:4])
        suffix = "…" if len(missing) > 4 else ""
        rows.append(
            {
                "h": "Documents still missing",
                "p": f"{labels}{suffix}",
                "section_id": "tax_income",
            }
        )
    return rows


def _help_for_section(section: dict[str, Any]) -> list[dict[str, str]]:
    section_id = str(section.get("id", ""))
    common = [
        {
            "h": "Why section by section?",
            "p": "Like a real accountant, we ask for one topic at a time so you're never overwhelmed.",
        },
    ]
    specific: dict[str, list[dict[str, str]]] = {
        "tax_income": [
            {
                "h": "Why only the 1040?",
                "p": "Line 1z is total wages. The return also has interest, dividends, SS, pension, AGI, and itemized deductions.",
            },
            {
                "h": "All pages",
                "p": "If you itemize, include Schedule A. Rental income needs Schedule E. Digital PDFs parse best.",
            },
            {
                "h": "Check the import summary",
                "p": "After you apply fields, a summary lists every value that was written so you can catch mistakes before running the plan.",
            },
        ],
        "connect": [
            {
                "h": "Is Plaid safe?",
                "p": "Plaid is read-only. We cannot move money or see your bank password.",
            },
        ],
    }
    return specific.get(section_id, []) + common
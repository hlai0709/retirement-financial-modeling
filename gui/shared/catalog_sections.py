"""Catalog-driven field grouping for expert and guided modes."""

from __future__ import annotations

from collections import defaultdict

from app.core.ui_groups import UI_GROUPS, fields_for_ui_group, sort_fields_for_guided, ui_group_for_field
from config.model_fields import PERSON_FIELDS, SHARED_FIELDS, ModelField


def assumption_sections(*, scope: str = "shared") -> list[tuple[str, str, tuple[ModelField, ...]]]:
    grouped: dict[str, list[ModelField]] = defaultdict(list)
    group_step_ids: dict[str, str] = {}
    labels = {g.step_id: g.label for g in UI_GROUPS}
    skip_groups = {"setup", "live_data", "llm_assist", "import_documents", "review", "scenarios"}

    if scope == "shared":
        for field in SHARED_FIELDS:
            if field.key in {"state_of_residence", "planning_precision"}:
                continue
            gid = ui_group_for_field(field)
            if gid in skip_groups:
                continue
            title = labels.get(gid, gid)
            grouped[title].append(field)
            group_step_ids[title] = gid
    else:
        for field in PERSON_FIELDS:
            gid = ui_group_for_field(field, person=scope)
            if scope == "wife":
                gid = gid.replace("_self", "_wife")
            if gid in skip_groups or (scope == "self" and gid.endswith("_wife")):
                continue
            title = labels.get(gid, gid)
            grouped[title].append(field)
            group_step_ids[title] = gid

    return [
        (title, group_step_ids[title], sort_fields_for_guided(fields))
        for title, fields in sorted(grouped.items())
        if fields
    ]


def fields_for_wizard_step(step_id: str) -> tuple[ModelField, ...]:
    if step_id in {"setup", "live_data", "llm_assist", "import_documents", "review"}:
        return ()
    return fields_for_ui_group(step_id)
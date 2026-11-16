"""Compact data-source summary for status bars — UI-agnostic."""

from __future__ import annotations

from typing import Any

from app.core.planning_snapshot import PlanningSnapshot


def build_data_sources_summary(snapshot: PlanningSnapshot) -> dict[str, Any]:
    sources = snapshot.build_data_sources()
    labels = sorted({label for label in sources.values() if label and label != "Manual"})
    if not labels:
        return {"summary": "Manual entry", "count": 0, "labels": []}
    if len(labels) == 1:
        summary = labels[0]
    elif len(labels) == 2:
        summary = f"{labels[0]} · {labels[1]}"
    else:
        summary = f"{labels[0]} · {labels[1]} +{len(labels) - 2}"
    return {"summary": summary, "count": len(sources), "labels": labels}
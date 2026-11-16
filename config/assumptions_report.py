"""Build exportable assumptions / parameter reports."""

from __future__ import annotations

from datetime import date

import pandas as pd

from config.field_values import format_field_value
from config.model_fields import PERSON_FIELDS, SHARED_FIELDS


def build_assumptions_dataframe(
    params: dict,
    *,
    sources: dict[str, str] | None = None,
    risk_tolerance: str | None = None,
) -> pd.DataFrame:
    """Flatten simulation parameters into a CSV-friendly table."""
    sources = sources or {}
    rows: list[dict] = []

    rows.append(
        {
            "scope": "meta",
            "person": "",
            "field_key": "mode",
            "label": "Projection Mode",
            "value": params.get("mode", ""),
            "source": "Manual",
            "data_source": "manual",
        }
    )
    if risk_tolerance:
        rows.append(
            {
                "scope": "meta",
                "person": "",
                "field_key": "risk_tolerance",
                "label": "Risk Tolerance",
                "value": risk_tolerance,
                "source": "Manual",
                "data_source": "manual",
            }
        )
    rows.append(
        {
            "scope": "meta",
            "person": "",
            "field_key": "exported_at",
            "label": "Exported At",
            "value": date.today().isoformat(),
            "source": "System",
            "data_source": "manual",
        }
    )

    for field in SHARED_FIELDS:
        value = params.get(field.key, "")
        display = format_field_value(field, value) if value != "" else ""
        rows.append(
            {
                "scope": "shared",
                "person": "",
                "field_key": field.key,
                "label": field.label,
                "value": display,
                "source": sources.get(field.label, "Manual"),
                "data_source": field.data_source,
            }
        )

    for person, person_params in params.get("persons", {}).items():
        for field in PERSON_FIELDS:
            value = person_params.get(field.key, "")
            display = format_field_value(field, value) if value != "" else ""
            source_key = f"{person.title()} {field.label}"
            rows.append(
                {
                    "scope": "person",
                    "person": person,
                    "field_key": field.key,
                    "label": field.label,
                    "value": display,
                    "source": sources.get(source_key, "Manual"),
                    "data_source": field.data_source,
                }
            )

    return pd.DataFrame(rows)
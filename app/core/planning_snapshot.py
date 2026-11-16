"""Platform-agnostic planning state — shared by expert, guided, and native apps."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from config.field_values import format_field_value, parse_field_value
from config.model_fields import PERSON_FIELDS, SHARED_FIELDS, ModelField, default_display_value
from app.core.simulation_options import SimulationOptions

SNAPSHOT_VERSION = 1
PERSONS = ("self", "wife")


@dataclass
class WizardProgress:
    last_step_id: str = "setup"
    completed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    touched_fields: list[str] = field(default_factory=list)
    sub_step_index: int = 0
    sub_step_positions: dict[str, int] = field(default_factory=dict)
    lazy_step_index: int = 0
    lazy_completed_sections: list[str] = field(default_factory=list)
    lazy_skipped_sections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_step_id": self.last_step_id,
            "completed_steps": list(self.completed_steps),
            "skipped_steps": list(self.skipped_steps),
            "touched_fields": list(self.touched_fields),
            "sub_step_index": self.sub_step_index,
            "sub_step_positions": dict(self.sub_step_positions),
            "lazy_step_index": self.lazy_step_index,
            "lazy_completed_sections": list(self.lazy_completed_sections),
            "lazy_skipped_sections": list(self.lazy_skipped_sections),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WizardProgress:
        if not data:
            return cls()
        positions = data.get("sub_step_positions") or {}
        return cls(
            last_step_id=data.get("last_step_id", "setup"),
            completed_steps=list(data.get("completed_steps", [])),
            skipped_steps=list(data.get("skipped_steps", [])),
            touched_fields=list(data.get("touched_fields", [])),
            sub_step_index=int(data.get("sub_step_index", 0)),
            sub_step_positions={str(k): int(v) for k, v in positions.items()},
            lazy_step_index=int(data.get("lazy_step_index", 0)),
            lazy_completed_sections=list(data.get("lazy_completed_sections", [])),
            lazy_skipped_sections=list(data.get("lazy_skipped_sections", [])),
        )


@dataclass
class PlanningSnapshot:
    household_mode: str = "joint"
    presentation_mode: str = "guided"
    shared: dict[str, str] = field(default_factory=dict)
    persons: dict[str, dict[str, str]] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    simulation: SimulationOptions = field(default_factory=SimulationOptions)
    wizard: WizardProgress = field(default_factory=WizardProgress)
    assumption_state: str = "CA"
    llm_extra_prompt: str = ""
    import_history: list[dict[str, Any]] = field(default_factory=list)
    # Multi-file AI Mode uploads (queued → extracted → applied). See document_library.py.
    document_library: list[dict[str, Any]] = field(default_factory=list)
    # Per-person annual spending category breakdown from the spending builder UI.
    spending_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.shared:
            self.shared = {f.key: _default_text(f) for f in SHARED_FIELDS}
        for person in PERSONS:
            self.persons.setdefault(
                person,
                {f.key: _default_text(f, person=person) for f in PERSON_FIELDS},
            )

    def get_raw(self, field: ModelField, *, person: str | None = None) -> str:
        if field.scope == "shared":
            return self.shared.get(field.key, "")
        if person is None:
            raise ValueError(f"person required for field {field.key}")
        return self.persons.get(person, {}).get(field.key, "")

    def set_raw(self, field: ModelField, value: str, *, person: str | None = None) -> None:
        if field.scope == "shared":
            self.shared[field.key] = value
            return
        if person is None:
            raise ValueError(f"person required for field {field.key}")
        self.persons.setdefault(person, {})[field.key] = value

    def set_parsed(self, field: ModelField, value: Any, *, person: str | None = None) -> None:
        self.set_raw(field, format_field_value(field, value), person=person)

    def sanitize_invalid_year_fields(self) -> list[str]:
        """Clear year fields that look like dollar balances (e.g. 250000).

        Local AI previously mapped outstanding principal into mortgage_payoff_year.
        Leaves blank/0 optional years alone.
        """
        from config.model_fields import FIELDS_BY_KEY

        cleared: list[str] = []
        year_fields = [f for f in FIELDS_BY_KEY.values() if f.field_type == "year"]
        for field in year_fields:
            if field.scope == "shared":
                scopes: list[tuple[str | None, dict[str, Any]]] = [(None, self.shared)]
            else:
                scopes = [(p, self.persons.setdefault(p, {})) for p in PERSONS]
            for person, bucket in scopes:
                raw = str(bucket.get(field.key) or "").strip()
                if not raw or raw in {"0", "0.0"}:
                    continue
                try:
                    numeric = int(float(raw.replace(",", "").replace("$", "")))
                except (TypeError, ValueError):
                    continue
                # Only strip values that cannot be calendar years (balances, loan ids)
                if 1900 <= numeric <= 2100:
                    if field.key == "mortgage_payoff_year" and not (2020 <= numeric <= 2080):
                        pass  # clear below
                    else:
                        continue
                bucket[field.key] = ""
                self.set_source(person, field.key, "Manual")
                cleared.append(self.source_key(person, field.key))
        return cleared

    def source_key(self, person: str | None, field_key: str) -> str:
        return f"{person or 'shared'}:{field_key}"

    def get_source(self, person: str | None, field_key: str) -> str:
        return self.field_sources.get(self.source_key(person, field_key), "Manual")

    def set_source(self, person: str | None, field_key: str, label: str) -> None:
        if not label or label == "Manual":
            self.field_sources.pop(self.source_key(person, field_key), None)
        else:
            self.field_sources[self.source_key(person, field_key)] = label

    def build_data_sources(self) -> dict[str, str]:
        from config.model_fields import FIELDS_BY_KEY

        sources: dict[str, str] = {}
        for key, label in self.field_sources.items():
            if not label or label == "Manual":
                continue
            scope, field_key = key.split(":", 1)
            field = FIELDS_BY_KEY.get(field_key)
            name = field.label if field else field_key.replace("_", " ").title()
            if scope in PERSONS:
                sources[f"{scope.title()} {name}"] = label
            else:
                sources[name] = label
        return sources

    def to_simulation_params(self, *, current_year: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "mode": self.household_mode,
            "persons": {},
            "current_year": current_year or date.today().year,
            "data_sources": self.build_data_sources(),
        }
        for field in SHARED_FIELDS:
            params[field.key] = parse_field_value(field, self.shared.get(field.key, ""))
        params["planning_precision"] = self.simulation.planning_precision
        params["mc_stock_allocation_pct"] = self.simulation.mc_stock_allocation_pct
        for person in PERSONS:
            params["persons"][person] = {
                field.key: parse_field_value(field, self.persons[person].get(field.key, ""))
                for field in PERSON_FIELDS
            }
        return params

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_version": SNAPSHOT_VERSION,
            "household_mode": self.household_mode,
            "presentation_mode": self.presentation_mode,
            "shared": dict(self.shared),
            "persons": {person: dict(values) for person, values in self.persons.items()},
            "field_sources": dict(self.field_sources),
            "simulation_options": self.simulation.to_dict(),
            "wizard_progress": self.wizard.to_dict(),
            "assumption_state": self.assumption_state,
            "llm_extra_prompt": self.llm_extra_prompt,
            "import_history": list(self.import_history),
            "document_library": list(self.document_library),
            "spending_breakdown": {
                person: dict(values)
                for person, values in (self.spending_breakdown or {}).items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanningSnapshot:
        snapshot = cls(
            household_mode=data.get("household_mode", "joint"),
            presentation_mode=data.get("presentation_mode", "guided"),
            shared=dict(data.get("shared", {})),
            persons={person: dict(values) for person, values in data.get("persons", {}).items()},
            field_sources=dict(data.get("field_sources", {})),
            simulation=SimulationOptions.from_dict(data.get("simulation_options", {})),
            wizard=WizardProgress.from_dict(data.get("wizard_progress")),
            assumption_state=data.get("assumption_state", "CA"),
            llm_extra_prompt=data.get("llm_extra_prompt", ""),
            import_history=list(data.get("import_history", [])),
            document_library=list(data.get("document_library", [])),
            spending_breakdown={
                str(person): dict(values)
                for person, values in (data.get("spending_breakdown") or {}).items()
                if isinstance(values, dict)
            },
        )
        snapshot.__post_init__()
        return snapshot

    def save_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path) -> PlanningSnapshot:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _default_text(field: ModelField, *, person: str | None = None) -> str:
    raw = default_display_value(field, person=person)
    return str(raw) if raw not in ("", None) else ""


_BLANK_NUMERIC_TYPES = frozenset({"money", "rate_pct", "age", "year", "int"})


def _blank_field_text(field: ModelField, *, person: str | None = None) -> str:
    if field.field_type == "bool":
        return "False"
    if field.field_type in _BLANK_NUMERIC_TYPES:
        return "0"
    if field.field_type == "text":
        return ""
    return _default_text(field, person=person)


def blank_planning_snapshot(*, presentation_mode: str = "guided") -> PlanningSnapshot:
    """Fresh working plan — numeric fields at 0, choices at catalog defaults."""
    snapshot = PlanningSnapshot(presentation_mode=presentation_mode)
    snapshot.shared = {f.key: _blank_field_text(f) for f in SHARED_FIELDS}
    snapshot.persons = {
        person: {f.key: _blank_field_text(f, person=person) for f in PERSON_FIELDS}
        for person in PERSONS
    }
    snapshot.field_sources = {}
    snapshot.import_history = []
    snapshot.document_library = []
    snapshot.spending_breakdown = {}
    snapshot.llm_extra_prompt = ""
    snapshot.wizard = WizardProgress()
    snapshot.simulation = SimulationOptions(
        simulation_mode="deterministic",
        planning_precision="standard",
        num_paths=1000,
        seed=None,
        mc_stock_allocation_pct=0.0,
        risk_tolerance="moderate",
    )
    return snapshot
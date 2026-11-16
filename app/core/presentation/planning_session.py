"""Central planning session — all UIs should talk to this, not duplicate logic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.action_result import ActionResult
from app.core.planning_snapshot import PlanningSnapshot, blank_planning_snapshot
from app.core.presentation.field_editor import FieldUpdate, apply_updates
from app.core.presentation.guided_screens import (
    build_guided_screen,
    simulation_label_from_mode,
    simulation_mode_from_label,
)
from app.core.presentation.expert_screens import build_expert_screen, expert_nav
from app.core.presentation.live_data_ops import (
    apply_balance_review,
    apply_market_snapshot,
    apply_owner_contributions,
    apply_owner_expenses,
    apply_owner_totals,
    build_live_data_payload,
    build_plaid_connections_payload,
    disconnect_plaid_institution,
    live_status_text,
    market_status_text,
    owner_totals_from_line_items,
    serialize_balance_review,
    serialize_contribution_review,
    serialize_csv_rows,
    serialize_expense_review,
    totals_from_import_rows,
)
from app.core.document_ingestion.ingestion_controller import apply_document_import, ingest_document
from app.core.presentation.llm_panel import apply_llm_suggestions, build_llm_panel_payload, run_llm_batch
from app.core.presentation.data_sources_summary import build_data_sources_summary
from app.core.presentation.field_derived import apply_derived_fields
from app.core.presentation.field_validation import validate_guided_step, validate_updates
from app.core.presentation.plan_completeness import (
    build_journey_rail,
    compute_completeness,
    mark_field_touched,
    mark_updates_touched,
    resolve_completeness_field,
    run_plan_readiness,
    seed_touched_fields_from_snapshot,
)
from app.core.lazy_mode.sections import build_lazy_journey_rail, lazy_section_at, lazy_section_count
from app.core.presentation.lazy_screens import build_lazy_screen
from app.core.presentation.simulation_results import serialize_simulation_result
from app.core.presentation.simulation_service import SimulationService
from app.core.workflow_definition import WorkflowDefinition
from config.policy.tables import active_policy_summary
from app.core.preferences import load_presentation_mode, save_presentation_mode
from app.core.session_store import (
    load_last_results,
    load_planning_snapshot,
    load_session_ui,
    save_last_results,
    save_planning_snapshot,
    save_session_draft,
    save_session_ui,
)
from providers.csv_provider import parse_balance_csv
from services.data_service import DataService
from settings import load_llm_settings, save_llm_settings


def _plaid_connections_state() -> dict[str, Any]:
    try:
        return build_plaid_connections_payload()
    except Exception:
        return {
            "plaid_ready": False,
            "count": 0,
            "institutions": [],
            "label": "Could not load Plaid connections",
        }


class PlanningSession:
    """
    UI-agnostic facade over PlanningSnapshot + guided workflow.

    Web (pywebview), Tk, or CLI shells are thin adapters that call these methods.
    """

    def __init__(self, snapshot: PlanningSnapshot | None = None) -> None:
        self.snapshot = snapshot or PlanningSnapshot(presentation_mode=load_presentation_mode())
        if self.snapshot.presentation_mode == "lazy":
            self.snapshot.presentation_mode = "ai"
        self._workflow = self._build_workflow()
        self._step_index = self._workflow.index_of(self.snapshot.wizard.last_step_id)
        self._simulation = SimulationService()
        self._expert_section = "assumptions"
        self._expert_assumptions_scope = "shared" if self.snapshot.household_mode == "joint" else "self"
        self._last_results: dict[str, Any] | None = None
        self._pending_expense_as_of: str = ""
        self._pending_contribution_as_of: str = ""
        self._last_saved_at: str | None = None
        self._plan_restored: bool = False

    @classmethod
    def from_preferences(cls) -> PlanningSession:
        snapshot = load_planning_snapshot()
        restored = snapshot is not None
        if snapshot is None:
            snapshot = PlanningSnapshot(presentation_mode=load_presentation_mode())
        session = cls(snapshot)
        session._plan_restored = restored
        ui = load_session_ui()
        if ui.get("expert_section"):
            session._expert_section = str(ui["expert_section"])
        scope = ui.get("expert_assumptions_scope")
        if scope in {"shared", "self", "wife"}:
            session._expert_assumptions_scope = str(scope)
        elif session.snapshot.household_mode != "joint":
            session._expert_assumptions_scope = "self"
        session._last_results = load_last_results()
        session._last_saved_at = ui.get("plan_saved_at") or ui.get("saved_at")
        if restored:
            seed_touched_fields_from_snapshot(session.snapshot)
        dirty = False
        if session.snapshot.sanitize_invalid_year_fields():
            dirty = True
        if apply_derived_fields(session.snapshot):
            dirty = True
        if dirty:
            session._persist()
        if session.snapshot.presentation_mode == "lazy":
            session.snapshot.presentation_mode = "ai"
            save_presentation_mode("ai")
        return session

    def _persist(self) -> None:
        """Auto-save working draft + UI chrome — does not overwrite the saved plan."""
        try:
            save_session_draft(self.snapshot)
            save_session_ui(
                expert_section=self._expert_section,
                expert_assumptions_scope=self._expert_assumptions_scope,
            )
        except OSError:
            pass

    def _save_plan_to_disk(self) -> None:
        save_planning_snapshot(self.snapshot)
        save_last_results(self._last_results)
        self._last_saved_at = save_session_ui(
            expert_section=self._expert_section,
            expert_assumptions_scope=self._expert_assumptions_scope,
            plan_saved_at=datetime.now(timezone.utc).isoformat(),
        )

    def _build_workflow(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            household_mode=self.snapshot.household_mode,
            include_spouse=self.snapshot.household_mode == "joint",
        )

    def _sync_workflow(self) -> None:
        self._workflow = self._build_workflow()
        if self._step_index >= len(self._workflow.steps):
            self._step_index = max(0, len(self._workflow.steps) - 1)

    def shell_state(self) -> dict[str, Any]:
        step = self._workflow.step_at(self._step_index)
        return {
            "presentation_mode": self.snapshot.presentation_mode,
            "step_index": self._step_index,
            "step_id": step.step_id,
            "total_steps": len(self._workflow.steps),
            "household_mode": self.snapshot.household_mode,
            "assumption_state": self.snapshot.assumption_state,
            "policy_label": active_policy_summary()["label"],
            "simulation_mode": self.snapshot.simulation.simulation_mode,
            "simulation_label": simulation_label_from_mode(self.snapshot.simulation.simulation_mode),
            "expert_section": self._expert_section,
            "expert_assumptions_scope": self._expert_assumptions_scope,
            "live_status": live_status_text(),
            "plaid_connections": _plaid_connections_state(),
            "has_results": self._last_results is not None,
            "last_saved_at": self._last_saved_at,
            "plan_restored": self._plan_restored,
            "completeness": run_plan_readiness(self.snapshot, self._workflow),
            "journey_rail": (
                build_lazy_journey_rail(
                    self.snapshot,
                    current_index=self.snapshot.wizard.lazy_step_index,
                )
                if self.snapshot.presentation_mode in {"ai", "lazy"}
                else build_journey_rail(
                    self._workflow,
                    self.snapshot,
                    current_index=self._step_index,
                )
            ),
            "lazy_step_index": self.snapshot.wizard.lazy_step_index,
            "lazy_total_steps": lazy_section_count(self.snapshot),
            "sub_step_index": self.snapshot.wizard.sub_step_index,
            "data_sources": build_data_sources_summary(self.snapshot),
        }

    def get_completeness(self) -> dict[str, Any]:
        return run_plan_readiness(self.snapshot, self._workflow)

    def get_lazy_screen(self, index: int | None = None) -> dict[str, Any]:
        """AI Mode screen payload (legacy method name get_lazy_screen)."""
        # Drop accidental duplicate imports (same bytes) so the UI stays clean.
        from app.core.lazy_mode.document_library import prune_duplicate_entries

        dirty = False
        pruned = prune_duplicate_entries(self.snapshot)
        if pruned.get("removed"):
            dirty = True
        if self.snapshot.sanitize_invalid_year_fields():
            dirty = True
        if dirty:
            self._persist()

        step = self.snapshot.wizard.lazy_step_index if index is None else index
        screen = build_lazy_screen(self.snapshot, self._workflow, index=step)
        screen["mode"] = "ai"
        screen["title"] = screen.get("title") or "AI Mode"
        if screen.get("section_id") == "welcome" or screen.get("layout") == "lazy_section":
            screen.setdefault(
                "subtitle",
                "Upload documents — local AI fills your facts; you only answer what’s left.",
            )
        return screen

    def get_ai_screen(self, index: int | None = None) -> dict[str, Any]:
        return self.get_lazy_screen(index)

    def go_to_lazy_step(self, index: int) -> dict[str, Any]:
        total = lazy_section_count(self.snapshot)
        index = max(0, min(index, total - 1))
        self.snapshot.wizard.lazy_step_index = index
        self._persist()
        return {"ok": True, "step_index": index}

    def mark_lazy_section_complete(self) -> dict[str, Any]:
        section = lazy_section_at(self.snapshot, self.snapshot.wizard.lazy_step_index)
        if section:
            section_id = str(section["id"])
            if section_id not in self.snapshot.wizard.lazy_completed_sections:
                self.snapshot.wizard.lazy_completed_sections.append(section_id)
            skipped = self.snapshot.wizard.lazy_skipped_sections
            if section_id in skipped:
                skipped.remove(section_id)
        self._persist()
        return {"ok": True}

    def skip_lazy_section(self) -> dict[str, Any]:
        section = lazy_section_at(self.snapshot, self.snapshot.wizard.lazy_step_index)
        if section:
            section_id = str(section["id"])
            if section_id not in self.snapshot.wizard.lazy_skipped_sections:
                self.snapshot.wizard.lazy_skipped_sections.append(section_id)
        self._persist()
        return {"ok": True}

    def advance_lazy_step(self) -> dict[str, Any]:
        self.mark_lazy_section_complete()
        return self.go_to_lazy_step(self.snapshot.wizard.lazy_step_index + 1)

    def lazy_ingest_upload(
        self,
        document_id: str,
        path: str,
        *,
        person: str | None = None,
    ) -> dict[str, Any]:
        from app.core.lazy_mode.orchestrator import ingest_lazy_upload

        return ingest_lazy_upload(
            self.snapshot,
            document_id=document_id,
            path=path,
            person=person,
        )

    def library_add_documents(
        self,
        document_id: str,
        paths: list[str],
        *,
        person: str | None = None,
    ) -> dict[str, Any]:
        from app.core.lazy_mode.document_library import add_files

        result = add_files(
            self.snapshot,
            document_id=document_id,
            paths=paths,
            person=person,
        )
        # Persist even when only duplicates were pruned / skipped
        if result.get("ok") or result.get("duplicate") or result.get("pruned"):
            self._persist()
        return result

    def library_remove_document(self, entry_id: str) -> dict[str, Any]:
        from app.core.lazy_mode.document_library import remove_entry

        result = remove_entry(self.snapshot, entry_id)
        if result.get("ok"):
            self._persist()
        return result

    def library_set_person(self, entry_id: str, person: str | None) -> dict[str, Any]:
        from app.core.lazy_mode.document_library import set_entry_person

        result = set_entry_person(self.snapshot, entry_id, person)
        if result.get("ok"):
            self._persist()
        return result

    def library_extract_document(self, entry_id: str) -> dict[str, Any]:
        from app.core.lazy_mode.orchestrator import extract_library_entry

        result = extract_library_entry(self.snapshot, entry_id)
        self._persist()
        return result

    def library_refresh_document(self, entry_id: str) -> dict[str, Any]:
        from app.core.lazy_mode.orchestrator import refresh_library_entry

        result = refresh_library_entry(self.snapshot, entry_id)
        self._persist()
        return result

    def library_replace_document(self, entry_id: str, path: str) -> dict[str, Any]:
        from app.core.lazy_mode.document_library import replace_entry_file

        result = replace_entry_file(self.snapshot, entry_id, path)
        if result.get("ok"):
            self._persist()
        return result

    def library_extract_pending(
        self,
        document_id: str | None = None,
        *,
        include_applied: bool = False,
    ) -> dict[str, Any]:
        from app.core.lazy_mode.orchestrator import extract_library_pending

        result = extract_library_pending(
            self.snapshot,
            document_id=document_id,
            include_applied=include_applied,
        )
        self._persist()
        return result

    def library_prune_duplicates(self, document_id: str | None = None) -> dict[str, Any]:
        from app.core.lazy_mode.document_library import prune_duplicate_entries

        result = prune_duplicate_entries(self.snapshot, document_id=document_id)
        if result.get("removed"):
            self._persist()
        return result

    def set_presentation_mode(self, mode: str) -> dict[str, Any]:
        if mode == "lazy":
            mode = "ai"
        if mode not in ("guided", "expert", "ai"):
            return {"ok": False, "error": f"unknown mode: {mode}"}
        self.snapshot.presentation_mode = mode
        save_presentation_mode(mode)
        self._persist()
        return {"ok": True, "mode": mode}

    def get_step_documents(self, step_id: str | None = None) -> dict[str, Any]:
        from app.core.document_ingestion.step_documents import build_documents_panel

        sid = step_id or self.snapshot.wizard.last_step_id or "setup"
        return build_documents_panel(self.snapshot, step_id=sid)

    def jump_to_guided_step(self, step_id: str) -> dict[str, Any]:
        idx = self._workflow.index_of(step_id)
        if idx < 0:
            return {"ok": False, "error": f"unknown step: {step_id}"}
        self.snapshot.presentation_mode = "guided"
        save_presentation_mode("guided")
        self.go_to_step(idx)
        return {"ok": True, "step_index": idx, "step_id": step_id}

    def go_to_step(self, index: int) -> dict[str, Any]:
        from app.core.presentation.micro_screens import micro_screen_count

        current = self._workflow.step_at(self._step_index)
        wizard = self.snapshot.wizard
        wizard.sub_step_positions[current.step_id] = wizard.sub_step_index

        index = max(0, min(index, len(self._workflow.steps) - 1))
        self._step_index = index
        step = self._workflow.step_at(index)
        wizard.last_step_id = step.step_id
        wizard.sub_step_index = wizard.sub_step_positions.get(step.step_id, 0)
        total_micro = micro_screen_count(step.step_id)
        if wizard.sub_step_index >= total_micro:
            wizard.sub_step_index = 0
        wizard.sub_step_positions[step.step_id] = wizard.sub_step_index
        self._persist()
        return {
            "ok": True,
            "step_index": index,
            "step_id": step.step_id,
            "sub_step_index": wizard.sub_step_index,
        }

    def set_sub_step(self, sub_index: int) -> dict[str, Any]:
        from app.core.presentation.micro_screens import micro_screen_count

        step = self._workflow.step_at(self._step_index)
        total = micro_screen_count(step.step_id)
        sub_index = max(0, min(sub_index, total - 1))
        self.snapshot.wizard.sub_step_index = sub_index
        self.snapshot.wizard.sub_step_positions[step.step_id] = sub_index
        self._persist()
        return {
            "ok": True,
            "sub_step_index": sub_index,
            "sub_total": total,
            "step_id": step.step_id,
        }

    def get_guided_screen(self, index: int | None = None) -> dict[str, Any]:
        idx = self._step_index if index is None else index
        return build_guided_screen(self.snapshot, self._workflow, index=idx)

    def _touch_field_key(self, field_key: str, person: str | None = None) -> None:
        field = resolve_completeness_field(field_key, person=person)
        if field is not None:
            scoped_person = person if field.scope == "person" else None
            mark_field_touched(self.snapshot, field, person=scoped_person)

    def persist_setup(self, household_mode: str, state: str) -> dict[str, Any]:
        if household_mode in ("joint", "separate"):
            self.snapshot.household_mode = household_mode
        self.snapshot.assumption_state = state
        self.snapshot.shared["state_of_residence"] = state
        self._touch_field_key("state_of_residence")
        self._sync_workflow()
        if self.snapshot.household_mode != "joint":
            self._expert_assumptions_scope = "self"
        self._persist()
        return {"ok": True, "policy_label": active_policy_summary()["label"]}

    def persist_step_values(self, updates: list[dict[str, Any]], *, validate: bool = True) -> dict[str, Any]:
        """Persist field edits for the current step. Updates: {key, value, person?, scope?}."""
        if not validate:
            return self._persist_step_draft(updates)

        step = self._workflow.step_at(self._step_index)
        if step.step_id == "setup":
            household = "joint"
            state = self.snapshot.assumption_state
            for row in updates:
                if row.get("key") == "household_mode":
                    label = str(row.get("value", ""))
                    household = "separate" if "separat" in label.lower() else "joint"
                if row.get("key") == "state_of_residence":
                    state = str(row.get("value", state))
            return self.persist_setup(household, state)

        step_id = step.step_id
        step_check = validate_guided_step(self.snapshot, step_id, updates, required=True)
        if not step_check["ok"]:
            return {"ok": False, **step_check}

        parsed = [
            FieldUpdate(
                key=str(row["key"]),
                value=str(row.get("value", "")),
                person=row.get("person"),
                scope=row.get("scope"),
            )
            for row in updates
            if row.get("key")
        ]
        field_errors = apply_updates(self.snapshot, parsed)
        if field_errors:
            return {
                "ok": False,
                "errors": [f"{row['label']}: {row['message']}" for row in field_errors],
                "field_errors": field_errors,
            }
        mark_updates_touched(self.snapshot, updates)
        apply_derived_fields(self.snapshot, updates=updates)
        self._persist()
        return {"ok": True}

    def validate_field_updates(
        self,
        updates: list[dict[str, Any]],
        *,
        required: bool = False,
    ) -> dict[str, Any]:
        field_errors = validate_updates(updates, required=required)
        return {
            "ok": not field_errors,
            "errors": [f"{row['label']}: {row['message']}" for row in field_errors],
            "field_errors": field_errors,
        }

    def validate_current_step(
        self,
        updates: list[dict[str, Any]] | None = None,
        *,
        required: bool = True,
    ) -> dict[str, Any]:
        step_id = self._workflow.step_at(self._step_index).step_id
        return validate_guided_step(self.snapshot, step_id, updates, required=required)

    def _persist_step_draft(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        """Best-effort persist without validation — matches Tk binder.pull_all()."""
        from app.core.presentation.field_editor import resolve_field

        step = self._workflow.step_at(self._step_index)
        if step.step_id == "setup":
            household = "joint"
            state = self.snapshot.assumption_state
            for row in updates:
                if row.get("key") == "household_mode":
                    label = str(row.get("value", ""))
                    household = "separate" if "separat" in label.lower() else "joint"
                if row.get("key") == "state_of_residence":
                    state = str(row.get("value", state))
            return self.persist_setup(household, state)

        for row in updates:
            key = row.get("key")
            if not key:
                continue
            field = resolve_field(
                str(key),
                scope=row.get("scope"),
                person=row.get("person"),
            )
            if field is None:
                continue
            person = row.get("person") if field.scope == "person" else None
            raw = str(row.get("value", "")).strip()
            if field.field_type == "money":
                raw = raw.replace(",", "").replace("$", "").strip()
            if field.field_type == "rate_pct":
                raw = raw.replace("%", "").strip()
            self.snapshot.set_raw(field, raw, person=person)
        mark_updates_touched(self.snapshot, updates)
        apply_derived_fields(self.snapshot, updates=updates)
        self._persist()
        return {"ok": True}

    def persist_simulation_mode(self, label: str) -> dict[str, Any]:
        mode = simulation_mode_from_label(label)
        if mode:
            self.snapshot.simulation.simulation_mode = mode
        self._persist()
        return {"ok": True, "simulation_mode": self.snapshot.simulation.simulation_mode}

    def mark_step_complete(self) -> dict[str, Any]:
        step_id = self._workflow.step_at(self._step_index).step_id
        if step_id not in self.snapshot.wizard.completed_steps:
            self.snapshot.wizard.completed_steps.append(step_id)
        self._persist()
        return {"ok": True}

    def mark_step_skipped(self) -> dict[str, Any]:
        step_id = self._workflow.step_at(self._step_index).step_id
        if step_id == "review":
            return {"ok": False}
        if step_id not in self.snapshot.wizard.skipped_steps:
            self.snapshot.wizard.skipped_steps.append(step_id)
        self._persist()
        return {"ok": True}

    def collect_simulation_params(self) -> dict[str, Any]:
        return self.snapshot.to_simulation_params()

    def simulation_warnings(self) -> list[str]:
        return self._simulation.collect_warnings(self.snapshot)

    def run_simulation(self, *, confirm_cpa_mc_paths: bool = True) -> ActionResult:
        return self._simulation.run(self.snapshot, confirm_cpa_mc_paths=confirm_cpa_mc_paths)

    def get_llm_panel(self) -> dict[str, Any]:
        return build_llm_panel_payload(self.snapshot)

    def persist_llm_prompt(self, text: str) -> dict[str, Any]:
        self.snapshot.llm_extra_prompt = str(text).strip()
        self._persist()
        return {"ok": True}

    def run_llm_research(self, selections: list[dict[str, str | None]], *, extra_prompt: str = "") -> dict[str, Any]:
        return run_llm_batch(self.snapshot, selections=selections, extra_prompt=extra_prompt)

    def apply_llm_research(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        result = apply_llm_suggestions(self.snapshot, rows)
        if result.get("ok"):
            self._persist()
        return result

    def ingest_document_file(self, path: str) -> dict[str, Any]:
        return ingest_document(self.snapshot, path)

    def apply_document_import(
        self,
        rows: list[dict[str, Any]],
        *,
        document_type: str = "",
        document_label: str = "",
        library_entry_id: str | None = None,
    ) -> dict[str, Any]:
        result = apply_document_import(
            self.snapshot,
            rows,
            document_type=document_type,
            document_label=document_label,
        )
        if result.get("ok") and library_entry_id:
            from app.core.lazy_mode.document_library import mark_entry_status

            mark_entry_status(
                self.snapshot,
                library_entry_id,
                status="applied",
                message=str(result.get("message") or "Applied"),
                field_count=int(result.get("applied") or 0),
            )
        if result.get("ok"):
            apply_derived_fields(self.snapshot)
            self._persist()
        return result

    def get_expert_screen(self, section_id: str | None = None) -> dict[str, Any]:
        sid = section_id or self._expert_section
        return build_expert_screen(
            self.snapshot,
            sid,
            assumptions_scope=self._expert_assumptions_scope,
            last_saved_at=self._last_saved_at,
        )

    def save_plan(self) -> dict[str, Any]:
        """Persist the current plan so it loads automatically on next launch."""
        try:
            self._save_plan_to_disk()
        except OSError:
            return {"ok": False, "message": "Could not save plan to disk."}
        self._plan_restored = True
        return {
            "ok": True,
            "saved_at": self._last_saved_at,
            "message": "Plan saved. It will load automatically next time you open the app.",
        }

    def start_new_plan(self) -> dict[str, Any]:
        """Load a blank template into the working session — last saved plan is untouched."""
        mode = self.snapshot.presentation_mode
        self.snapshot = blank_planning_snapshot(presentation_mode=mode)
        self._workflow = self._build_workflow()
        self._step_index = 0
        self._last_results = None
        self._expert_section = "setup"
        self._expert_assumptions_scope = "shared" if self.snapshot.household_mode == "joint" else "self"
        self._plan_restored = False
        self._persist()
        return {
            "ok": True,
            "saved_at": self._last_saved_at,
            "step_index": self._step_index,
            "message": (
                "Loaded a blank plan template (zeros). Your last saved plan is unchanged — "
                "restart the app to reload it, or Save plan to replace it."
            ),
        }

    def set_expert_scope(self, scope: str) -> dict[str, Any]:
        if self.snapshot.household_mode != "joint":
            self._expert_assumptions_scope = "self"
        elif scope in {"shared", "self", "wife"}:
            self._expert_assumptions_scope = scope
        else:
            self._expert_assumptions_scope = "shared"
        self._persist()
        return {"ok": True, "scope": self._expert_assumptions_scope}

    def get_expert_nav(self) -> list[dict[str, str]]:
        return expert_nav()

    def set_expert_section(self, section_id: str) -> dict[str, Any]:
        self._expert_section = section_id
        self._persist()
        return {"ok": True, "section_id": section_id}

    def persist_expert_fields(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        if self._expert_section == "setup":
            household = self.snapshot.household_mode
            state = self.snapshot.assumption_state
            for row in updates:
                if row.get("key") == "household_mode":
                    label = str(row.get("value", ""))
                    household = "separate" if "separat" in label.lower() else "joint"
                if row.get("key") == "state_of_residence":
                    state = str(row.get("value", state))
            result = self.persist_setup(household, state)
            if household != "joint":
                self._expert_assumptions_scope = "self"
            return result
        return self.persist_step_values(updates, validate=False)

    def persist_expert_simulation(self, updates: dict[str, Any]) -> dict[str, Any]:
        sim = self.snapshot.simulation
        if "simulation_mode" in updates:
            raw = str(updates["simulation_mode"])
            _expert_modes = {
                "Deterministic": "deterministic",
                "Monte Carlo": "monte_carlo",
                "Both": "both",
            }
            mode = simulation_mode_from_label(raw) or _expert_modes.get(raw) or raw
            if mode in {"deterministic", "monte_carlo", "both"}:
                sim.simulation_mode = mode
        if "planning_precision" in updates:
            sim.planning_precision = str(updates["planning_precision"])
        if "num_paths" in updates:
            try:
                sim.num_paths = int(str(updates["num_paths"]).strip())
            except ValueError:
                pass
        if "mc_stock_allocation_pct" in updates:
            try:
                sim.mc_stock_allocation_pct = float(str(updates["mc_stock_allocation_pct"]).strip())
            except ValueError:
                pass
        if "risk_tolerance" in updates:
            sim.risk_tolerance = str(updates["risk_tolerance"])
        self._persist()
        return {"ok": True}

    def get_live_data_panel(self) -> dict[str, Any]:
        return build_live_data_payload(self.snapshot)

    def get_spending_builder(self, person: str = "self") -> dict[str, Any]:
        from app.core.presentation.spending_builder import (
            build_spending_builder,
            suggest_expense_phase_pcts_from_amounts,
        )

        payload = build_spending_builder(self.snapshot, person=person)
        amounts = {c["id"]: c["annual"] for c in payload.get("categories") or []}
        phase = suggest_expense_phase_pcts_from_amounts(amounts)
        return {"ok": True, "phase_suggestion": phase, **payload}

    def apply_spending_builder(
        self,
        person: str,
        amounts: dict[str, Any],
        mortgage_payoff_year: str | None = None,
    ) -> dict[str, Any]:
        from app.core.presentation.spending_builder import apply_spending_builder

        result = apply_spending_builder(
            self.snapshot,
            person=person,
            amounts=amounts or {},
            mortgage_payoff_year=mortgage_payoff_year,
        )
        if result.get("ok"):
            self._persist()
        return result

    def suggest_expense_phase_pcts(self, person: str = "self") -> dict[str, Any]:
        from app.core.presentation.spending_builder import apply_suggested_expense_phase_pcts

        result = apply_suggested_expense_phase_pcts(self.snapshot, person=person)
        if result.get("ok"):
            self._persist()
        return result

    def suggest_spending_averages(self, person: str = "self") -> dict[str, Any]:
        from app.core.presentation.spending_builder import suggest_spending_averages_with_llm

        return suggest_spending_averages_with_llm(self.snapshot, person=person)

    def ai_categorize_expenses(
        self,
        rows: list[dict[str, Any]] | None = None,
        backend: str = "local",
    ) -> dict[str, Any]:
        """AI-label merchants as food / dining / entertainment / other (etc.)."""
        from app.core.presentation.spending_builder import ai_categorize_expense_rows
        from providers.local_doc_ai import local_ai_available

        if rows is None:
            service = DataService()
            cached = service.load_cached_expense_snapshot()
            if not cached or not cached.analysis:
                return {"ok": False, "message": "No expenses on file. Refresh expenses first."}
            rows = [
                {
                    "merchant": item.merchant,
                    "category": item.category,
                    "annual_amount": item.annual_amount,
                    "monthly_amount": item.monthly_amount,
                    "expense_type": item.expense_type,
                    "included": item.included,
                    "owner": item.owner,
                }
                for item in cached.analysis.items
            ]
        result = ai_categorize_expense_rows(rows or [], backend=backend)
        result["local_available"] = local_ai_available()
        if result.get("ok") and result.get("rows"):
            service = DataService()
            cached = service.load_cached_expense_snapshot()
            if cached and cached.analysis:
                by_m = {
                    str(r.get("merchant") or ""): str(r.get("category") or "")
                    for r in result["rows"]
                }
                for item in cached.analysis.items:
                    if item.merchant in by_m and by_m[item.merchant]:
                        item.category = by_m[item.merchant]
                service.save_expense_snapshot(cached)
        return result

    def fill_spending_from_expenses_ai(
        self,
        person: str = "self",
        backend: str = "local",
    ) -> dict[str, Any]:
        from app.core.presentation.spending_builder import (
            fill_lifestyle_spending_from_expenses_ai,
        )
        from providers.local_doc_ai import local_ai_available

        result = fill_lifestyle_spending_from_expenses_ai(
            self.snapshot,
            person=person,
            backend=backend,
        )
        result["local_available"] = local_ai_available()
        if result.get("ok"):
            self._persist()
        return result

    def ai_backend_status(self) -> dict[str, Any]:
        """Which LLM backends are available for jobs that can stay offline."""
        from providers.local_doc_ai import host_label, local_ai_available
        from settings import load_llm_settings
        from security.llm_keychain import has_api_key

        settings = load_llm_settings()
        cloud_ok = bool(settings.configured and has_api_key(settings.provider))
        local_ok = local_ai_available()
        return {
            "ok": True,
            "local_available": local_ok,
            "local_label": host_label() if local_ok else "Ollama not running (127.0.0.1:11434)",
            "cloud_available": cloud_ok,
            "cloud_label": (
                f"{settings.provider}/{settings.model}"
                if cloud_ok
                else "Cloud API not configured (LLM Settings)"
            ),
            "hint": (
                "Document extract & merchant categorize can use Local Ollama. "
                "AI Estimates / typical regional averages use Cloud API."
            ),
        }

    def prepare_run_plan(self) -> dict[str, Any]:
        from app.core.presentation.pre_run_review import build_pre_run_review

        readiness = run_plan_readiness(self.snapshot, self._workflow)
        needs_cpa = self._simulation.needs_cpa_mc_confirmation(self.snapshot)
        review = build_pre_run_review(self.snapshot)
        if not readiness["ready"]:
            missing_count = readiness["missing_only_count"]
            invalid_count = readiness["invalid_count"]
            parts: list[str] = []
            if missing_count:
                parts.append(
                    f"fill in {missing_count} required field{'s' if missing_count != 1 else ''}"
                )
            if invalid_count:
                parts.append(
                    f"fix {invalid_count} invalid value{'s' if invalid_count != 1 else ''}"
                )
            detail = " and ".join(parts) if parts else "complete your plan"
            return {
                "ok": False,
                "ready": False,
                "blocked": True,
                "completeness": readiness,
                "review": review,
                "warnings": [],
                "needs_cpa_confirm": False,
                "num_paths": self.snapshot.simulation.num_paths,
                "message": f"Before running your plan, {detail}.",
            }
        return {
            "ok": True,
            "ready": True,
            "blocked": False,
            "completeness": readiness,
            "review": review,
            "warnings": self.simulation_warnings(),
            "needs_cpa_confirm": needs_cpa,
            "num_paths": self.snapshot.simulation.num_paths,
        }

    def run_plan_web(self, *, confirm_cpa: bool = True) -> dict[str, Any]:
        readiness = run_plan_readiness(self.snapshot, self._workflow)
        if not readiness["ready"]:
            missing_count = readiness["missing_only_count"]
            invalid_count = readiness["invalid_count"]
            parts: list[str] = []
            if missing_count:
                parts.append(
                    f"fill in {missing_count} required field{'s' if missing_count != 1 else ''}"
                )
            if invalid_count:
                parts.append(
                    f"fix {invalid_count} invalid value{'s' if invalid_count != 1 else ''}"
                )
            detail = " and ".join(parts) if parts else "complete your plan"
            return {
                "ok": False,
                "blocked": True,
                "completeness": readiness,
                "message": f"Before running your plan, {detail}.",
            }
        if self._simulation.needs_cpa_mc_confirmation(self.snapshot) and not confirm_cpa:
            return {
                "ok": False,
                "needs_cpa_confirm": True,
                "message": f"CPA Precision with {self.snapshot.simulation.num_paths:,} paths may be slow.",
            }
        result = self.run_simulation(confirm_cpa_mc_paths=confirm_cpa)
        if not result.ok:
            payload = result.payload if isinstance(result.payload, dict) else {}
            return {
                "ok": False,
                "message": result.message or result.title,
                "title": result.title,
                "cancelled": result.title == "Simulation Cancelled",
                "field_key": payload.get("field_key"),
                "step_id": payload.get("step_id"),
                "field_label": payload.get("label"),
            }
        if result.payload is None:
            return {"ok": False, "message": "Simulation returned no data."}
        holdings = None
        try:
            holdings = DataService().get_portfolio_holdings()
        except Exception:
            holdings = None
        self._last_results = serialize_simulation_result(
            result.payload,
            risk_tolerance=self.snapshot.simulation.risk_tolerance,
            holdings=holdings,
        )
        self._persist()
        return {
            "ok": True,
            "message": "Simulation complete.",
            "results": self._last_results,
            "warnings": result.payload.warnings,
        }

    def get_last_results(self) -> dict[str, Any] | None:
        return self._last_results

    def apply_what_if(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        from config.model_fields import FIELDS_BY_KEY

        applied = 0
        for change in changes:
            field = FIELDS_BY_KEY.get(str(change.get("field_key", "")))
            if field is None:
                continue
            person = change.get("person")
            if field.scope == "person" and not person:
                continue
            self.snapshot.set_parsed(field, change.get("new_value"), person=person)
            self.snapshot.set_source(person if field.scope == "person" else None, field.key, "Adviser what-if")
            applied += 1
        if not applied:
            return {"ok": False, "message": "No applicable what-if changes."}
        self._persist()
        return {
            "ok": True,
            "message": "What-if values applied to assumptions. Re-run the plan to see updated results.",
            "applied": applied,
        }

    def parse_import_csv(self, path: str) -> dict[str, Any]:
        try:
            rows = parse_balance_csv(path)
        except (OSError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "rows": serialize_csv_rows(rows)}

    def apply_import_csv(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        totals = totals_from_import_rows(rows)
        apply_owner_totals(self.snapshot, totals)
        self._persist()
        return {
            "ok": True,
            "message": f"Applied balances — self ${totals.get('self', 0):,.0f}, wife ${totals.get('wife', 0):,.0f}",
            "totals": totals,
        }

    def refresh_balances_review(self, *, mode: str = "refresh") -> dict[str, Any]:
        from security.keychain import list_item_ids

        try:
            service = DataService()
            if mode == "manage":
                if not list_item_ids():
                    return {"ok": False, "message": "Connect an institution first."}
                snapshot = service.snapshot_for_account_mapping()
            else:
                if not list_item_ids():
                    return {"ok": False, "message": "Connect an institution first."}
                snapshot = service.refresh_live_data()
                if snapshot.errors and not snapshot.accounts:
                    return {"ok": False, "message": "\n".join(snapshot.errors)}
            review = serialize_balance_review(
                snapshot,
                mode=mode,
                service=service if mode == "manage" else None,
            )
            return {"ok": True, "review": review}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def disconnect_plaid_item(self, item_id: str) -> dict[str, Any]:
        from security.keychain import list_item_ids

        try:
            service = DataService()
            result = disconnect_plaid_institution(service, item_id)
            if not result.get("ok"):
                return result
            if not list_item_ids():
                return {
                    "ok": True,
                    "message": result["message"],
                    "status": result.get("status"),
                    "disconnected_all": True,
                }
            snapshot = service.snapshot_for_account_mapping()
            review = serialize_balance_review(snapshot, mode="manage", service=service)
            return {
                "ok": True,
                "message": result["message"],
                "review": review,
                "status": result.get("status"),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def apply_balances_review(self, rows: list[dict[str, Any]], apply_assets: bool = True) -> dict[str, Any]:
        try:
            service = DataService()
            result = apply_balance_review(self.snapshot, service, rows, apply_assets=apply_assets)
            if result.get("ok"):
                apply_derived_fields(self.snapshot)
                self._persist()
            return result
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def refresh_expenses_review(self) -> dict[str, Any]:
        try:
            service = DataService()
            snapshot = service.refresh_expenses()
            if snapshot.analysis.transactions_used == 0:
                return {"ok": False, "message": "No expense transactions found yet."}
            self._pending_expense_as_of = snapshot.as_of
            review = serialize_expense_review(snapshot, self.snapshot.household_mode)
            return {"ok": True, "review": review, "as_of": snapshot.as_of}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def apply_expenses_review(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            from app.core.presentation.spending_builder import (
                copy_healthcare_from_expense_rows,
                sync_expense_rows_into_spending_breakdown,
            )

            from app.core.presentation.live_data_ops import base_living_category_breakdown

            totals = owner_totals_from_line_items(
                rows,
                self.snapshot.household_mode,
                base_living_only=True,
            )
            breakdown = base_living_category_breakdown(rows)
            service = DataService()
            cached = service.load_cached_expense_snapshot()
            if cached and cached.analysis and cached.analysis.items:
                by_key = {
                    (str(item.merchant), str(item.owner)): item
                    for item in cached.analysis.items
                }
                for row in rows:
                    key = (str(row.get("merchant") or ""), str(row.get("owner") or ""))
                    item = by_key.get(key)
                    if item is None:
                        # Owner may have changed — match merchant only
                        item = next(
                            (
                                it
                                for it in cached.analysis.items
                                if it.merchant == row.get("merchant")
                            ),
                            None,
                        )
                    if item is None:
                        continue
                    item.included = bool(row.get("included"))
                    owner = str(row.get("owner") or item.owner)
                    if owner in {"self", "wife", "unassigned", "exclude"}:
                        item.owner = owner
                    expense_type = str(row.get("expense_type") or "").lower()
                    if expense_type in {"recurring", "variable"}:
                        item.expense_type = expense_type
                    category = str(row.get("category") or "").strip().lower()
                    if category:
                        item.category = category
                service.save_expense_snapshot(cached)
            apply_owner_expenses(self.snapshot, totals, expense_snapshot=cached)
            healthcare = copy_healthcare_from_expense_rows(self.snapshot, rows)
            sync_expense_rows_into_spending_breakdown(self.snapshot, rows)
            self._persist()
            parts = ", ".join(f"{k} ${v:,.0f}" for k, v in list(breakdown.items())[:8])
            health_note = ""
            if healthcare:
                bits = ", ".join(f"{k} ${v:,.0f}" for k, v in healthcare.items())
                health_note = f" Healthcare premium from checked bills ({bits}) stays out of base."
            return {
                "ok": True,
                "message": (
                    f"Base Expenses = recurring bills + food + dining + entertainment + travel "
                    f"(ignores other/education/mortgage/healthcare). "
                    f"Self ${totals.get('self', 0):,.0f}, wife ${totals.get('wife', 0):,.0f}"
                    + (f" · {parts}" if parts else "")
                    + health_note
                    + ". Edit further in Spending builder anytime."
                ),
                "totals": totals,
                "base_breakdown": breakdown,
                "status": live_status_text(),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def refresh_contributions_review(self) -> dict[str, Any]:
        try:
            service = DataService()
            snapshot = service.refresh_contributions()
            if snapshot.analysis.transactions_used == 0:
                return {"ok": False, "message": "No contribution deposits found."}
            self._pending_contribution_as_of = snapshot.as_of
            review = serialize_contribution_review(snapshot, self.snapshot.household_mode)
            return {"ok": True, "review": review, "as_of": snapshot.as_of}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def apply_contributions_review(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            totals = owner_totals_from_line_items(rows, self.snapshot.household_mode)
            service = DataService()
            cached = service.load_cached_contribution_snapshot()
            apply_owner_contributions(
                self.snapshot,
                totals,
                as_of=self._pending_contribution_as_of or (cached.as_of if cached else ""),
            )
            self._persist()
            return {
                "ok": True,
                "message": (
                    f"Annual Contributions updated — self ${totals.get('self', 0):,.0f}, "
                    f"wife ${totals.get('wife', 0):,.0f}"
                ),
                "totals": totals,
                "status": live_status_text(),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def refresh_market_data_web(self) -> dict[str, Any]:
        try:
            service = DataService()
            snapshot = service.refresh_market_data()
            apply_market_snapshot(self.snapshot, snapshot)
            self._persist()
            return {
                "ok": True,
                "message": market_status_text(snapshot),
                "status": live_status_text(),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def refresh_live_balances(self) -> dict[str, Any]:
        """Backward-compatible alias — returns review payload instead of auto-applying."""
        return self.refresh_balances_review(mode="refresh")

    def get_llm_settings(self) -> dict[str, Any]:
        settings = load_llm_settings()
        return {
            "ok": True,
            "provider": settings.provider,
            "model": settings.model,
            "configured": settings.configured,
        }

    def save_llm_settings(self, provider: str, model: str, api_key: str | None = None) -> dict[str, Any]:
        from security.llm_keychain import store_api_key
        from settings import LLMSettings

        try:
            settings = LLMSettings(
                provider=str(provider or "none").strip().lower() or "none",
                model=str(model or "").strip(),
            )
            save_llm_settings(settings)
            if api_key and str(api_key).strip():
                store_api_key(settings.provider, str(api_key).strip())
            return {
                "ok": True,
                "message": f"LLM settings saved ({settings.provider}"
                + (f" / {settings.model}" if settings.model else "")
                + ").",
            }
        except Exception as exc:
            return {"ok": False, "message": f"Could not save LLM settings: {exc}"}
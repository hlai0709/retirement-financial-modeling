"""Thin pywebview bridge — delegates all logic to app.core.presentation."""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.core.presentation import PlanningSession
from gui.version import APP_BUILD_LABEL
from gui_web import file_picker, plaid_web


def _clear_runtime_caches() -> None:
    """Drop cached Lazy/config JSON so a restart picks up file edits."""
    try:
        from app.core.lazy_mode.manifest import load_document_manifest

        load_document_manifest.cache_clear()
    except Exception:
        pass
    try:
        from app.core.lazy_mode.sections import load_lazy_sections

        load_lazy_sections.cache_clear()
    except Exception:
        pass
    try:
        from app.core.lazy_mode.manifest import load_document_manifest

        load_document_manifest.cache_clear()
    except Exception:
        pass
    try:
        from providers.document_extractors.w2_pdf import _load_parser_config

        _load_parser_config.cache_clear()
    except Exception:
        pass
    try:
        from providers.document_extractors.pay_stub_pdf import _load_parser_config as _pay_stub_cfg

        _pay_stub_cfg.cache_clear()
    except Exception:
        pass
    try:
        from app.core.document_ingestion.step_documents import _load_field_document_map

        _load_field_document_map.cache_clear()
    except Exception:
        pass


class WebAppApi:
    """
    Methods exposed to JavaScript as `await pywebview.api.<name>(...)`.

    This class must stay thin: no business rules here.
    """

    def __init__(self) -> None:
        self._session = PlanningSession.from_preferences()
        self.restart_requested = False
        self._redact_lock = threading.Lock()
        self._redact_job: dict[str, Any] = {
            "state": "idle",  # idle | running | done | error
            "percent": 0,
            "page": 0,
            "pages": 0,
            "phase": "",
            "message": "",
            "ok": None,
            "result_message": "",
            "used_ai": False,
            "input_path": "",
            "tmp_path": "",
            "tmp_dir": "",
            "default_name": "",
            "elapsed_seconds": 0,
            "eta_seconds": None,
            "eta_label": "",
        }

    @property
    def session(self) -> PlanningSession:
        return self._session

    def get_state(self) -> dict[str, Any]:
        state = self._session.shell_state()
        state["build_label"] = APP_BUILD_LABEL
        return state

    def get_guided_screen(self, index: int | None = None) -> dict[str, Any]:
        return self._session.get_guided_screen(index)

    def set_presentation_mode(self, mode: str) -> dict[str, Any]:
        return self._session.set_presentation_mode(mode)

    def get_step_documents(self, step_id: str | None = None) -> dict[str, Any]:
        return self._session.get_step_documents(step_id)

    def _upload_file_types(self, document_id: str) -> tuple[str, ...]:
        if document_id == "form_1040":
            return (
                "PDF files (*.pdf)",
                "JSON files (*.json)",
                "All files (*.*)",
            )
        return ("PDF files (*.pdf)", "All files (*.*)")

    def upload_step_document(self, document_id: str, person: str | None = None) -> dict[str, Any]:
        """Add one or more files to the document library (does not extract yet)."""
        return self.library_add_documents(document_id, person=person)

    # Back-compat aliases used by older live-app.js builds
    def lazy_upload_document(self, document_id: str, person: str | None = None) -> dict[str, Any]:
        return self.upload_step_document(document_id, person=person)

    def library_add_documents(self, document_id: str, person: str | None = None) -> dict[str, Any]:
        """Multi-select files and queue them in the plan's document library."""
        types = self._upload_file_types(document_id)
        paths = file_picker.pick_open_files(file_types=types, allow_multiple=True)
        if not paths:
            return {"ok": False, "cancelled": True, "message": "Upload cancelled."}
        return self._session.library_add_documents(document_id, paths, person=person)

    def library_remove_document(self, entry_id: str) -> dict[str, Any]:
        return self._session.library_remove_document(entry_id)

    def library_set_person(self, entry_id: str, person: str | None = None) -> dict[str, Any]:
        return self._session.library_set_person(entry_id, person)

    def library_extract_document(self, entry_id: str) -> dict[str, Any]:
        return self._session.library_extract_document(entry_id)

    def library_refresh_document(self, entry_id: str) -> dict[str, Any]:
        """Re-run extract on an already imported library file."""
        return self._session.library_refresh_document(entry_id)

    def library_replace_document(self, entry_id: str) -> dict[str, Any]:
        """Pick a new PDF for an existing library slot, then queue for extract."""
        types = ("PDF files (*.pdf)", "All files (*.*)")
        paths = file_picker.pick_open_files(file_types=types, allow_multiple=False)
        if not paths:
            return {"ok": False, "cancelled": True, "message": "Replace cancelled."}
        return self._session.library_replace_document(entry_id, paths[0])

    def library_extract_pending(
        self,
        document_id: str | None = None,
        include_applied: bool = False,
    ) -> dict[str, Any]:
        return self._session.library_extract_pending(
            document_id,
            include_applied=bool(include_applied),
        )

    def library_prune_duplicates(self, document_id: str | None = None) -> dict[str, Any]:
        return self._session.library_prune_duplicates(document_id)

    def get_lazy_screen(self, index: int | None = None) -> dict[str, Any]:
        return self._session.get_lazy_screen(index)

    def get_ai_screen(self, index: int | None = None) -> dict[str, Any]:
        return self._session.get_ai_screen(index)

    def go_to_lazy_step(self, index: int) -> dict[str, Any]:
        return self._session.go_to_lazy_step(index)

    def advance_lazy_step(self) -> dict[str, Any]:
        return self._session.advance_lazy_step()

    def skip_lazy_section(self) -> dict[str, Any]:
        self._session.skip_lazy_section()
        return self._session.advance_lazy_step()

    def lazy_ingest_upload(self, document_id: str, path: str) -> dict[str, Any]:
        return self._session.lazy_ingest_upload(document_id, path)

    def jump_to_guided_step(self, step_id: str) -> dict[str, Any]:
        return self._session.jump_to_guided_step(step_id)

    def go_to_step(self, index: int) -> dict[str, Any]:
        return self._session.go_to_step(index)

    def set_sub_step(self, sub_index: int) -> dict[str, Any]:
        return self._session.set_sub_step(sub_index)

    def persist_step_values(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.persist_step_values(updates, validate=True)

    def persist_step_draft(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.persist_step_values(updates, validate=False)

    def validate_field_updates(
        self,
        updates: list[dict[str, Any]],
        required: bool = False,
    ) -> dict[str, Any]:
        return self._session.validate_field_updates(updates, required=bool(required))

    def validate_current_step(
        self,
        updates: list[dict[str, Any]] | None = None,
        required: bool = True,
    ) -> dict[str, Any]:
        return self._session.validate_current_step(updates, required=bool(required))

    def persist_setup(self, household_mode: str, state: str) -> dict[str, Any]:
        return self._session.persist_setup(household_mode, state)

    def persist_simulation_mode(self, mode: str) -> dict[str, Any]:
        return self._session.persist_simulation_mode(mode)

    def mark_step_complete(self) -> dict[str, Any]:
        return self._session.mark_step_complete()

    def mark_step_skipped(self) -> dict[str, Any]:
        return self._session.mark_step_skipped()

    def get_llm_panel(self) -> dict[str, Any]:
        return self._session.get_llm_panel()

    def persist_llm_prompt(self, text: str) -> dict[str, Any]:
        return self._session.persist_llm_prompt(text)

    def run_llm_research(self, selections: list[dict[str, str | None]], extra_prompt: str = "") -> dict[str, Any]:
        return self._session.run_llm_research(selections, extra_prompt=extra_prompt)

    def apply_llm_research(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.apply_llm_research(rows)

    def get_expert_nav(self) -> list[dict[str, str]]:
        return self._session.get_expert_nav()

    def get_expert_screen(self, section_id: str | None = None) -> dict[str, Any]:
        return self._session.get_expert_screen(section_id)

    def set_expert_section(self, section_id: str) -> dict[str, Any]:
        return self._session.set_expert_section(section_id)

    def set_expert_scope(self, scope: str) -> dict[str, Any]:
        return self._session.set_expert_scope(scope)

    def persist_expert_fields(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.persist_expert_fields(updates)

    def persist_expert_simulation(self, updates: dict[str, Any]) -> dict[str, Any]:
        return self._session.persist_expert_simulation(updates)

    def save_plan(self) -> dict[str, Any]:
        return self._session.save_plan()

    def start_new_plan(self) -> dict[str, Any]:
        return self._session.start_new_plan()

    def restart_app(self) -> dict[str, Any]:
        """Save plan, close the window, and re-exec the process (full app restart)."""
        try:
            self._session.save_plan()
        except Exception:
            try:
                self._session._persist()
            except Exception:
                pass
        _clear_runtime_caches()
        self.restart_requested = True
        try:
            import webview

            for window in list(webview.windows):
                window.destroy()
        except Exception:
            pass
        return {"ok": True, "message": "Restarting…"}

    def pdf_redactor_capabilities(self) -> dict[str, Any]:
        """Test feature: whether local PDF redaction deps are available."""
        from providers.pdf_redactor import capabilities

        return capabilities()

    def _redact_set(self, **kwargs: Any) -> None:
        with self._redact_lock:
            self._redact_job.update(kwargs)

    def pdf_redact_status(self) -> dict[str, Any]:
        """Poll background redaction progress (percent, page, message)."""
        with self._redact_lock:
            return dict(self._redact_job)

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool = False) -> bool:
        """
        Robust bool for pywebview JS bridges.

        JS ``false`` is sometimes dropped (so the Python default applies) or
        arrives as 0 / \"0\" / \"false\". Prefer explicit 0/1 from the UI.
        """
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        s = str(value).strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off", ""}:
            return False
        return default

    def pdf_redact_start(self, use_ai: int = 0, custom_rules: str = "") -> dict[str, Any]:
        """
        Pick a PDF, then redact in a background thread.

        use_ai: 1 = AI + form rules + regex; 0 = form rules + regex only (fast).
        Pass integers from JS (pywebview is unreliable with boolean false).

        Poll pdf_redact_status until state is done/error, then pdf_redact_save.
        """
        from providers.pdf_redactor import capabilities, redact_pdf

        with self._redact_lock:
            if self._redact_job.get("state") == "running":
                return {"ok": False, "message": "A redaction is already in progress."}

        caps = capabilities()
        if not caps.get("ready"):
            return {
                "ok": False,
                "message": "PDF redaction is not ready. " + "; ".join(caps.get("notes") or ["Install pymupdf."]),
                "capabilities": caps,
            }

        want_ai = self._coerce_bool(use_ai, default=False)
        if want_ai and not caps.get("ai_available"):
            notes = "; ".join(caps.get("notes") or [])
            return {
                "ok": False,
                "message": (
                    "You selected AI mode, but Ollama is not available. "
                    f"{notes or 'Start the Ollama app and ensure qwen3:14b is installed.'} "
                    "Or choose Fast mode."
                ),
                "capabilities": caps,
                "want_ai": True,
                "used_ai": False,
            }

        input_path = file_picker.pick_open_file(
            title="Select PDF to redact",
            file_types=("PDF files (*.pdf)", "All files (*.*)"),
        )
        if not input_path:
            return {"ok": False, "cancelled": True, "message": "No file selected."}

        src = Path(input_path)
        default_name = f"{src.stem}_REDACTED.pdf"
        # Only skip AI when the user explicitly chose Fast mode
        skip_ai = not want_ai
        tmp_dir = Path(tempfile.mkdtemp(prefix="rp_redact_"))
        tmp_out = tmp_dir / default_name

        self._redact_set(
            state="running",
            percent=0,
            page=0,
            pages=0,
            phase="start",
            message=(
                "Starting local AI redaction (127.0.0.1 only, no internet)…"
                if want_ai
                else "Starting local redaction (no AI, no internet)…"
            ),
            ok=None,
            result_message="",
            used_ai=want_ai,
            want_ai=want_ai,
            input_path=str(src),
            tmp_path=str(tmp_out),
            tmp_dir=str(tmp_dir),
            default_name=default_name,
        )

        def _on_progress(info: dict[str, Any]) -> None:
            self._redact_set(
                percent=info.get("percent", 0),
                page=info.get("page", 0),
                pages=info.get("pages", 0),
                phase=info.get("phase", ""),
                message=info.get("message", ""),
                elapsed_seconds=info.get("elapsed_seconds", 0),
                eta_seconds=info.get("eta_seconds"),
                eta_label=info.get("eta_label") or "",
            )

        def _worker() -> None:
            try:
                result = redact_pdf(
                    src,
                    tmp_out,
                    custom_rules=str(custom_rules or ""),
                    skip_ai=skip_ai,
                    progress=_on_progress,
                )
                if result.get("ok"):
                    # Fail loudly if AI was requested but not actually used
                    actually_ai = bool(result.get("used_ai"))
                    if want_ai and not actually_ai:
                        self._redact_set(
                            state="error",
                            ok=False,
                            result_message=(
                                "AI mode was requested but the redactor ran without AI. "
                                "Check that Ollama is running and the model is loaded."
                            ),
                            message="AI mode failed",
                            used_ai=False,
                        )
                        try:
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                        except Exception:
                            pass
                        return
                    self._redact_set(
                        state="done",
                        percent=100,
                        ok=True,
                        result_message=str(result.get("message", "Done.")),
                        used_ai=actually_ai,
                        pages=int(result.get("pages") or 0),
                        message="Ready to save",
                    )
                else:
                    self._redact_set(
                        state="error",
                        ok=False,
                        result_message=str(result.get("message", "Redaction failed.")),
                        message=str(result.get("message", "Failed")),
                    )
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass
            except Exception as exc:
                self._redact_set(
                    state="error",
                    ok=False,
                    result_message=str(exc),
                    message=str(exc),
                )
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

        threading.Thread(target=_worker, name="pdf-redact", daemon=True).start()
        return {
            "ok": True,
            "started": True,
            "message": "AI redaction started." if want_ai else "Fast redaction started.",
            "input_name": src.name,
            "used_ai": want_ai,
            "want_ai": want_ai,
            "mode": "ai" if want_ai else "fast",
        }

    def pdf_redact_save(self) -> dict[str, Any]:
        """After status.state == done, pick save location and copy the redacted file."""
        with self._redact_lock:
            job = dict(self._redact_job)

        if job.get("state") != "done" or not job.get("tmp_path"):
            return {
                "ok": False,
                "message": "Nothing ready to save. Run redaction first.",
            }

        tmp_path = Path(str(job["tmp_path"]))
        tmp_dir = Path(str(job.get("tmp_dir") or tmp_path.parent))
        default_name = str(job.get("default_name") or "redacted.pdf")

        if not tmp_path.is_file():
            self._redact_set(state="idle")
            return {"ok": False, "message": "Temporary redacted file is missing."}

        save_path = file_picker.pick_save_file(
            title="Save redacted PDF",
            file_types=("PDF files (*.pdf)", "All files (*.*)"),
            save_filename=default_name,
        )
        if not save_path:
            return {
                "ok": False,
                "cancelled": True,
                "message": "Save cancelled. You can try Save again, or discard.",
            }

        dest = Path(save_path)
        if dest.suffix.lower() != ".pdf":
            dest = dest.with_suffix(".pdf")
        try:
            shutil.copy2(tmp_path, dest)
        except Exception as exc:
            return {"ok": False, "message": f"Could not save: {exc}"}
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self._redact_set(state="idle", tmp_path="", tmp_dir="", percent=0, message="")

        return {
            "ok": True,
            "message": (
                f"{job.get('result_message', 'Done.')}\nSaved to:\n{dest}\n\n"
                "Always review the redacted PDF before sharing."
            ),
            "output_path": str(dest),
        }

    def pdf_redact_discard(self) -> dict[str, Any]:
        """Discard a completed temp redaction without saving."""
        with self._redact_lock:
            job = dict(self._redact_job)
        tmp_dir = job.get("tmp_dir")
        if tmp_dir:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
        self._redact_set(
            state="idle",
            tmp_path="",
            tmp_dir="",
            percent=0,
            message="",
            ok=None,
        )
        return {"ok": True, "message": "Discarded."}

    def pdf_redact_interactive(self, use_ai: int = 0, custom_rules: str = "") -> dict[str, Any]:
        """
        Legacy one-shot flow (blocks). Prefer pdf_redact_start + status + save.
        Kept for regression smoke tests. use_ai: 1 = AI, 0 = fast.
        """
        from providers.pdf_redactor import capabilities, redact_pdf

        caps = capabilities()
        if not caps.get("ready"):
            return {
                "ok": False,
                "message": "PDF redaction is not ready. " + "; ".join(caps.get("notes") or ["Install pymupdf."]),
            }

        want_ai = self._coerce_bool(use_ai, default=False)
        if want_ai and not caps.get("ai_available"):
            return {
                "ok": False,
                "message": "AI mode requested but Ollama is not available. Use Fast mode or start Ollama.",
            }

        input_path = file_picker.pick_open_file(
            title="Select PDF to redact",
            file_types=("PDF files (*.pdf)", "All files (*.*)"),
        )
        if not input_path:
            return {"ok": False, "cancelled": True, "message": "No file selected."}

        src = Path(input_path)
        default_name = f"{src.stem}_REDACTED.pdf"
        skip_ai = not want_ai
        tmp_dir = Path(tempfile.mkdtemp(prefix="rp_redact_"))
        tmp_out = tmp_dir / default_name
        try:
            result = redact_pdf(
                src,
                tmp_out,
                custom_rules=str(custom_rules or ""),
                skip_ai=skip_ai,
            )
            if not result.get("ok"):
                return result
            save_path = file_picker.pick_save_file(
                title="Save redacted PDF",
                file_types=("PDF files (*.pdf)", "All files (*.*)"),
                save_filename=default_name,
            )
            if not save_path:
                return {
                    "ok": False,
                    "cancelled": True,
                    "message": "Redaction finished but save was cancelled.",
                }
            dest = Path(save_path)
            if dest.suffix.lower() != ".pdf":
                dest = dest.with_suffix(".pdf")
            shutil.copy2(tmp_out, dest)
            result["output_path"] = str(dest)
            result["message"] = (
                f"{result.get('message', 'Done.')}\nSaved to:\n{dest}\n\n"
                "Always review the redacted PDF before sharing."
            )
            return result
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def get_live_data_panel(self) -> dict[str, Any]:
        return self._session.get_live_data_panel()

    def connect_plaid(self) -> dict[str, Any]:
        return plaid_web.start_plaid_connect()

    def list_plaid_institutions(self) -> dict[str, Any]:
        """Live Plaid links currently in Keychain (and known account names)."""
        from app.core.presentation.live_data_ops import build_plaid_connections_payload

        payload = build_plaid_connections_payload()
        return {"ok": True, **payload}

    def import_document(self) -> dict[str, Any]:
        path = file_picker.pick_open_file(
            title="Import document",
            file_types=(
                "JSON files (*.json)",
                "PDF files (*.pdf)",
                "Images (*.png;*.jpg;*.jpeg)",
                "All files (*.*)",
            ),
        )
        if not path:
            return {"ok": False, "cancelled": True, "message": "Import cancelled."}
        return self._session.ingest_document_file(path)

    def apply_document_import(
        self,
        rows: list[dict[str, Any]],
        document_type: str = "",
        document_label: str = "",
        library_entry_id: str | None = None,
    ) -> dict[str, Any]:
        return self._session.apply_document_import(
            rows,
            document_type=document_type,
            document_label=document_label,
            library_entry_id=library_entry_id,
        )

    def import_balances(self) -> dict[str, Any]:
        path = file_picker.pick_open_file(
            file_types=("CSV files (*.csv)", "All files (*.*)"),
        )
        if not path:
            return {"ok": False, "cancelled": True, "message": "Import cancelled."}
        parsed = self._session.parse_import_csv(path)
        if not parsed.get("ok"):
            return parsed
        return {"ok": True, "rows": parsed.get("rows", []), "message": "Review import rows below."}

    def apply_import_balances(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.apply_import_csv(rows)

    def refresh_live_balances(self) -> dict[str, Any]:
        return self._session.refresh_balances_review(mode="refresh")

    def refresh_balances_manage(self) -> dict[str, Any]:
        return self._session.refresh_balances_review(mode="manage")

    def disconnect_plaid_item(self, item_id: str) -> dict[str, Any]:
        return self._session.disconnect_plaid_item(item_id)

    def apply_balances_review(self, rows: list[dict[str, Any]], apply_assets: bool = True) -> dict[str, Any]:
        return self._session.apply_balances_review(rows, apply_assets=apply_assets)

    def refresh_expenses_review(self) -> dict[str, Any]:
        return self._session.refresh_expenses_review()

    def apply_expenses_review(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.apply_expenses_review(rows)

    def refresh_contributions_review(self) -> dict[str, Any]:
        return self._session.refresh_contributions_review()

    def apply_contributions_review(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.apply_contributions_review(rows)

    def refresh_market_data(self) -> dict[str, Any]:
        return self._session.refresh_market_data_web()

    def prepare_run_plan(self) -> dict[str, Any]:
        return self._session.prepare_run_plan()

    def get_spending_builder(self, person: str = "self") -> dict[str, Any]:
        return self._session.get_spending_builder(person)

    def apply_spending_builder(
        self,
        person: str,
        amounts: dict[str, Any],
        mortgage_payoff_year: str | None = None,
    ) -> dict[str, Any]:
        return self._session.apply_spending_builder(
            person,
            amounts or {},
            mortgage_payoff_year=mortgage_payoff_year,
        )

    def suggest_spending_averages(self, person: str = "self") -> dict[str, Any]:
        return self._session.suggest_spending_averages(person)

    def suggest_expense_phase_pcts(self, person: str = "self") -> dict[str, Any]:
        return self._session.suggest_expense_phase_pcts(person)

    def ai_categorize_expenses(
        self,
        rows: list[dict[str, Any]] | None = None,
        backend: str = "local",
    ) -> dict[str, Any]:
        return self._session.ai_categorize_expenses(rows, backend=backend)

    def fill_spending_from_expenses_ai(
        self,
        person: str = "self",
        backend: str = "local",
    ) -> dict[str, Any]:
        return self._session.fill_spending_from_expenses_ai(person, backend=backend)

    def ai_backend_status(self) -> dict[str, Any]:
        return self._session.ai_backend_status()

    def run_plan(self, confirm_cpa: bool = True) -> dict[str, Any]:
        """JS must pass confirm_cpa positionally — pywebview does not support keyword-only args."""
        try:
            return self._session.run_plan_web(confirm_cpa=bool(confirm_cpa))
        except Exception as exc:
            return {"ok": False, "message": f"Run plan failed: {exc}"}

    def get_simulation_results(self) -> dict[str, Any]:
        results = self._session.get_last_results()
        if results is None:
            return {"ok": False, "message": "No simulation results yet."}
        return {"ok": True, "results": results}

    def apply_what_if(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        return self._session.apply_what_if(changes)

    def get_llm_settings(self) -> dict[str, Any]:
        return self._session.get_llm_settings()

    def save_llm_settings(self, provider: str, model: str, api_key: str = "") -> dict[str, Any]:
        return self._session.save_llm_settings(provider, model, api_key or None)

    def pick_export_path(self) -> dict[str, Any]:
        path = file_picker.pick_save_file(file_types=("CSV files (*.csv)", "All files (*.*)"))
        if not path:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "path": path}

    def export_assumptions_csv(self, path: str) -> dict[str, Any]:
        from config.assumptions_report import build_assumptions_dataframe
        from pathlib import Path

        try:
            df = build_assumptions_dataframe(self._session.snapshot.to_simulation_params())
            out = Path(path)
            if out.suffix.lower() != ".csv":
                out = out.with_suffix(".csv")
            df.to_csv(out, index=False)
            return {"ok": True, "message": f"Exported to {out}"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def pick_tax_return_path(self) -> dict[str, Any]:
        path = file_picker.pick_open_file(file_types=("JSON files (*.json)", "All files (*.*)"))
        if not path:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "path": path}

    def validate_tax_return(self, path: str) -> dict[str, Any]:
        from pathlib import Path

        from app.core.controllers.simulation_controller import SimulationController

        result = SimulationController().validate_tax_return(Path(path))
        return {
            "ok": result.ok,
            "title": result.title,
            "message": result.message,
        }
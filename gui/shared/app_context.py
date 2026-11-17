"""Shared application state and operations for expert + guided modes."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
from datetime import date

import tkinter as tk
from tkinter import filedialog, messagebox

from adviser.advisor_engine import WhatIfChange
from app.core.controllers.simulation_controller import SimulationController, SimulationRunRequest
from app.core.planning_snapshot import PlanningSnapshot
from config.assumptions_report import build_assumptions_dataframe
from config.field_values import format_field_value
from config.model_fields import FIELDS_BY_KEY, ModelField, fields_with_data_source, field_with_model_role
from config.policy.live import ensure_active_policy
from config.policy.tables import active_policy_summary
from gui.legacy import (
    AssumptionResearchReviewDialog,
    ContributionReviewDialog,
    CsvImportReviewDialog,
    ExpenseReviewDialog,
    LLMSettingsDialog,
    LiveDataReviewDialog,
    ResultsWindow,
    _count_unassigned_line_items,
    _format_balance,
)
from gui.shared.field_binder import FieldBinder
from gui.shared.simulation_progress import SimulationProgressDialog
from providers.assumption_research import AssumptionResearchContext, research_assumption
from providers.plaid_provider import HoldingSnapshot
from security.keychain import list_item_ids
from security.link_server import open_in_chrome, stop_stale_servers
from security.llm_keychain import has_api_key
from services.data_service import DataService, ExpenseSnapshot, MarketSnapshot
from settings import LLMSettings, PROJECT_ROOT, get_settings, link_server_info_path, load_llm_settings, save_llm_settings


class AppContext:
    """Platform-specific shell operations; both presentation modes share this."""

    def __init__(self, root: tk.Tk, snapshot: PlanningSnapshot) -> None:
        self.root = root
        self.snapshot = snapshot
        self.binder = FieldBinder(snapshot)
        self.simulation = SimulationController()
        self._data_service: DataService | None = None
        self._link_in_progress = False
        self.plaid_ready = get_settings().plaid_configured
        self.llm_settings = load_llm_settings()
        self.results_window: ResultsWindow | None = None
        self._sim_thread: threading.Thread | None = None
        self._sim_cancel: threading.Event | None = None
        self._sim_progress: SimulationProgressDialog | None = None
        ensure_active_policy()

    @property
    def policy_label(self) -> str:
        return active_policy_summary()["label"]

    def data_service(self) -> DataService:
        if self._data_service is None:
            self._data_service = DataService()
        return self._data_service

    def sync_snapshot_from_binder(self) -> None:
        self.binder.pull_all()
        self.snapshot.household_mode = self.snapshot.household_mode
        self.snapshot.simulation.planning_precision = self.snapshot.simulation.planning_precision

    def apply_market_snapshot(self, market: MarketSnapshot) -> None:
        for field in fields_with_data_source("market", scope="shared"):
            attr = field.market_snapshot_attribute()
            if not attr:
                continue
            market_field = getattr(market, attr, None)
            if market_field is None:
                continue
            self.binder.set_value(field, market_field.value, source=market_field.display_label())

    def market_status_text(self, market: MarketSnapshot) -> str:
        parts = ["Market data updated."]
        if market.is_stale():
            parts.append("Warning: cached data is older than 24 hours.")
        if market.errors:
            parts.append("Issues: " + "; ".join(market.errors))
        return " ".join(parts)

    def live_status_text(self) -> str:
        if not self.plaid_ready:
            return "Plaid not configured."
        items = list_item_ids()
        if not items:
            return "No institutions connected — click Connect Accounts to link via Plaid."
        cached = self.data_service().load_cached_snapshot()
        expense_cached = self.data_service().load_cached_expense_snapshot()
        if cached and cached.as_of:
            status = (
                f"Last refresh: {cached.as_of}  |  "
                f"{len(items)} institution(s)  |  "
                f"self {_format_balance(cached.owner_totals.get('self', 0))}  "
                f"wife {_format_balance(cached.owner_totals.get('wife', 0))}"
            )
            if expense_cached and expense_cached.as_of:
                status += (
                    f"  |  expenses {_format_balance(expense_cached.analysis.annual_household)}"
                    f" ({expense_cached.as_of[:10]})"
                )
            return status
        if expense_cached and expense_cached.as_of:
            return (
                f"{len(items)} institution(s) connected  |  "
                f"expenses {_format_balance(expense_cached.analysis.annual_household)}"
                f" ({expense_cached.as_of[:10]})"
            )
        return f"{len(items)} institution(s) connected — click Refresh Live Data to fetch balances."

    def apply_owner_totals(self, totals: dict[str, float]) -> None:
        asset_field = next(
            (f for f in fields_with_data_source("plaid", scope="person") if f.key == "initial_assets"),
            None,
        )
        if asset_field is None:
            return
        for person in ("self", "wife"):
            value = totals.get(person, 0.0)
            self.binder.set_value(asset_field, value, person=person, source="Plaid balances")

    def apply_owner_expenses(self, totals: dict[str, float], *, snapshot: ExpenseSnapshot | None = None) -> None:
        expense_field = field_with_model_role("base_expense")
        if expense_field is None:
            return
        source = (
            f"Plaid transactions ({snapshot.as_of[:10]})"
            if snapshot and snapshot.as_of
            else "Plaid transactions"
        )
        for person in ("self", "wife"):
            value = totals.get(person, 0.0)
            if value:
                self.binder.set_value(expense_field, value, person=person, source=source)
                self.binder.snapshot.persons[person]["_expense_review_base"] = (
                    self.binder.snapshot.get_raw(expense_field, person=person)
                )
                self.binder.snapshot.base_excludes_property_tax = True
            else:
                self.binder.set_source(person, expense_field.key, "Manual")

    def apply_owner_contributions(self, totals: dict[str, float], *, as_of: str = "") -> None:
        field = FIELDS_BY_KEY.get("annual_contributions")
        if field is None:
            return
        source = f"Plaid deposits ({as_of[:10]})" if as_of else "Plaid deposits"
        for person in ("self", "wife"):
            value = totals.get(person, 0.0)
            if value:
                self.binder.set_value(field, value, person=person, source=source)

    def apply_what_if(self, changes: list[WhatIfChange]) -> None:
        for change in changes:
            field = FIELDS_BY_KEY.get(change.field_key)
            if field is None:
                continue
            self.binder.set_value(field, change.new_value, person=change.person, source="Adviser what-if")
        messagebox.showinfo(
            "What-if Applied",
            "Suggested values were copied to the input form.\n"
            "Run Simulation again to see the updated projection.",
            parent=self.root,
        )

    def fetch_holdings(self) -> list[HoldingSnapshot]:
        if not self.plaid_ready:
            return []
        try:
            return self.data_service().get_portfolio_holdings()
        except Exception:
            return []

    def close_results_window(self) -> None:
        if self.results_window and self.results_window.winfo_exists():
            self.results_window._on_close()

    def run_simulation(self, *, confirm_cpa_mc: bool = True) -> None:
        if self._sim_thread is not None and self._sim_thread.is_alive():
            messagebox.showinfo(
                "Simulation Running",
                "A simulation is already in progress.",
                parent=self.root,
            )
            return

        self.binder.pull_all()
        if self.simulation.needs_cpa_mc_confirmation(self.snapshot) and not confirm_cpa_mc:
            if not messagebox.askyesno(
                "CPA Precision + Monte Carlo",
                "CPA Precision runs slower per path. Consider 1,000 paths or fewer.\n\n"
                f"Continue with {self.snapshot.simulation.num_paths:,} paths?",
                parent=self.root,
            ):
                return
            confirm_cpa_mc = True

        warnings = self.simulation.collect_warnings(self.snapshot)
        if warnings:
            messagebox.showwarning(
                "Planning Approximations",
                "\n\n".join(warnings),
                parent=self.root,
            )

        request = SimulationRunRequest(snapshot=self.snapshot, confirm_cpa_mc_paths=confirm_cpa_mc)
        cancel_event = threading.Event()
        progress = SimulationProgressDialog(self.root)
        progress.bind_cancel(cancel_event.set)
        self._sim_cancel = cancel_event
        self._sim_progress = progress

        def on_progress(completed: int, total: int) -> None:
            self.root.after(0, lambda: progress.update_progress(completed, total))

        def worker() -> None:
            result = self.simulation.run(
                request,
                cancel_event=cancel_event,
                on_progress=on_progress,
            )
            self.root.after(0, lambda: self._finish_simulation(result, progress))

        self._sim_thread = threading.Thread(target=worker, daemon=True)
        self._sim_thread.start()

    def _finish_simulation(self, result, progress: SimulationProgressDialog) -> None:
        progress.close()
        self._sim_thread = None
        self._sim_cancel = None
        self._sim_progress = None

        if not result.ok:
            if result.title == "Simulation Cancelled":
                return
            fn = messagebox.showerror if result.level == "error" else messagebox.showwarning
            fn(result.title, result.message, parent=self.root)
            return

        payload = result.payload
        try:
            holdings = self.fetch_holdings()
            self.close_results_window()
            self.results_window = ResultsWindow(
                self.root,
                payload.output,
                params=payload.params,
                risk_tolerance=self.snapshot.simulation.risk_tolerance,
                holdings=holdings or None,
                on_apply_what_if=self.apply_what_if,
            )
            self.results_window.focus()
        except Exception as exc:
            messagebox.showerror("Results Error", str(exc), parent=self.root)

    def validate_tax_return(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Select tax return JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        result = self.simulation.validate_tax_return(path)
        if result.ok:
            messagebox.showinfo(result.title, result.message, parent=self.root)
        else:
            messagebox.showerror(result.title, result.message, parent=self.root)

    def export_assumptions_csv(self) -> None:
        self.binder.pull_all()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Export Assumptions",
        )
        if not path:
            return
        df = build_assumptions_dataframe(self.snapshot.to_simulation_params())
        df.to_csv(path, index=False)
        messagebox.showinfo("Exported", f"Assumptions saved to:\n{path}", parent=self.root)

    def open_llm_settings(self) -> None:
        dialog = LLMSettingsDialog(self.root, self.llm_settings)
        self.root.wait_window(dialog)
        if dialog.saved_settings is not None:
            self.llm_settings = dialog.saved_settings
            save_llm_settings(self.llm_settings)

    def llm_status_text(self) -> str:
        if not self.llm_settings.configured:
            return "LLM not configured"
        key = "key set" if has_api_key(self.llm_settings.provider) else "no API key"
        return f"{self.llm_settings.provider} / {self.llm_settings.model or 'default'} ({key})"

    def research_field(self, field: ModelField, person: str | None = None) -> None:
        if not field.supports_llm_research():
            return
        if not self.llm_settings.configured or not has_api_key(self.llm_settings.provider):
            messagebox.showwarning("LLM Required", "Configure LLM Settings and API key first.", parent=self.root)
            return
        context_person = person or "self"
        ages = self.snapshot.persons.get(context_person, {})
        try:
            current_age = int(ages.get("current_age") or 0) or None
            retirement_age = int(ages.get("retirement_age") or 0) or None
        except ValueError:
            current_age = retirement_age = None
        context = AssumptionResearchContext(
            field_key=field.key,
            person=person,
            current_age=current_age,
            retirement_age=retirement_age,
            state=self.snapshot.assumption_state or None,
            planning_year=date.today().year,
            fred_api_key=get_settings().fred_api_key,
        )
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            suggestion, _from_cache = research_assumption(
                context,
                provider=self.llm_settings.provider,
                model=self.llm_settings.model,
            )
        except Exception as exc:
            messagebox.showerror("Research Error", str(exc), parent=self.root)
            return
        finally:
            self.root.config(cursor="")

        dialog = AssumptionResearchReviewDialog(self.root, suggestion, field)
        self.root.wait_window(dialog)
        if dialog.applied:
            self.binder.set_value(
                field,
                suggestion.suggested_annual_usd,
                person=person,
                source=suggestion.source_label(),
            )

    # --- Plaid connect (from legacy) ---

    def connect_accounts(self, *, on_status) -> None:
        if self._link_in_progress:
            return
        self._link_in_progress = True
        on_status("Starting Plaid Link server…")
        try:
            stop_stale_servers()
            subprocess.Popen(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "setup_plaid.py"), "link", "--no-browser"],
                cwd=str(PROJECT_ROOT),
                start_new_session=True,
            )
            self._poll_link_server(on_status)
        except Exception as exc:
            self._link_in_progress = False
            messagebox.showerror("Connect Error", str(exc), parent=self.root)

    def _poll_link_server(self, on_status, attempt: int = 0) -> None:
        if attempt >= 75:
            self._link_in_progress = False
            messagebox.showerror("Connect Error", "Plaid Link server did not become ready.", parent=self.root)
            on_status(self.live_status_text())
            return
        info_path = link_server_info_path()
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                url = info["url"]
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        open_in_chrome(url)
                        on_status(f"Plaid Link open in Chrome ({url})")
                        self.root.after(60_000, lambda: setattr(self, "_link_in_progress", False))
                        return
            except Exception:
                pass
        self.root.after(200, lambda: self._poll_link_server(on_status, attempt + 1))

    def manage_account_mappings(self, *, on_status) -> None:
        if not list_item_ids():
            messagebox.showwarning("No accounts", "Connect an institution first.", parent=self.root)
            return
        service = self.data_service()
        snapshot = service.snapshot_for_account_mapping()
        dialog = LiveDataReviewDialog(self.root, snapshot, service, mode="manage")
        self.root.wait_window(dialog)
        if not dialog.applied:
            return
        if dialog.apply_assets:
            self.apply_owner_totals(dialog.get_owner_totals())
        on_status(self.live_status_text())
        messagebox.showinfo("Mappings Saved", "Account mappings saved.", parent=self.root)

    def refresh_live_data(self, *, on_status) -> None:
        if not list_item_ids():
            messagebox.showwarning("No accounts", "Connect an institution first.", parent=self.root)
            return
        service = self.data_service()
        snapshot = service.refresh_live_data()
        if snapshot.errors and not snapshot.accounts:
            messagebox.showerror("Refresh Error", "\n".join(snapshot.errors), parent=self.root)
            return
        dialog = LiveDataReviewDialog(self.root, snapshot, service, mode="refresh")
        self.root.wait_window(dialog)
        if dialog.applied:
            self.apply_owner_totals(dialog.get_owner_totals())
            messagebox.showinfo("Applied", "Initial Assets updated from Plaid.", parent=self.root)
        on_status(self.live_status_text())

    def refresh_expenses(self, *, on_status) -> None:
        service = self.data_service()
        snapshot = service.refresh_expenses()
        if snapshot.analysis.transactions_used == 0:
            messagebox.showwarning("No transactions", "No expense transactions found yet.", parent=self.root)
            return
        dialog = ExpenseReviewDialog(self.root, snapshot, self.snapshot.household_mode)
        self.root.wait_window(dialog)
        if dialog.applied:
            totals = dialog.get_owner_expense_totals()
            service.save_expense_snapshot(snapshot)
            self.apply_owner_expenses(totals, snapshot=snapshot)
            messagebox.showinfo("Applied", "Base Expenses updated.", parent=self.root)
        on_status(self.live_status_text())

    def refresh_contributions(self, *, on_status) -> None:
        service = self.data_service()
        snapshot = service.refresh_contributions()
        if snapshot.analysis.transactions_used == 0:
            messagebox.showwarning("No contributions", "No contribution deposits found.", parent=self.root)
            return
        dialog = ContributionReviewDialog(self.root, snapshot, self.snapshot.household_mode)
        self.root.wait_window(dialog)
        if dialog.applied:
            totals = dialog.get_owner_contribution_totals()
            service.save_contribution_snapshot(snapshot)
            self.apply_owner_contributions(totals, as_of=snapshot.as_of)
            messagebox.showinfo("Applied", "Annual Contributions updated.", parent=self.root)
        on_status(self.live_status_text())

    def refresh_market_data(self, *, on_status) -> None:
        snapshot = self.data_service().refresh_market_data()
        self.apply_market_snapshot(snapshot)
        on_status(self.market_status_text(snapshot))

    def import_csv_balances(self, *, on_status) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import balance CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        rows = self.data_service().parse_balance_csv_file(path)
        dialog = CsvImportReviewDialog(self.root, rows)
        self.root.wait_window(dialog)
        if dialog.applied:
            totals = dialog.get_owner_totals()
            self.apply_owner_totals(totals)
            messagebox.showinfo("Applied", "Initial Assets updated from CSV.", parent=self.root)
        on_status(self.live_status_text())
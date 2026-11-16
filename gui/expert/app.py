"""Expert mode — Liquid Glass structured editor."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from app.core.planning_snapshot import PlanningSnapshot
from config.model_fields import FIELDS_BY_KEY, field_choice_values

from gui.design_system.components.glass_input import glass_combobox
from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.components.pill_controls import PillRadioGroup
from gui.design_system.components.section_rail import SectionRail
from gui.design_system.tokens import COLORS, SPACING
from gui.expert.sections import EXPERT_NAV
from gui.legacy import _create_scrollable_frame
from gui.shared.app_context import AppContext
from gui.shared.catalog_sections import assumption_sections
from gui.shared.field_panel import render_grouped_sections
from gui.shared.field_search import filter_assumption_sections
from gui.shared.live_data_panel import build_live_data_panel
from gui.shared.llm_research_panel import build_llm_research_panel

if TYPE_CHECKING:
    from gui.app import RetirementApp


class ExpertApp:
    SECTION_IDS = tuple(nav_id for nav_id, _ in EXPERT_NAV)

    def __init__(self, parent: tk.Widget, snapshot: PlanningSnapshot, *, shell: RetirementApp) -> None:
        self.parent = parent
        self.root = parent.winfo_toplevel()
        self.snapshot = snapshot
        self.shell = shell
        self.ctx = AppContext(self.root, snapshot)
        self._section_frames: dict[str, ttk.Frame] = {}

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self._host = tk.Frame(parent, bg=COLORS.bg_base, highlightthickness=0)
        self._host.grid(row=0, column=0, sticky="nsew")
        self._host.columnconfigure(1, weight=1)
        self._host.rowconfigure(0, weight=1)

        self._rail = SectionRail(
            self._host,
            sections=EXPERT_NAV,
            active_id="assumptions",
            on_select=self.show_section,
        )
        self._rail.grid(row=0, column=0, sticky="ns", padx=(0, SPACING.sm), pady=0)

        self.content = tk.Frame(self._host, bg=COLORS.bg_base, highlightthickness=0)
        self.content.grid(row=0, column=1, sticky="nsew", padx=(SPACING.sm, 0), pady=0)
        self.content.rowconfigure(1, weight=1)
        self.content.columnconfigure(0, weight=1)

        self._headline_var = tk.StringVar(value="Assumptions")
        self._subtitle_var = tk.StringVar(value="All planning fields — grouped by topic")
        self._build_header()

        _canvas, scroll = _create_scrollable_frame(self.content)
        scroll.columnconfigure(0, weight=1)
        scroll.grid(row=1, column=0, sticky="nsew")

        self._section_frames["setup"] = self._build_setup(scroll)
        self._section_frames["assumptions"] = self._build_assumptions(scroll)
        self._section_frames["llm"] = self._build_llm(scroll)
        self._section_frames["live_data"] = self._build_live_data(scroll)
        self._section_frames["simulate"] = self._build_simulate(scroll)
        self._section_frames["settings"] = self._build_settings(scroll)

        self.shell.set_status_bar_visible(True)
        self.show_section("assumptions")
        self._load_cached_market()
        self.shell.update_shell_status(headline="Expert editor", detail="Assumptions")
        self.shell.set_run_plan_enabled(True)

    def _build_header(self) -> None:
        head = tk.Frame(self.content, bg=COLORS.bg_base, highlightthickness=0)
        head.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.md))
        left = tk.Frame(head, bg=COLORS.bg_base, highlightthickness=0)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left,
            textvariable=self._headline_var,
            bg=COLORS.bg_base,
            fg=COLORS.text_primary,
            font=("SF Pro Text", 28, "bold"),
        ).pack(anchor="w", padx=(SPACING.sm, 0))
        tk.Label(
            left,
            textvariable=self._subtitle_var,
            bg=COLORS.bg_base,
            fg=COLORS.text_muted,
            font=("SF Pro Text", 12),
        ).pack(anchor="w", padx=(SPACING.sm, 0), pady=(SPACING.xs, 0))
        ttk.Button(head, text="Jump to Guided →", style="Ghost.TButton", command=self._jump_guided).pack(side="right")

    def _jump_guided(self) -> None:
        self.shell.switch_mode("guided")

    def _on_nav_change(self) -> None:
        pass

    def show_section(self, section_id: str) -> None:
        self._rail.set_active(section_id)
        for sid, frame in self._section_frames.items():
            if sid == section_id:
                frame.grid(row=0, column=0, sticky="nw")
            else:
                frame.grid_remove()
        label = dict(EXPERT_NAV).get(section_id, section_id)
        self._headline_var.set(label)
        self._subtitle_var.set(self._subtitle_for(section_id))
        self.shell.update_shell_status(headline="Expert editor", detail=label)

    def _subtitle_for(self, section_id: str) -> str:
        subtitles = {
            "setup": "Mode and tax jurisdiction for the whole plan",
            "assumptions": "All planning fields — grouped by topic",
            "llm": "Batch-estimate empty fields with AI",
            "live_data": "Connect accounts and refresh assumptions from real data",
            "simulate": "Run options and Monte Carlo settings",
            "settings": "Integrations and app configuration",
        }
        return subtitles.get(section_id, "")

    def _build_setup(self, parent: ttk.Frame) -> tk.Frame:
        frame = GlassPanel(parent, strong=True)
        body = frame.body

        ttk.Label(body, text="Household mode", style="Question.TLabel").pack(anchor="w", pady=(0, SPACING.sm))
        self.mode_var = tk.StringVar(value=self.snapshot.household_mode)
        PillRadioGroup(
            body,
            options=(("joint", "Joint"), ("separate", "Separate")),
            variable=self.mode_var,
            bg=COLORS.glass_strong,
        ).pack(anchor="w", pady=(0, SPACING.lg))
        self.mode_var.trace_add("write", lambda *_: setattr(self.snapshot, "household_mode", self.mode_var.get()))

        ttk.Label(body, text="State of residence", style="Question.TLabel").pack(anchor="w", pady=(0, SPACING.sm))
        state_field = FIELDS_BY_KEY["state_of_residence"]
        self.state_combo = glass_combobox(
            body,
            values=list(field_choice_values(state_field) or ("CA",)),
            width=8,
        )
        self.state_combo.set(self.snapshot.assumption_state)
        self.state_combo.master.pack(anchor="w", pady=(0, SPACING.md))
        self.state_combo.bind("<<ComboboxSelected>>", self._on_state_change)
        ttk.Label(body, text=f"Tax tables: {self.ctx.policy_label}", style="Caption.TLabel").pack(anchor="w")
        return frame

    def _on_state_change(self, _event=None) -> None:
        self.snapshot.assumption_state = self.state_combo.get()
        raw = self.snapshot.shared.get("state_of_residence", "CA")
        self.snapshot.shared["state_of_residence"] = self.state_combo.get()
        if raw != self.state_combo.get() and "state_of_residence" in self.ctx.binder.shared_widgets:
            from gui.legacy import _widget_set_text

            _widget_set_text(self.ctx.binder.shared_widgets["state_of_residence"], self.state_combo.get())

    def _build_assumptions(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, SPACING.sm))
        ttk.Label(toolbar, text="Person:", style="Secondary.TLabel").pack(side="left")
        self.person_var = tk.StringVar(value="shared")
        for val, label in (("shared", "Shared"), ("self", "Self"), ("wife", "Wife")):
            ttk.Radiobutton(
                toolbar,
                text=label,
                variable=self.person_var,
                value=val,
                command=self._refresh_assumption_panel,
            ).pack(side="left", padx=SPACING.sm)
        ttk.Label(toolbar, text="Search:", style="Secondary.TLabel").pack(side="left", padx=(SPACING.md, 0))
        self._search_var = tk.StringVar()
        search_entry = ttk.Entry(toolbar, textvariable=self._search_var, width=24)
        search_entry.pack(side="left", padx=SPACING.xs)
        self._search_var.trace_add("write", lambda *_: self._refresh_assumption_panel())
        self.assumption_panel = ttk.Frame(frame)
        self.assumption_panel.pack(fill="both", expand=True)
        self._refresh_assumption_panel()
        return frame

    def _refresh_assumption_panel(self) -> None:
        self.ctx.binder.prune_destroyed_widgets()
        for child in self.assumption_panel.winfo_children():
            child.destroy()
        person = self.person_var.get()
        sections = assumption_sections(scope="shared" if person == "shared" else person)
        sections = filter_assumption_sections(sections, self._search_var.get())
        render_person = None if person == "shared" else person
        render_grouped_sections(self.assumption_panel, sections, self.ctx.binder, person=render_person)

    def _build_llm(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        build_llm_research_panel(frame, self.ctx, title="AI Assistant")
        return frame

    def _build_live_data(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        build_live_data_panel(frame, self.ctx, title="Live Data")
        return frame

    def _build_simulate(self, parent: ttk.Frame) -> tk.Frame:
        frame = GlassPanel(parent, strong=True)
        body = frame.body
        sim = self.snapshot.simulation
        ttk.Label(body, text=f"Tax tables: {self.ctx.policy_label}", style="Caption.TLabel").pack(anchor="w", pady=(0, SPACING.sm))

        ttk.Label(body, text="Simulation mode", style="Question.TLabel").pack(anchor="w", pady=(0, SPACING.sm))
        self.sim_mode_var = tk.StringVar(value=sim.simulation_mode)
        PillRadioGroup(
            body,
            options=(
                ("deterministic", "Deterministic"),
                ("monte_carlo", "Monte Carlo"),
                ("both", "Both"),
            ),
            variable=self.sim_mode_var,
            bg=COLORS.glass_strong,
        ).pack(anchor="w", pady=(0, SPACING.md))
        self.sim_mode_var.trace_add("write", self._sync_sim_options)

        ttk.Label(body, text="Planning precision", style="Question.TLabel").pack(anchor="w", pady=(0, SPACING.sm))
        self.precision_var = tk.StringVar(value=sim.planning_precision)
        PillRadioGroup(
            body,
            options=(("standard", "Standard"), ("cpa", "CPA Precision")),
            variable=self.precision_var,
            bg=COLORS.glass_strong,
        ).pack(anchor="w", pady=(0, SPACING.md))
        self.precision_var.trace_add("write", self._sync_sim_options)

        grid = ttk.Frame(body, style="Glass.TFrame")
        grid.pack(anchor="w", pady=SPACING.md)
        ttk.Label(grid, text="MC paths:").grid(row=0, column=0, sticky="w")
        self.paths_entry = ttk.Entry(grid, width=10)
        self.paths_entry.insert(0, str(sim.num_paths))
        self.paths_entry.grid(row=0, column=1, sticky="w", padx=SPACING.sm)
        ttk.Label(grid, text="Stock %:").grid(row=1, column=0, sticky="w")
        self.stock_entry = ttk.Entry(grid, width=10)
        self.stock_entry.insert(0, str(int(sim.mc_stock_allocation_pct)))
        self.stock_entry.grid(row=1, column=1, sticky="w", padx=SPACING.sm)
        ttk.Label(grid, text="Risk:").grid(row=2, column=0, sticky="w")
        self.risk_var = tk.StringVar(value=sim.risk_tolerance)
        ttk.Combobox(grid, textvariable=self.risk_var, values=["conservative", "moderate", "aggressive"], state="readonly", width=14).grid(
            row=2, column=1, sticky="w", padx=SPACING.sm
        )
        ttk.Button(body, text="Validate Tax Return", command=self.ctx.validate_tax_return).pack(anchor="w", pady=SPACING.xs)
        ttk.Button(body, text="Export Assumptions CSV", command=self.ctx.export_assumptions_csv).pack(anchor="w", pady=SPACING.xs)
        return frame

    def _sync_sim_options(self, *_args) -> None:
        sim = self.snapshot.simulation
        sim.simulation_mode = self.sim_mode_var.get()
        sim.planning_precision = self.precision_var.get()
        sim.risk_tolerance = self.risk_var.get()

    def run_plan(self) -> None:
        sim = self.snapshot.simulation
        try:
            sim.num_paths = int(self.paths_entry.get().strip() or "1000")
            sim.mc_stock_allocation_pct = float(self.stock_entry.get().strip() or "70")
        except ValueError:
            pass
        self._sync_sim_options()
        self.ctx.binder.pull_all()
        self.ctx.run_simulation()

    def _build_settings(self, parent: ttk.Frame) -> tk.Frame:
        frame = GlassPanel(parent, strong=True)
        body = frame.body
        ttk.Button(body, text="LLM Settings…", command=self.ctx.open_llm_settings).pack(anchor="w", pady=SPACING.sm)
        if not self.ctx.plaid_ready:
            ttk.Label(body, text="Plaid: not configured (.env)", style="Secondary.TLabel").pack(anchor="w")
        return frame

    def _load_cached_market(self) -> None:
        try:
            cached = self.ctx.data_service().load_cached_market_snapshot()
        except Exception:
            return
        if cached:
            self.ctx.apply_market_snapshot(cached)

    def collect_simulation_params(self) -> dict:
        self.ctx.binder.pull_all()
        return self.snapshot.to_simulation_params()
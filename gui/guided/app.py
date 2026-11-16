"""Guided mode — Liquid Glass one-question-at-a-time journey."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

from app.core.guided_catalog import guided_help_for_field, guided_question_for_field
from app.core.planning_snapshot import PlanningSnapshot
from app.core.workflow_definition import WorkflowDefinition
from config.model_fields import FIELDS_BY_KEY, field_choice_values

from gui.design_system.components.content_glass import ContentGlass
from gui.design_system.components.glass_input import glass_combobox
from gui.design_system.components.glass_panel import GlassPanel
from gui.design_system.components.guided_help_panel import GuidedHelpPanel
from gui.design_system.components.pill_controls import PillRadioGroup
from gui.design_system.components.primary_button import GhostButton, PrimaryButton, RunPlanButton, SecondaryButton
from gui.design_system.components.progress_strip import ProgressStrip
from gui.design_system.tokens import COLORS, FONTS, SPACING
from gui.design_system.typography import ui_family
from gui.legacy import _create_scrollable_frame
from gui.shared.app_context import AppContext
from gui.shared.catalog_sections import fields_for_wizard_step
from gui.shared.field_panel import render_guided_field_rows
from gui.shared.live_data_panel import build_live_data_panel
from gui.shared.llm_research_panel import build_llm_research_panel

if TYPE_CHECKING:
    from gui.app import RetirementApp


class GuidedApp:
    """Isolated guided presentation — safe to delete without touching expert/core."""

    def __init__(self, parent: tk.Widget, snapshot: PlanningSnapshot, *, shell: RetirementApp) -> None:
        self.parent = parent
        self.root = parent.winfo_toplevel()
        self.snapshot = snapshot
        self.shell = shell
        self.ctx = AppContext(self.root, snapshot)
        self.workflow = WorkflowDefinition(
            household_mode=snapshot.household_mode,
            include_spouse=snapshot.household_mode == "joint",
        )
        self.step_index = self.workflow.index_of(snapshot.wizard.last_step_id)

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self._host = tk.Frame(parent, bg=COLORS.bg_base, highlightthickness=0)
        self._host.grid(row=0, column=0, sticky="nsew")
        self._host.columnconfigure(0, weight=1)
        self._host.rowconfigure(0, weight=1)

        self.scroll_host = tk.Frame(self._host, bg=COLORS.bg_base, highlightthickness=0)
        self.scroll_host.grid(row=0, column=0, sticky="nsew", pady=(0, SPACING.xs))
        self.scroll_host.rowconfigure(1, weight=1)
        self.scroll_host.columnconfigure(0, weight=1)

        self._build_title_row()
        _canvas, self.body = _create_scrollable_frame(self.scroll_host)
        self.body.columnconfigure(0, weight=0)

        self._build_footer()
        self._help = GuidedHelpPanel(self._host)
        self._render_step()

    def _build_title_row(self) -> None:
        row = tk.Frame(self.scroll_host, bg=COLORS.bg_base, highlightthickness=0)
        row.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.md))
        self.step_title_var = tk.StringVar()
        tk.Label(
            row,
            textvariable=self.step_title_var,
            bg=COLORS.bg_base,
            fg=COLORS.text_primary,
            font=(ui_family(), FONTS.size_step_title, "bold"),
        ).pack(side="left", padx=(SPACING.lg, 0))
        self.help_btn = SecondaryButton(row, text="Help", command=self._open_help)
        self.help_btn.pack(side="right")

    def _build_footer(self) -> None:
        outer = tk.Frame(self._host, bg=COLORS.bg_base, highlightthickness=0)
        outer.grid(row=1, column=0, sticky="ew")
        dock = GlassPanel(outer, padding=SPACING.md)
        dock.pack(fill="x")

        nav = tk.Frame(dock.body, bg=COLORS.glass, highlightthickness=0)
        nav.pack(fill="x")
        left = tk.Frame(nav, bg=COLORS.glass, highlightthickness=0)
        left.pack(side="left")
        self.back_btn = GhostButton(left, text="← Back", command=self._go_back)
        self.back_btn.pack(side="left", padx=(0, SPACING.sm))
        self.skip_btn = GhostButton(left, text="Skip for now", command=self._skip_step)
        self.skip_btn.pack(side="left")

        self._action_host = tk.Frame(nav, bg=COLORS.glass, highlightthickness=0)
        self._action_host.pack(side="right")
        self.continue_btn = PrimaryButton(self._action_host, text="Continue →", command=self._go_next)
        self.continue_btn.pack(side="right")
        self.run_btn = RunPlanButton(self._action_host, text="Run plan", command=self.run_plan)
        self.run_btn.pack(side="right")
        self.run_btn.pack_forget()

        self._progress = ProgressStrip(dock.body)
        self._progress.pack(fill="x")

    def _current_step(self):
        return self.workflow.step_at(self.step_index)

    def _journey_label(self, step_id: str) -> str:
        mapping = {
            "setup": "Start",
            "timeline": "Start",
            "income_self": "Money in",
            "income_wife": "Money in",
            "assets_self": "Money in",
            "assets_wife": "Money in",
            "spending_self": "Money out",
            "spending_wife": "Money out",
            "healthcare_self": "Health",
            "healthcare_wife": "Health",
            "tax_benefits": "Taxes & income",
            "tax_wife": "Taxes & income",
            "market": "Economy",
            "live_data": "Connect",
            "llm_assist": "Connect",
            "review": "Your plan",
        }
        return mapping.get(step_id, "Your plan")

    def _help_items_for_step(self, step_id: str) -> list[tuple[str, str]]:
        if step_id == "setup":
            return [
                ("Question 1", "Together (joint) — shared assets, combined taxes.\n\nSeparately — independent projections."),
                ("Question 2", "State of residence sets your state tax tables."),
            ]
        step = self._current_step()
        person = step.person_scope if step.person_scope in ("self", "wife") else None
        fields = fields_for_wizard_step(step_id)
        items: list[tuple[str, str]] = []
        for idx, field in enumerate(fields, start=1):
            q = guided_question_for_field(field, person=person)
            body = guided_help_for_field(field) or "No additional help for this field."
            items.append((f"{idx} · {q}", body))
        return items

    def _open_help(self) -> None:
        step = self._current_step()
        items = self._help_items_for_step(step.step_id)
        self._help.open(title=f"Help — {step.label}", items=items)

    def _render_step(self) -> None:
        self.ctx.binder.prune_destroyed_widgets()
        for child in self.body.winfo_children():
            child.destroy()

        step = self._current_step()
        self.snapshot.wizard.last_step_id = step.step_id
        total = len(self.workflow.steps)
        journey = self._journey_label(step.step_id)
        self.step_title_var.set(step.label.title())
        self._progress.set_progress(step=self.step_index + 1, total=total, journey=journey)

        self.shell.update_shell_status(headline="Building your retirement picture", detail=f"{journey} — {step.label}")
        is_review = step.step_id == "review"
        self.shell.set_status_bar_visible(False)
        self.shell.set_run_plan_enabled(False)

        if step.step_id == "setup":
            self._render_setup()
        elif step.step_id == "live_data":
            self._render_live_data()
        elif step.step_id == "llm_assist":
            self._render_llm()
        elif step.step_id == "review":
            self._render_review()
        else:
            self._render_fields(step.step_id)

        self.back_btn.set_disabled(self.step_index <= 0)
        self.skip_btn.set_disabled(is_review)
        if is_review:
            self.continue_btn.pack_forget()
            self.run_btn.pack(side="right")
        else:
            self.run_btn.pack_forget()
            self.continue_btn.pack(side="right")

    def _render_setup(self) -> None:
        card = ContentGlass(self.body, wide=True)
        card.grid(row=0, column=0, sticky="nw", pady=SPACING.sm)
        frame = card.body

        self._render_numbered_block(frame, 1, "How are you modeling your household?")
        self.mode_var = tk.StringVar(value=self.snapshot.household_mode)
        PillRadioGroup(
            frame,
            options=(("joint", "Together (joint)"), ("separate", "Separately")),
            variable=self.mode_var,
            bg=COLORS.glass_strong,
        ).pack(anchor="w", padx=SPACING.lg, pady=(0, SPACING.lg))

        self._render_divider(frame)
        self._render_numbered_block(frame, 2, "Where do you live?")
        state_field = FIELDS_BY_KEY["state_of_residence"]
        self.state_combo = glass_combobox(
            frame,
            values=list(field_choice_values(state_field) or ("CA",)),
            width=10,
        )
        self.state_combo.set(self.snapshot.assumption_state)
        self.state_combo.master.pack(anchor="w", padx=SPACING.lg, pady=(0, SPACING.md))
        tk.Label(
            frame,
            text=f"Tax tables in use — {self.ctx.policy_label}",
            bg=COLORS.glass_strong,
            fg=COLORS.text_muted,
            font=(ui_family(), FONTS.size_caption),
        ).pack(anchor="w", padx=SPACING.lg, pady=(0, SPACING.lg))

    def _render_numbered_block(self, parent: tk.Frame, number: int, text: str) -> None:
        row = tk.Frame(parent, bg=COLORS.glass_strong, highlightthickness=0)
        row.pack(anchor="w", fill="x", padx=SPACING.lg, pady=(SPACING.md, SPACING.sm))
        tk.Label(row, text=f"{number}.", bg=COLORS.glass_strong, fg=COLORS.accent, font=("SF Pro Text", 18, "bold")).pack(
            side="left", anchor="nw"
        )
        tk.Label(row, text=text, bg=COLORS.glass_strong, fg=COLORS.text_primary, font=("SF Pro Text", 18)).pack(
            side="left", anchor="w", padx=(SPACING.xs, 0)
        )

    def _render_divider(self, parent: tk.Frame) -> None:
        tk.Frame(parent, bg=COLORS.glass_border_subtle, height=1).pack(fill="x", padx=SPACING.md, pady=SPACING.md)

    def _summary_card(self, parent: tk.Frame, heading: str, body: str) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=COLORS.glass_btn,
            highlightthickness=1,
            highlightbackground=COLORS.glass_border_subtle,
        )
        tk.Label(
            card,
            text=heading,
            bg=COLORS.glass_btn,
            fg=COLORS.text_primary,
            font=(ui_family(), 13, "bold"),
        ).pack(anchor="w", padx=SPACING.md, pady=(SPACING.md, SPACING.xs))
        tk.Label(
            card,
            text=body,
            bg=COLORS.glass_btn,
            fg=COLORS.text_secondary,
            font=(ui_family(), 12),
            wraplength=220,
            justify="left",
        ).pack(anchor="w", padx=SPACING.md, pady=(0, SPACING.md))
        return card

    def _render_fields(self, step_id: str) -> None:
        step = self._current_step()
        card = ContentGlass(self.body, wide=step_id in ("timeline", "market"))
        card.grid(row=0, column=0, sticky="nw", pady=SPACING.sm)
        fields = fields_for_wizard_step(step_id)
        person = step.person_scope if step.person_scope in ("self", "wife") else None
        render_guided_field_rows(card.body, fields, self.ctx.binder, person=person)

    def _render_live_data(self) -> None:
        card = ContentGlass(self.body, wide=True)
        card.grid(row=0, column=0, sticky="nsew", pady=SPACING.sm)
        build_live_data_panel(card.body, self.ctx, compact=True, title="Pull real balances when you're ready (optional)")

    def _render_llm(self) -> None:
        card = ContentGlass(self.body, wide=True)
        card.grid(row=0, column=0, sticky="nsew", pady=SPACING.sm)
        build_llm_research_panel(
            card.body,
            self.ctx,
            compact=True,
            title="Let AI fill in reasonable estimates for unanswered fields (optional)",
        )

    def _render_review(self) -> None:
        card = ContentGlass(self.body, wide=True)
        card.grid(row=0, column=0, sticky="ew", pady=SPACING.sm)
        frame = card.body
        self.ctx.binder.pull_all()
        params = self.snapshot.to_simulation_params()
        self_assets = params["persons"]["self"].get("initial_assets", 0)

        self._render_numbered_block(frame, 1, "Your plan at a glance")
        summary = tk.Frame(frame, bg=COLORS.glass_strong, highlightthickness=0)
        summary.pack(fill="x", padx=SPACING.lg, pady=(0, SPACING.lg))
        left = tk.Frame(summary, bg=COLORS.glass_strong, highlightthickness=0)
        left.pack(side="left", fill="x", expand=True, padx=(0, SPACING.sm))
        right = tk.Frame(summary, bg=COLORS.glass_strong, highlightthickness=0)
        right.pack(side="left", fill="x", expand=True, padx=(SPACING.sm, 0))
        self._summary_card(left, "Household", self.snapshot.household_mode.title()).pack(fill="x", pady=(0, SPACING.sm))
        self._summary_card(left, "State", self.snapshot.assumption_state).pack(fill="x")
        self._summary_card(right, "Your assets today", f"${self_assets:,.0f}").pack(fill="x")

        self._render_divider(frame)
        self._render_numbered_block(frame, 2, "How should we simulate your plan?")
        self.sim_mode_var = tk.StringVar(value=self.snapshot.simulation.simulation_mode)
        PillRadioGroup(
            frame,
            options=(
                ("deterministic", "Single"),
                ("monte_carlo", "Monte Carlo"),
                ("both", "Both"),
            ),
            variable=self.sim_mode_var,
            bg=COLORS.glass_strong,
        ).pack(anchor="w", padx=SPACING.lg, pady=(0, SPACING.lg))

        self.shell.update_shell_status(
            headline="Ready to see your future",
            detail="Review looks good — run your plan when you're ready.",
        )

    def _persist_step_input(self) -> None:
        step = self._current_step()
        if step.step_id == "setup":
            self.snapshot.household_mode = self.mode_var.get()
            self.snapshot.assumption_state = self.state_combo.get()
            self.snapshot.shared["state_of_residence"] = self.state_combo.get()
            self.workflow = WorkflowDefinition(
                household_mode=self.snapshot.household_mode,
                include_spouse=self.snapshot.household_mode == "joint",
            )
        elif step.step_id == "review":
            self.snapshot.simulation.simulation_mode = self.sim_mode_var.get()
        else:
            self.ctx.binder.pull_all()

    def _mark_complete(self) -> None:
        step_id = self._current_step().step_id
        if step_id not in self.snapshot.wizard.completed_steps:
            self.snapshot.wizard.completed_steps.append(step_id)

    def _go_next(self) -> None:
        self._persist_step_input()
        self._mark_complete()
        if self.step_index + 1 >= len(self.workflow.steps):
            return
        self.step_index += 1
        self._render_step()

    def _go_back(self) -> None:
        self._persist_step_input()
        if self.step_index <= 0:
            return
        self.step_index -= 1
        self._render_step()

    def _skip_step(self) -> None:
        step = self._current_step()
        if step.step_id == "review":
            return
        if step.step_id not in self.snapshot.wizard.skipped_steps:
            self.snapshot.wizard.skipped_steps.append(step.step_id)
        if self.step_index + 1 >= len(self.workflow.steps):
            return
        self.step_index += 1
        self._render_step()

    def run_plan(self) -> None:
        if self._current_step().step_id != "review":
            return
        self._persist_step_input()
        self.ctx.run_simulation()

    def collect_simulation_params(self) -> dict:
        self.ctx.binder.pull_all()
        return self.snapshot.to_simulation_params()
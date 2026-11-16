"""Shared LLM batch research UI for expert sidebar and guided wizard step."""

from __future__ import annotations

import time

import tkinter as tk
from tkinter import messagebox, ttk

from app.core.controllers.llm_research_controller import (
    LLMResearchBatchRequest,
    LLMResearchController,
    LLMResearchFieldSelection,
    field_display_label,
    is_field_empty,
)
from config.model_fields import ModelField, llm_researchable_fields
from gui.legacy import _create_scrollable_frame
from gui.shared.app_context import AppContext
from gui.shared.batch_research_review_dialog import BatchResearchReviewDialog
from security.llm_keychain import get_api_key, has_api_key
from settings import get_settings


class LLMResearchPanel:
    """Embeddable batch research section — no per-field Research buttons."""

    def __init__(
        self,
        parent: ttk.Frame,
        ctx: AppContext,
        *,
        compact: bool = False,
        dialog_parent: tk.Misc | None = None,
    ) -> None:
        self.parent = parent
        self.ctx = ctx
        self.compact = compact
        self._dialog_parent = dialog_parent
        self.controller = LLMResearchController()
        self._checks: list[tuple[tk.BooleanVar, ModelField, str | None]] = []
        self._progress_var = tk.StringVar(value="")
        self._build()

    def _message_parent(self) -> tk.Misc:
        return self._dialog_parent or self.ctx.root

    def _wait_dialog(self, win: tk.Misc) -> None:
        root = self.ctx.root
        try:
            while win.winfo_exists():
                root.update()
                time.sleep(0.02)
        except tk.TclError:
            pass

    def _build(self) -> None:
        status_row = ttk.Frame(self.parent)
        status_row.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(status_row, textvariable=tk.StringVar(value=self.ctx.llm_status_text())).pack(side="left")
        ttk.Button(status_row, text="LLM Settings…", command=self._open_llm_settings).pack(side="right")

        ttk.Label(
            self.parent,
            text="Additional context (optional):",
            wraplength=700 if not self.compact else 560,
        ).pack(anchor="w", padx=8, pady=(8, 2))
        prompt_height = 3 if self.compact else 4
        self.prompt_text = tk.Text(self.parent, height=prompt_height, width=72 if self.compact else 80)
        self.prompt_text.insert("1.0", self.ctx.snapshot.llm_extra_prompt)
        self.prompt_text.pack(padx=8, pady=4, fill="x")

        select_row = ttk.Frame(self.parent)
        select_row.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(select_row, text="Select fields to research:", font=("", 10, "bold")).pack(side="left")
        ttk.Button(select_row, text="All", command=self._select_all).pack(side="right", padx=(4, 0))
        ttk.Button(select_row, text="None", command=self._select_none).pack(side="right", padx=(4, 0))
        ttk.Button(select_row, text="Empty only", command=self._select_empty).pack(side="right", padx=(4, 0))

        list_host = ttk.Frame(self.parent)
        list_height = 180 if self.compact else 260
        list_host.pack(fill="both", expand=not self.compact, padx=8, pady=4)
        list_host.rowconfigure(0, weight=1)
        list_host.columnconfigure(0, weight=1)
        canvas, list_frame = _create_scrollable_frame(list_host)
        canvas.config(height=list_height)
        list_frame.columnconfigure(0, weight=1)

        self._checks.clear()
        for field in llm_researchable_fields():
            if field.scope == "person":
                for person in ("self", "wife"):
                    var = tk.BooleanVar(value=False)
                    ttk.Checkbutton(
                        list_frame,
                        text=field_display_label(field, person=person),
                        variable=var,
                    ).pack(anchor="w")
                    self._checks.append((var, field, person))
            else:
                var = tk.BooleanVar(value=False)
                ttk.Checkbutton(list_frame, text=field_display_label(field), variable=var).pack(anchor="w")
                self._checks.append((var, field, None))

        action_row = ttk.Frame(self.parent)
        action_row.pack(fill="x", padx=8, pady=8)
        ttk.Button(action_row, text="Research selected", command=self._run_batch).pack(side="left")
        ttk.Label(action_row, textvariable=self._progress_var, foreground="#555").pack(side="left", padx=12)

    def _selected(self) -> list[tuple[ModelField, str | None]]:
        return [(field, person) for var, field, person in self._checks if var.get()]

    def _select_all(self) -> None:
        for var, _field, _person in self._checks:
            var.set(True)

    def _select_none(self) -> None:
        for var, _field, _person in self._checks:
            var.set(False)

    def _select_empty(self) -> None:
        snapshot = self.ctx.snapshot
        for var, field, person in self._checks:
            var.set(is_field_empty(snapshot, field, person=person))

    def _persist_prompt(self) -> None:
        self.ctx.snapshot.llm_extra_prompt = self.prompt_text.get("1.0", "end").strip()

    def _open_llm_settings(self) -> None:
        from gui.legacy import LLMSettingsDialog
        from settings import save_llm_settings

        dialog = LLMSettingsDialog(self._message_parent(), self.ctx.llm_settings)
        self._wait_dialog(dialog)
        if dialog.saved_settings is not None:
            self.ctx.llm_settings = dialog.saved_settings
            save_llm_settings(self.ctx.llm_settings)

    def _run_batch(self) -> None:
        self._persist_prompt()
        selected = self._selected()
        if not selected:
            messagebox.showinfo(
                "No Selection",
                "Select at least one field to research.",
                parent=self._message_parent(),
            )
            return

        settings = self.ctx.llm_settings
        if not settings.configured or not has_api_key(settings.provider):
            messagebox.showwarning(
                "LLM Required",
                "Configure LLM Settings and save an API key first.",
                parent=self._message_parent(),
            )
            return

        request = LLMResearchBatchRequest(
            snapshot=self.ctx.snapshot,
            selections=[
                LLMResearchFieldSelection(field_key=field.key, person=person)
                for field, person in selected
            ],
            extra_prompt=self.ctx.snapshot.llm_extra_prompt,
            provider=settings.provider,
            model=settings.model,
        )

        self.ctx.root.config(cursor="watch")
        self._progress_var.set("Researching…")
        self.ctx.root.update_idletasks()
        try:
            result = self.controller.run_batch(
                request,
                api_key=get_api_key(settings.provider),
                fred_api_key=get_settings().fred_api_key,
                on_progress=self._on_progress,
            )
        finally:
            self.ctx.root.config(cursor="")
            self._progress_var.set("")

        if not result.ok:
            messagebox.showerror(result.title, result.message, parent=self._message_parent())
            return

        batch = result.payload
        parent = self._message_parent()
        dialog = BatchResearchReviewDialog(parent, batch.results, self.ctx.binder)
        self._wait_dialog(dialog)
        if dialog.applied_count:
            messagebox.showinfo(
                "Applied",
                f"Updated {dialog.applied_count} field(s) from LLM research.",
                parent=parent,
            )

    def _on_progress(self, index: int, total: int, field_key: str) -> None:
        self._progress_var.set(f"Researching {index}/{total}: {field_key}")
        self.ctx.root.update_idletasks()


def build_llm_research_panel(
    parent: ttk.Frame,
    ctx: AppContext,
    *,
    compact: bool = False,
    title: str | None = "AI Assistant",
    select_empty: bool = False,
    dialog_parent: tk.Misc | None = None,
) -> ttk.Frame:
    """Create a labeled frame containing the shared batch research panel."""
    frame = ttk.LabelFrame(parent, text=title) if title else ttk.Frame(parent)
    frame.pack(fill="both", expand=True, padx=8, pady=8)
    panel = LLMResearchPanel(frame, ctx, compact=compact, dialog_parent=dialog_parent)
    if select_empty:
        panel._select_empty()
    return frame
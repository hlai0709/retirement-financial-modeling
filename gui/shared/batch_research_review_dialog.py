"""Review batch LLM research results before applying to planning fields."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from config.model_fields import FIELDS_BY_KEY, ModelField
from gui.legacy import _format_suggestion_value
from gui.shared.field_binder import FieldBinder
from providers.assumption_research import AssumptionResearchResult, AssumptionSuggestion


class BatchResearchReviewDialog(tk.Toplevel):
    """Modal table of batch suggestions with per-row include toggles."""

    def __init__(self, parent, results: list[AssumptionResearchResult], binder: FieldBinder):
        super().__init__(parent)
        self.title("Review LLM Research")
        self.geometry("920x520")
        self.minsize(760, 420)
        self.transient(parent)
        self.grab_set()

        self.binder = binder
        self.applied_count = 0
        self._rows: list[dict] = []

        succeeded = sum(1 for item in results if item.suggestion is not None)
        failed = len(results) - succeeded
        summary = f"{succeeded} suggestion(s) ready to review"
        if failed:
            summary += f"; {failed} failed"
        ttk.Label(self, text=summary, wraplength=860).pack(padx=12, pady=(12, 8), anchor="w")

        columns = ("include", "field", "suggested", "confidence", "status")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=4)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=14)
        for col, label, width in (
            ("include", "Apply?", 70),
            ("field", "Field", 280),
            ("suggested", "Suggested", 120),
            ("confidence", "Confidence", 90),
            ("status", "Notes", 260),
        ):
            self.tree.heading(col, text=label)
            anchor = "e" if col == "suggested" else "w"
            self.tree.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("error", foreground="#a33")
        self.tree.bind("<Double-1>", self._toggle_include)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)

        for item in results:
            field = FIELDS_BY_KEY.get(item.field_key)
            label = _result_label(field, item)
            if item.suggestion is not None and field is not None:
                suggested = _format_suggestion_value(field, item.suggestion.suggested_annual_usd)
                confidence = item.suggestion.confidence
                status = item.suggestion.explanation[:120]
                include_default = True
                tags = ()
            else:
                suggested = "—"
                confidence = "—"
                status = item.error or "Research failed"
                include_default = False
                tags = ("error",)

            iid = self.tree.insert(
                "",
                "end",
                values=(
                    "yes" if include_default else "no",
                    label,
                    suggested,
                    confidence,
                    status,
                ),
                tags=tags,
            )
            self._rows.append(
                {
                    "iid": iid,
                    "result": item,
                    "field": field,
                    "include_var": tk.BooleanVar(value=include_default),
                }
            )

        detail_frame = ttk.LabelFrame(self, text="Details")
        detail_frame.pack(fill="x", padx=12, pady=8)
        self.detail_text = tk.Text(detail_frame, height=6, wrap="word")
        self.detail_text.pack(fill="x", padx=8, pady=6)
        self.detail_text.config(state="disabled")

        ttk.Label(
            self,
            text="Educational research only — not licensed financial or medical advice.",
            foreground="#8a5a00",
            wraplength=860,
        ).pack(padx=12, pady=4, anchor="w")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=12)
        ttk.Button(btn_frame, text="Apply selected", command=self._apply_selected).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _toggle_include(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        for row in self._rows:
            if row["iid"] != iid or row["field"] is None or row["result"].suggestion is None:
                continue
            new_value = not row["include_var"].get()
            row["include_var"].set(new_value)
            values = list(self.tree.item(iid, "values"))
            values[0] = "yes" if new_value else "no"
            self.tree.item(iid, values=values)
            break

    def _show_detail(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        row = next((item for item in self._rows if item["iid"] == iid), None)
        if row is None:
            return
        suggestion = row["result"].suggestion
        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", "end")
        if suggestion is None:
            self.detail_text.insert("1.0", row["result"].error or "No suggestion available.")
        else:
            sources = "\n".join(f"  • {source}" for source in suggestion.sources)
            self.detail_text.insert(
                "1.0",
                (
                    f"Method: {suggestion.projection_method}\n\n"
                    f"{suggestion.explanation}\n\n"
                    f"Sources:\n{sources}"
                ),
            )
        self.detail_text.config(state="disabled")

    def _apply_selected(self) -> None:
        applied = 0
        for row in self._rows:
            if not row["include_var"].get():
                continue
            result = row["result"]
            field = row["field"]
            suggestion = result.suggestion
            if field is None or suggestion is None:
                continue
            self._apply_suggestion(field, result.person, suggestion)
            applied += 1
        self.applied_count = applied
        self.destroy()

    def _apply_suggestion(
        self,
        field: ModelField,
        person: str | None,
        suggestion: AssumptionSuggestion,
    ) -> None:
        value = suggestion.suggested_annual_usd
        if field.field_type == "rate_pct":
            value = value if abs(value) <= 1 else value / 100
        elif field.field_type in ("age", "year", "int"):
            value = int(round(value))
        self.binder.set_value(
            field,
            value,
            person=person,
            source=suggestion.source_label(),
        )


def _result_label(field: ModelField | None, item: AssumptionResearchResult) -> str:
    if field is None:
        return item.field_key
    if item.person:
        return f"{item.person.title()} — {field.label}"
    return field.label
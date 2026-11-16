"""Review/apply dialog for document import — mirrors LLM batch review pattern."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable


def show_document_import_review(
    parent: tk.Misc,
    rows: list[dict[str, Any]],
    *,
    title: str = "Review imported fields",
    on_apply: Callable[[list[dict[str, Any]]], None] | None = None,
) -> None:
    """Simple Tk review table with per-row apply checkboxes."""
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Select fields to apply to your plan:").pack(anchor="w", pady=(0, 8))

    vars_by_idx: list[tk.BooleanVar] = []
    for i, row in enumerate(rows):
        var = tk.BooleanVar(value=bool(row.get("apply_default", row.get("apply"))))
        if not row.get("ok", True):
            var.set(False)
        vars_by_idx.append(var)
        label = f"{row.get('label', row.get('field_key', ''))}: {row.get('suggested', '')}"
        cb = ttk.Checkbutton(frame, text=label, variable=var, state="normal" if row.get("ok", True) else "disabled")
        cb.pack(anchor="w", pady=2)

    def _apply() -> None:
        payload = []
        for i, row in enumerate(rows):
            copy = dict(row)
            copy["apply"] = vars_by_idx[i].get()
            payload.append(copy)
        if on_apply:
            on_apply(payload)
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="Apply selected", command=_apply).pack(side="right", padx=(0, 8))
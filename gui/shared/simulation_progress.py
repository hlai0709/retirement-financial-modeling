"""Progress UI for background simulation runs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class SimulationProgressDialog(tk.Toplevel):
    """Non-blocking progress window with cancel support."""

    def __init__(self, parent: tk.Misc, *, title: str = "Running Simulation") -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("420x140")
        self.minsize(360, 120)
        self.transient(parent)
        self._cancelled = False

        self.status_var = tk.StringVar(value="Starting simulation…")
        self.progress_var = tk.DoubleVar(value=0.0)

        ttk.Label(self, textvariable=self.status_var, wraplength=380).pack(
            anchor="w", padx=16, pady=(16, 8)
        )
        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100.0)
        self.progress.pack(fill="x", padx=16, pady=4)
        ttk.Button(self, text="Cancel", command=self._on_cancel).pack(anchor="e", padx=16, pady=12)

        self._on_cancel_callback = None
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def bind_cancel(self, callback) -> None:
        self._on_cancel_callback = callback

    def _on_cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self.status_var.set("Cancelling…")
        if self._on_cancel_callback is not None:
            self._on_cancel_callback()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def update_progress(self, completed: int, total: int, *, phase: str = "Simulation") -> None:
        if total <= 0:
            self.progress_var.set(0.0)
            self.status_var.set(f"{phase}…")
            return
        pct = min(100.0, (completed / total) * 100.0)
        self.progress_var.set(pct)
        self.status_var.set(f"{phase}: {completed:,} / {total:,}")

    def close(self) -> None:
        if self.winfo_exists():
            self.destroy()
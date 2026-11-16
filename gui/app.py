"""Application entry — persistent shell + Guided/Expert presentation forks."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from app.core.planning_snapshot import PlanningSnapshot
from gui.design_system import install_theme
from gui.design_system.components.ambient_canvas import AmbientCanvas, GradientMount
from gui.design_system.components.app_header import AppHeader
from gui.design_system.components.status_bar import StatusBar
from gui.design_system.tokens import COLORS, SPACING
from gui.expert.app import ExpertApp
from gui.guided.app import GuidedApp
from gui.shared.preferences import load_presentation_mode, save_presentation_mode


class RetirementApp:
    """Top-level shell; owns PlanningSnapshot and swaps presentation forks."""

    def __init__(self, root: tk.Tk, *, presentation_mode: str | None = None) -> None:
        self.root = root
        install_theme(root)
        root.title("Retirement Planner")
        root.geometry("980x740")
        root.minsize(820, 600)
        root.configure(bg=COLORS.bg_base)

        self.snapshot = PlanningSnapshot(
            presentation_mode=presentation_mode or load_presentation_mode(),
        )
        self._active_app: ExpertApp | GuidedApp | None = None

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self._ambient = AmbientCanvas(root)
        self._ambient.place(relx=0, rely=0, relwidth=1, relheight=1)

        # Rounded window chrome (mockup .window)
        self._window = tk.Frame(
            root,
            bg=COLORS.glass_border_subtle,
            highlightthickness=0,
        )
        self._window.grid(row=0, column=0, sticky="nsew", padx=SPACING.chrome_margin, pady=SPACING.chrome_margin)
        self._window.columnconfigure(0, weight=1)
        self._window.rowconfigure(0, weight=1)

        self._window_inner = tk.Frame(self._window, bg=COLORS.bg_base, highlightthickness=0)
        self._window_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self._window_inner.columnconfigure(0, weight=1)
        self._window_inner.rowconfigure(1, weight=1)

        self._win_ambient = AmbientCanvas(self._window_inner)
        self._win_ambient.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.header = AppHeader(
            self._window_inner,
            mode=self.snapshot.presentation_mode,
            on_mode_change=self._switch_mode,
        )
        self.header.grid(row=0, column=0, sticky="ew", padx=SPACING.md, pady=(SPACING.md, SPACING.xs))

        self.content_mount = GradientMount(
            self._window_inner,
            margin_x=SPACING.xl + SPACING.lg,
            margin_y=0,
        )
        self.content_mount.grid(row=1, column=0, sticky="nsew")
        self.content_host = self.content_mount.body
        self.content_host.columnconfigure(0, weight=1)
        self.content_host.rowconfigure(0, weight=1)

        self.status_bar = StatusBar(self._window_inner, on_run_plan=self._run_plan)
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=SPACING.md, pady=(SPACING.xs, SPACING.md))

        self._mount_presentation(self.snapshot.presentation_mode)

    def update_shell_status(self, *, headline: str, detail: str = "") -> None:
        self.status_bar.set_status(headline=headline, detail=detail)

    def set_run_plan_enabled(self, enabled: bool) -> None:
        self.status_bar.set_run_enabled(enabled)

    def set_status_bar_visible(self, visible: bool) -> None:
        if visible:
            self.status_bar.grid(row=2, column=0, sticky="ew", padx=SPACING.md, pady=(SPACING.xs, SPACING.md))
        else:
            self.status_bar.grid_remove()

    def _clear_content_children(self) -> None:
        for child in self.content_host.winfo_children():
            child.destroy()

    def _mount_presentation(self, mode: str) -> None:
        self.snapshot.presentation_mode = mode
        save_presentation_mode(mode)
        self.header.set_mode(mode)
        self._clear_content_children()
        try:
            if mode == "expert":
                self._active_app = ExpertApp(
                    self.content_host,
                    self.snapshot,
                    shell=self,
                )
            else:
                self._active_app = GuidedApp(
                    self.content_host,
                    self.snapshot,
                    shell=self,
                )
        except Exception as exc:
            self._active_app = None
            messagebox.showerror("UI Error", f"Failed to open {mode} mode:\n{exc}", parent=self.root)
            raise

    def switch_mode(self, mode: str) -> None:
        self._switch_mode(mode)

    def _switch_mode(self, mode: str) -> None:
        if mode == self.snapshot.presentation_mode:
            return
        if self._active_app is not None and hasattr(self._active_app, "ctx"):
            self._active_app.ctx.binder.pull_all()
        self._mount_presentation(mode)

    def _run_plan(self) -> None:
        if self._active_app is not None and hasattr(self._active_app, "run_plan"):
            self._active_app.run_plan()

    def collect_simulation_params(self) -> dict:
        if self._active_app is None:
            return self.snapshot.to_simulation_params()
        return self._active_app.collect_simulation_params()

    def _collect_simulation_params(self) -> dict:
        return self.collect_simulation_params()


RetirementGUI = RetirementApp
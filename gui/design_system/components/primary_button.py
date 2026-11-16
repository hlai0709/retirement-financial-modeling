"""Button primitives — delegate to PillButton."""

from __future__ import annotations

from typing import Callable

import tkinter as tk

from gui.design_system.components.pill_controls import PillButton


class PrimaryButton(PillButton):
    def __init__(self, parent: tk.Misc, *, text: str, command: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(parent, text=text, command=command, variant="primary", **kwargs)


class RunPlanButton(PillButton):
    def __init__(self, parent: tk.Misc, *, text: str = "Run plan", command: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(parent, text=text, command=command, variant="run", **kwargs)


class SecondaryButton(PillButton):
    def __init__(self, parent: tk.Misc, *, text: str, command: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(parent, text=text, command=command, variant="help", **kwargs)


class GhostButton(PillButton):
    def __init__(self, parent: tk.Misc, *, text: str, command: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(parent, text=text, command=command, variant="secondary", **kwargs)
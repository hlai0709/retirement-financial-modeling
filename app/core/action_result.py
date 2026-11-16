"""Structured outcomes from controllers — views decide how to display them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ActionLevel = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    title: str
    message: str
    level: ActionLevel = "info"
    payload: Any = None

    @classmethod
    def success(
        cls,
        title: str,
        message: str = "",
        *,
        payload: Any = None,
        level: ActionLevel = "info",
    ) -> ActionResult:
        return cls(ok=True, title=title, message=message, level=level, payload=payload)

    @classmethod
    def failure(
        cls,
        title: str,
        message: str,
        *,
        level: ActionLevel = "error",
        payload: Any = None,
    ) -> ActionResult:
        return cls(ok=False, title=title, message=message, level=level, payload=payload)

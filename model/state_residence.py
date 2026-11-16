"""Resolve state of residence across relocation years."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from model.state_taxes import canonical_state_code, state_profile, state_uses_fallback


@dataclass(frozen=True)
class YearStateContext:
    code: str
    name: str
    label: str
    fallback: bool = False


def state_residence_defaults(params: dict[str, Any]) -> tuple[str, int | None, str]:
    residence = canonical_state_code(
        str(params.get("state_of_residence", "CA") or "CA")
    )
    move_year = params.get("state_move_year")
    if move_year in (None, "", 0, "0"):
        move_year = None
    else:
        move_year = int(move_year)
        if move_year < 1900:
            move_year = None
    after_move = canonical_state_code(
        str(params.get("state_after_move") or params.get("state_of_residence") or residence)
    )
    return residence, move_year, after_move


def resolve_year_state(
    *,
    year: int,
    state_residence: str,
    state_move_year: int | None,
    state_after_move: str,
) -> YearStateContext:
    code = state_residence
    if state_move_year is not None and year >= state_move_year:
        code = state_after_move
    requested = canonical_state_code(code)
    profile = state_profile(requested)
    assert profile is not None
    fallback = state_uses_fallback(requested)
    label = requested
    if state_move_year is not None and year == state_move_year:
        label = f"{state_residence}→{state_after_move}"
    name = profile.name
    if fallback:
        name = f"{requested} (CA estimate)"
        if label == requested:
            label = f"{requested}~CA"
    return YearStateContext(
        code=requested,
        name=name,
        label=label,
        fallback=fallback,
    )
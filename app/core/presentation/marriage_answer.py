"""Joint “Are you married?” answer and the filing status it writes.

A past marriage year is rejected by the projection, so “Yes” leaves the year
blank and uses married filing jointly for every year. “Not yet” keeps a
future year and files single until that year.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.planning_snapshot import PlanningSnapshot
from config.model_fields import SHARED_FIELDS, ModelField

_SHARED_FIELDS = {field.key: field for field in SHARED_FIELDS}


def _shared_field(key: str) -> ModelField:
    field = _SHARED_FIELDS.get(key)
    if field is None:
        raise KeyError(key)
    return field

_YES = "yes"
_NO = "no"


def _year_text(snapshot: PlanningSnapshot) -> str:
    return str(snapshot.shared.get("marriage_year") or "").strip()


def _filing_text(snapshot: PlanningSnapshot) -> str:
    return str(snapshot.shared.get("filing_status") or "").strip()


def _calendar_year(text: str) -> int | None:
    if text in {"", "0"}:
        return None
    try:
        year = int(float(text))
    except ValueError:
        return None
    return year


def marriage_answer(snapshot: PlanningSnapshot) -> str:
    """Stored answer: yes, no, or blank when the question has not been answered.

    A year already in the past is an earlier plan's "when did you marry?"
    answer, so the page opens on Yes instead of a future-year error.
    """
    if snapshot.household_mode != "joint":
        return ""
    year = _calendar_year(_year_text(snapshot))
    if year is not None:
        if year < date.today().year:
            return _YES
        return _NO
    if _filing_text(snapshot) == "mfj":
        return _YES
    return ""


def normalize_stored_marriage(snapshot: PlanningSnapshot) -> bool:
    """A past marriage year on a loaded joint plan is already married.

    Does what Yes does: file jointly and clear the year, so Run does not
    reject it before the timeline page is opened. Returns True when it wrote.
    """
    if snapshot.household_mode != "joint":
        return False
    year = _calendar_year(_year_text(snapshot))
    if year is None or year >= date.today().year:
        return False
    snapshot.set_raw(_shared_field("filing_status"), "mfj")
    snapshot.set_raw(_shared_field("filing_status_after_marriage"), "mfj")
    snapshot.set_raw(_shared_field("marriage_year"), "")
    return True


def marriage_answer_for_validation(
    snapshot: PlanningSnapshot,
    updates: list[dict[str, Any]] | None,
) -> str:
    """Answer on this submit. A missing radio means the question is still open."""
    if updates is None:
        return marriage_answer(snapshot)
    for row in updates:
        if str(row.get("key") or "") != "already_married":
            continue
        value = str(row.get("value") or "").strip().lower()
        return value if value in {_YES, _NO} else ""
    return ""


def catalog_updates_for_marriage(
    snapshot: PlanningSnapshot,
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop the virtual question, and drop a year the Yes answer is about to clear."""
    answer = marriage_answer_for_validation(snapshot, updates)
    kept: list[dict[str, Any]] = []
    for row in updates:
        key = str(row.get("key") or "")
        if key == "already_married":
            continue
        if key == "marriage_year" and answer == _YES:
            continue
        kept.append(row)
    return kept


def apply_marriage_answer(snapshot: PlanningSnapshot, updates: list[dict[str, Any]]) -> None:
    """Write filing status from the timeline question. No-op when it was not asked."""
    answer = ""
    seen = False
    for row in updates:
        if str(row.get("key") or "") != "already_married":
            continue
        seen = True
        answer = str(row.get("value") or "").strip().lower()
    if not seen or answer not in {_YES, _NO}:
        return
    if answer == _YES:
        snapshot.set_raw(_shared_field("filing_status"), "mfj")
        snapshot.set_raw(_shared_field("filing_status_after_marriage"), "mfj")
        snapshot.set_raw(_shared_field("marriage_year"), "")
        return
    snapshot.set_raw(_shared_field("filing_status"), "single")
    snapshot.set_raw(_shared_field("filing_status_before_marriage"), "single")
    snapshot.set_raw(_shared_field("filing_status_after_marriage"), "mfj")

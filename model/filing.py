"""Filing status resolution — marriage transitions and per-person overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from model.taxes import VALID_FILING_STATUSES, normalize_filing_status


@dataclass(frozen=True)
class YearFilingContext:
    layout: str  # joint_return | separate_returns
    label: str
    joint_filing_status: str | None
    person_filing_status: dict[str, str]


def _valid_filing(status: str, *, fallback: str) -> str:
    normalized = normalize_filing_status(status)
    if normalized in VALID_FILING_STATUSES:
        return normalized
    return fallback


def marriage_filing_defaults(params: dict[str, Any]) -> tuple[int | None, str, str]:
    marriage_year = params.get("marriage_year")
    if marriage_year in ("", None):
        marriage_year = None
    else:
        marriage_year = int(marriage_year)

    legacy = _valid_filing(
        str(params.get("filing_status", "head_of_household") or "head_of_household"),
        fallback="head_of_household",
    )
    before = _valid_filing(
        str(params.get("filing_status_before_marriage") or legacy),
        fallback=legacy,
    )
    after = _valid_filing(
        str(params.get("filing_status_after_marriage") or "mfj"),
        fallback="mfj",
    )
    return marriage_year, before, after


def person_filing_status_before_marriage(
    person: dict[str, Any],
    *,
    default: str,
) -> str:
    override = person.get("filing_status_before_marriage") or person.get("filing_status")
    if override:
        return _valid_filing(str(override), fallback=default)
    return default


def asset_shares(persons: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Portfolio ownership shares for per-person RMD and separate-filer withdrawals."""
    totals = {
        name: max(0.0, float(person.get("initial_assets", 0) or 0))
        for name, person in persons.items()
    }
    explicit = {
        name: float(person["asset_share_pct"])
        for name, person in persons.items()
        if person.get("asset_share_pct") not in (None, "")
    }
    if explicit:
        total_pct = sum(explicit.values())
        if total_pct > 0:
            return {name: explicit.get(name, 0.0) / total_pct for name in persons}

    total_assets = sum(totals.values())
    if total_assets <= 0:
        share = 1.0 / len(persons)
        return {name: share for name in persons}
    return {name: totals[name] / total_assets for name in persons}


def _filing_label(layout: str, person_status: dict[str, str], joint_status: str | None) -> str:
    if layout == "joint_return" and joint_status:
        return joint_status
    statuses = [person_status[name] for name in sorted(person_status)]
    if len(set(statuses)) == 1:
        if len(statuses) > 1:
            return f"{statuses[0]}×{len(statuses)}"
        return statuses[0]
    return "+".join(f"{name}:{person_status[name]}" for name in sorted(person_status))


def apply_survivor_filing(
    filing: YearFilingContext,
    *,
    household_mode: bool,
    living_count: int,
    survivor_status: str,
    living_names: list[str],
) -> YearFilingContext:
    """Switch to survivor filing when exactly one household member is alive."""
    if not household_mode or living_count != 1 or len(living_names) != 1:
        return filing
    status = _valid_filing(survivor_status, fallback="single")
    name = living_names[0]
    return YearFilingContext(
        layout="joint_return",
        label=f"survivor_{status}",
        joint_filing_status=status,
        person_filing_status={name: status},
    )


def resolve_year_filing(
    *,
    year: int,
    marriage_year: int | None,
    filing_before: str,
    filing_after: str,
    persons: dict[str, dict[str, Any]],
    household_mode: bool,
    legacy_filing_status: str,
) -> YearFilingContext:
    """Choose joint vs separate tax returns and filing status for a projection year."""
    if marriage_year is None:
        status = legacy_filing_status
        if household_mode and len(persons) > 1:
            return YearFilingContext(
                layout="joint_return",
                label=status,
                joint_filing_status=status,
                person_filing_status={name: status for name in persons},
            )
        return YearFilingContext(
            layout="separate_returns",
            label=status,
            joint_filing_status=None,
            person_filing_status={name: status for name in persons},
        )

    married = year >= marriage_year
    if household_mode and len(persons) > 1 and not married:
        person_status = {
            name: person_filing_status_before_marriage(person, default=filing_before)
            for name, person in persons.items()
        }
        return YearFilingContext(
            layout="separate_returns",
            label=_filing_label("separate_returns", person_status, None),
            joint_filing_status=None,
            person_filing_status=person_status,
        )

    status = filing_after if married else filing_before
    if household_mode and len(persons) > 1 and married:
        return YearFilingContext(
            layout="joint_return",
            label=status,
            joint_filing_status=status,
            person_filing_status={name: status for name in persons},
        )

    person_status = {
        name: (
            person_filing_status_before_marriage(person, default=filing_before)
            if not married
            else filing_after
        )
        for name, person in persons.items()
    }
    return YearFilingContext(
        layout="separate_returns",
        label=_filing_label("separate_returns", person_status, None),
        joint_filing_status=None,
        person_filing_status=person_status,
    )
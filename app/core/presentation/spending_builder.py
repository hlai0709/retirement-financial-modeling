"""Retirement spending category builder — sums into Base Expenses (+ mortgage field)."""

from __future__ import annotations

from typing import Any

from app.core.planning_snapshot import PlanningSnapshot
from config.field_values import format_field_value, parse_field_value
from config.model_fields import FIELDS_BY_KEY
from services.data_service import DataService

# Category id → display. Annual USD amounts in the UI.
SPENDING_CATEGORIES: tuple[dict[str, str], ...] = (
    {
        "id": "housing_rent",
        "label": "Rent / HOA (ongoing)",
        "hint": "Monthly rent or HOA × 12. Mortgage is a separate line below.",
    },
    {
        "id": "mortgage",
        "label": "Mortgage (until paid off)",
        "hint": "From mortgage statements when imported. Not part of Base Expenses.",
    },
    {
        "id": "utilities",
        "label": "Utilities",
        "hint": "Electric, gas, water, internet, phone, trash — typically permanent.",
    },
    {
        "id": "property_tax",
        "label": "Property tax (if not in escrow)",
        "hint": "Annual property tax when paid outside the mortgage escrow.",
    },
    {
        "id": "food",
        "label": "Food & groceries",
        "hint": "Groceries and household staples.",
    },
    {
        "id": "dining",
        "label": "Dining out",
        "hint": "Restaurants, coffee, delivery — edit down for retirement if you like.",
    },
    {
        "id": "transportation",
        "label": "Transportation",
        "hint": "Fuel, transit, rideshare. Car purchases are one-offs — leave out of base.",
    },
    {
        "id": "healthcare",
        "label": "Healthcare (premiums + OOP)",
        "hint": "Insurance premiums and typical out-of-pocket. Can pre-fill from pay stub / employer health.",
    },
    {
        "id": "insurance_other",
        "label": "Other insurance",
        "hint": "Life, umbrella, renters/home (if not in mortgage escrow).",
    },
    {
        "id": "education",
        "label": "Education / tuition",
        "hint": "Private school, college — often ends before or early in retirement; edit or zero out.",
    },
    {
        "id": "entertainment",
        "label": "Entertainment",
        "hint": "Hobbies, streaming, movies — recurring lifestyle, not one trip.",
    },
    {
        "id": "travel",
        "label": "Travel (typical annual)",
        "hint": "Average yearly travel. Big one-time trips shouldn’t inflate this forever.",
    },
    {
        "id": "shopping",
        "label": "Shopping & personal",
        "hint": "Clothes, household goods, personal care.",
    },
    {
        "id": "subscriptions",
        "label": "Subscriptions",
        "hint": "Software, memberships, recurring digital services.",
    },
    {
        "id": "other",
        "label": "Other / buffer",
        "hint": "Misc. contingency. Prefer putting named costs in categories above.",
    },
)

# Map Plaid expense analyzer categories → builder categories
_PLAID_TO_BUILDER: dict[str, str] = {
    "mortgage": "mortgage",
    "utilities": "utilities",
    "insurance": "insurance_other",
    "subscriptions": "subscriptions",
    "food": "food",
    "dining": "dining",
    "transportation": "transportation",
    "healthcare": "healthcare",
    "education": "education",
    "property_tax": "property_tax",
    "travel": "travel",
    "discretionary": "entertainment",
    "other": "other",
}

# What counts toward Base Expenses (retirement living). Mortgage is separate.
# "other" / education are tracked in the builder but ignored for Base for now.
BASE_RECURRING_CATEGORIES: frozenset[str] = frozenset(
    {
        "utilities",
        "insurance",
        "insurance_other",
        "subscriptions",
        "healthcare",
        "property_tax",
        "transportation",
    }
)
BASE_LIFESTYLE_CATEGORIES: frozenset[str] = frozenset(
    {
        "food",
        "dining",
        "entertainment",
        "travel",
    }
)
BASE_EXPENSE_CATEGORIES: frozenset[str] = BASE_RECURRING_CATEGORIES | BASE_LIFESTYLE_CATEGORIES


def counts_toward_base(category_id: str) -> bool:
    """Base living excludes healthcare, mortgage, and property tax.

    Mortgage and property tax have their own plan fields. The projection adds
    those on top of base, so folding them into Base Expenses would count twice.
    """
    if category_id in {"healthcare", "property_tax"}:
        return False
    return category_id in BASE_EXPENSE_CATEGORIES or category_id == "housing_rent"
IGNORE_FOR_BASE_CATEGORIES: frozenset[str] = frozenset(
    {
        "mortgage",
        "other",
        "education",
        "housing_rent",
        "shopping",
    }
)

# When suggesting Go-Go / Slow-Go / No-Go % from spending mix:
# permanent costs stay; lifestyle (food/dining/entertainment/travel) steps down.
# Mortgage is NOT in these % — it is added separately until payoff year.
_SLOW_GO_LIFESTYLE_FACTOR = 0.70
_NO_GO_LIFESTYLE_FACTOR = 0.40


def spending_mix_from_amounts(amounts: dict[str, Any]) -> dict[str, float]:
    """Split category amounts into permanent vs lifestyle dollars."""
    permanent = 0.0
    lifestyle = 0.0
    for cid, raw in amounts.items():
        val = _money(raw)
        if val <= 0:
            continue
        if cid in BASE_LIFESTYLE_CATEGORIES:
            lifestyle += val
        elif cid in BASE_RECURRING_CATEGORIES or cid == "housing_rent":
            permanent += val
    return {
        "permanent": round(permanent, 2),
        "lifestyle": round(lifestyle, 2),
        "base": round(permanent + lifestyle, 2),
    }


def suggest_expense_phase_pcts_from_amounts(amounts: dict[str, Any]) -> dict[str, Any]:
    """Derive Go-Go / Slow-Go / No-Go % from permanent vs lifestyle spending.

    go_go ≈ 100% of base
    slow_go ≈ (permanent + 70% lifestyle) / base
    no_go ≈ (permanent + 40% lifestyle) / base

    Mortgage is excluded from this mix (paid until its own payoff year).
    """
    mix = spending_mix_from_amounts(amounts)
    base = mix["base"]
    if base <= 0:
        return {
            "ok": False,
            "message": (
                "Need Base living categories filled (bills + food/dining/entertainment/travel) "
                "before suggesting phase percentages."
            ),
            **mix,
        }
    permanent = mix["permanent"]
    lifestyle = mix["lifestyle"]
    go_go = 100.0
    slow_go = 100.0 * (permanent + _SLOW_GO_LIFESTYLE_FACTOR * lifestyle) / base
    no_go = 100.0 * (permanent + _NO_GO_LIFESTYLE_FACTOR * lifestyle) / base
    # Keep a sensible floor so No-Go never drops below permanent share
    permanent_share = 100.0 * permanent / base
    slow_go = max(permanent_share, min(100.0, round(slow_go, 1)))
    no_go = max(permanent_share, min(slow_go, round(no_go, 1)))
    return {
        "ok": True,
        "expense_go_go_pct": go_go,
        "expense_slow_go_pct": slow_go,
        "expense_no_go_pct": no_go,
        "expense_phase_mode": "age_banded",
        "permanent_share_pct": round(permanent_share, 1),
        "lifestyle_share_pct": round(100.0 * lifestyle / base, 1),
        **mix,
        "message": (
            f"From your mix (permanent ${permanent:,.0f} + lifestyle ${lifestyle:,.0f}): "
            f"Go-Go {go_go:g}%, Slow-Go {slow_go:g}%, No-Go {no_go:g}%. "
            "Mortgage stays separate until payoff."
        ),
    }


def apply_suggested_expense_phase_pcts(
    snapshot: PlanningSnapshot,
    amounts: dict[str, Any] | None = None,
    *,
    person: str = "self",
) -> dict[str, Any]:
    """Write suggested phase % into the plan (shared fields) and enable age_banded mode."""
    person = person if person in {"self", "wife"} else "self"
    if amounts is None:
        amounts = dict((getattr(snapshot, "spending_breakdown", None) or {}).get(person) or {})
        if not any(_money(v) > 0 for v in amounts.values()):
            # Fall back to rebuilding from current builder seed
            built = build_spending_builder(snapshot, person=person)
            amounts = {c["id"]: c["annual"] for c in built.get("categories") or []}
    suggestion = suggest_expense_phase_pcts_from_amounts(amounts)
    if not suggestion.get("ok"):
        return suggestion

    mode_field = FIELDS_BY_KEY.get("expense_phase_mode")
    if mode_field is not None:
        snapshot.set_raw(mode_field, "age_banded")
        snapshot.set_source(None, "expense_phase_mode", "Derived from spending mix")

    for key in ("expense_go_go_pct", "expense_slow_go_pct", "expense_no_go_pct"):
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            continue
        pct = float(suggestion[key])
        snapshot.set_raw(field, format_field_value(field, pct / 100.0))
        snapshot.set_source(None, key, "Derived from spending mix")

    return suggestion


def _money(raw: Any) -> float:
    try:
        return max(0.0, float(raw or 0))
    except (TypeError, ValueError):
        return 0.0


def _autofill_from_plan(snapshot: PlanningSnapshot, person: str) -> dict[str, float]:
    """Seed categories from known plan fields."""
    values: dict[str, float] = {c["id"]: 0.0 for c in SPENDING_CATEGORIES}
    persons = snapshot.persons.get(person) or {}

    mortgage = _money(persons.get("mortgage_annual"))
    if mortgage > 0:
        values["mortgage"] = mortgage

    property_tax = _money(persons.get("property_tax_annual"))
    if property_tax > 0:
        values["property_tax"] = property_tax

    # Employer health monthly → annual healthcare seed
    mode = str(persons.get("employer_health_mode") or "none").lower()
    monthly = _money(persons.get("employer_health_monthly"))
    if mode in {"employer", "cobra"} and monthly > 0:
        values["healthcare"] = monthly * 12.0
    else:
        pre = _money(persons.get("health_insurance_pre_medicare"))
        if pre > 0:
            values["healthcare"] = pre

    return values


def sync_mortgage_into_spending_breakdown(
    snapshot: PlanningSnapshot,
    *,
    person: str | None = "self",
) -> None:
    """Keep Spending builder mortgage line aligned with Mortgage Annual plan field."""
    person = person if person in {"self", "wife"} else "self"
    persons = snapshot.persons.get(person) or {}
    mortgage = _money(persons.get("mortgage_annual"))
    if not hasattr(snapshot, "spending_breakdown") or snapshot.spending_breakdown is None:
        snapshot.spending_breakdown = {}
    bucket = dict(snapshot.spending_breakdown.get(person) or {})
    if mortgage > 0:
        bucket["mortgage"] = round(mortgage, 2)
    elif "mortgage" in bucket:
        bucket["mortgage"] = 0.0
    snapshot.spending_breakdown[person] = bucket


def sync_property_tax_into_spending_breakdown(
    snapshot: PlanningSnapshot,
    *,
    person: str | None = "self",
) -> None:
    """Keep Spending builder property-tax line aligned with Property Tax Annual."""
    person = person if person in {"self", "wife"} else "self"
    persons = snapshot.persons.get(person) or {}
    amount = _money(persons.get("property_tax_annual"))
    if not hasattr(snapshot, "spending_breakdown") or snapshot.spending_breakdown is None:
        snapshot.spending_breakdown = {}
    bucket = dict(snapshot.spending_breakdown.get(person) or {})
    bucket["property_tax"] = round(amount, 2) if amount > 0 else 0.0
    snapshot.spending_breakdown[person] = bucket


def sync_expense_rows_into_spending_breakdown(
    snapshot: PlanningSnapshot,
    rows: list[dict[str, Any]],
) -> None:
    """Update categories the review actually mentioned. Leave the rest alone.

    Mortgage and property tax stay on their plan fields. A category with no
    rows (rent the user typed, a bill this export did not see) is not zeroed.
    """
    if not hasattr(snapshot, "spending_breakdown") or snapshot.spending_breakdown is None:
        snapshot.spending_breakdown = {}
    sums: dict[str, dict[str, float]] = {"self": {}, "wife": {}}
    seen: dict[str, set[str]] = {"self": set(), "wife": set()}
    for row in rows:
        owner = str(row.get("owner") or "")
        if owner not in sums:
            continue
        cat = str(row.get("category") or "other").lower()
        if cat == "mortgage":
            continue
        builder_id = _PLAID_TO_BUILDER.get(cat, "other")
        seen[owner].add(builder_id)
        if not row.get("included"):
            continue
        sums[owner][builder_id] = sums[owner].get(builder_id, 0.0) + _money(
            row.get("annual_amount")
        )
    for person in ("self", "wife"):
        existing = dict(snapshot.spending_breakdown.get(person) or {})
        for cid in seen[person]:
            existing[cid] = round(sums[person].get(cid, 0.0), 2)
        person_fields = snapshot.persons.get(person) or {}
        mortgage = _money(person_fields.get("mortgage_annual"))
        if mortgage <= 0:
            mortgage = _money(existing.get("mortgage"))
        existing["mortgage"] = round(mortgage, 2) if mortgage > 0 else 0.0
        plan_tax = _money(person_fields.get("property_tax_annual"))
        if "property_tax" not in seen[person] and plan_tax > 0:
            existing["property_tax"] = round(plan_tax, 2)
        snapshot.spending_breakdown[person] = existing
        if "property_tax" in seen[person]:
            prop_field = FIELDS_BY_KEY.get("property_tax_annual")
            if prop_field is not None:
                amount = _money(existing.get("property_tax"))
                snapshot.set_raw(
                    prop_field,
                    format_field_value(prop_field, amount),
                    person=person,
                )
                snapshot.set_source(
                    person,
                    "property_tax_annual",
                    "Expense review" if amount > 0 else "Manual",
                )


def copy_healthcare_from_expense_rows(
    snapshot: PlanningSnapshot,
    rows: list[dict[str, Any]],
) -> dict[str, float]:
    """Put checked healthcare into the premium field when that field is still blank."""
    totals = {"self": 0.0, "wife": 0.0}
    for row in rows:
        if not row.get("included"):
            continue
        cat = str(row.get("category") or "").lower()
        builder_id = _PLAID_TO_BUILDER.get(cat, cat)
        if builder_id != "healthcare" and cat != "healthcare":
            continue
        owner = str(row.get("owner") or "")
        if owner in totals:
            totals[owner] += _money(row.get("annual_amount"))
    written: dict[str, float] = {}
    field = FIELDS_BY_KEY.get("health_insurance_pre_medicare")
    if field is None:
        return written
    for person, amount in totals.items():
        if amount <= 0:
            continue
        person_fields = snapshot.persons.get(person) or {}
        if _money(person_fields.get("health_insurance_pre_medicare")) > 0:
            continue
        if _money(person_fields.get("health_insurance_post_retire")) > 0:
            continue
        snapshot.set_raw(field, format_field_value(field, amount), person=person)
        snapshot.set_source(person, field.key, "Expense review")
        written[person] = round(amount, 2)
    return written


def _autofill_from_plaid_expenses(snapshot: PlanningSnapshot, person: str) -> dict[str, float]:
    """Seed from Plaid: recurring included lines only (skip one-offs / unchecked)."""
    values: dict[str, float] = {c["id"]: 0.0 for c in SPENDING_CATEGORIES}
    try:
        cached = DataService().load_cached_expense_snapshot()
    except Exception:
        cached = None
    if not cached or not cached.analysis:
        return values

    plan_has_mortgage = _money((snapshot.persons.get(person) or {}).get("mortgage_annual")) > 0

    for item in cached.analysis.items or []:
        if not getattr(item, "included", True):
            continue
        owner = str(getattr(item, "owner", "") or "")
        if owner == "exclude":
            continue
        # Person-tagged lines; unassigned folds into self
        if owner in {"self", "wife"} and owner != person:
            continue
        if owner in {"unassigned", ""} and person != "self":
            continue
        annual = _money(getattr(item, "annual_amount", 0))
        if annual <= 0:
            continue
        cat = str(getattr(item, "category", "other") or "other").lower()
        if cat == "mortgage" and plan_has_mortgage:
            continue
        builder_id = _PLAID_TO_BUILDER.get(cat, "other")
        expense_type = str(getattr(item, "expense_type", "") or "")
        if expense_type != "recurring":
            lifestyle = builder_id in {"food", "dining", "entertainment"}
            recurring_bill = builder_id in BASE_RECURRING_CATEGORIES
            if builder_id == "travel" or not (lifestyle or recurring_bill):
                continue
        values[builder_id] = values.get(builder_id, 0.0) + annual

    return values


def build_spending_builder(
    snapshot: PlanningSnapshot,
    *,
    person: str = "self",
) -> dict[str, Any]:
    person = person if person in {"self", "wife"} else "self"
    saved = dict((getattr(snapshot, "spending_breakdown", None) or {}).get(person) or {})

    plan_seed = _autofill_from_plan(snapshot, person)
    plaid_seed = _autofill_from_plaid_expenses(snapshot, person)
    # Start from Plaid, then let explicit plan fields (mortgage / health) win when set
    merged: dict[str, float] = {c["id"]: 0.0 for c in SPENDING_CATEGORIES}
    for key, amount in plaid_seed.items():
        if key in merged:
            merged[key] = _money(amount)
    for key, amount in plan_seed.items():
        if key in merged and _money(amount) > 0:
            merged[key] = _money(amount)

    # Saved user edits win when present — except Mortgage / Property tax, which
    # track dedicated plan fields so document imports are not hidden by a stale save.
    amounts: dict[str, float] = {}
    sources: dict[str, str] = {}
    plan_mortgage = _money(plan_seed.get("mortgage"))
    plan_property_tax = _money(plan_seed.get("property_tax"))
    mortgage_source = str(snapshot.get_source(person, "mortgage_annual") or "")
    property_tax_source = str(snapshot.get_source(person, "property_tax_annual") or "")
    for cat in SPENDING_CATEGORIES:
        cid = cat["id"]
        if cid == "mortgage" and plan_mortgage > 0:
            amounts[cid] = plan_mortgage
            if mortgage_source and mortgage_source != "Manual":
                sources[cid] = mortgage_source
            else:
                sources[cid] = "From your plan"
            continue
        if cid == "property_tax" and plan_property_tax > 0:
            amounts[cid] = plan_property_tax
            if property_tax_source and property_tax_source != "Manual":
                sources[cid] = property_tax_source
            else:
                sources[cid] = "From your plan"
            continue
        if cid in saved and saved[cid] not in (None, ""):
            amounts[cid] = _money(saved[cid])
            sources[cid] = "Saved breakdown"
        elif merged.get(cid, 0) > 0:
            amounts[cid] = merged[cid]
            if plan_seed.get(cid, 0) > 0 and abs(merged[cid] - plan_seed[cid]) < 0.02:
                sources[cid] = "From your plan"
            elif plaid_seed.get(cid, 0) > 0:
                sources[cid] = "From Plaid expenses"
            else:
                sources[cid] = "Suggested"
        else:
            amounts[cid] = 0.0
            sources[cid] = ""

    rows = []
    base_total = 0.0
    mortgage_total = 0.0
    property_tax_total = 0.0
    for cat in SPENDING_CATEGORIES:
        cid = cat["id"]
        annual = amounts.get(cid, 0.0)
        if cid == "mortgage":
            mortgage_total += annual
        elif cid == "property_tax":
            property_tax_total += annual
        elif counts_toward_base(cid):
            base_total += annual
        rows.append(
            {
                "id": cid,
                "label": cat["label"],
                "hint": cat["hint"],
                "annual": round(annual, 2),
                "monthly": round(annual / 12.0, 2) if annual else 0.0,
                "source": sources.get(cid) or "",
                "to_base": counts_toward_base(cid),
            }
        )

    current_base = snapshot.get_raw(FIELDS_BY_KEY["base_expenses"], person=person)
    try:
        current_base_val = float(parse_field_value(FIELDS_BY_KEY["base_expenses"], current_base) or 0)
    except Exception:
        current_base_val = 0.0

    return {
        "person": person,
        "person_label": "You" if person == "self" else "Spouse",
        "categories": rows,
        "base_total": round(base_total, 2),
        "mortgage_total": round(mortgage_total, 2),
        "property_tax_total": round(property_tax_total, 2),
        "grand_total": round(base_total + mortgage_total + property_tax_total, 2),
        "current_base_expenses": current_base_val,
        "mortgage_payoff_year": str(
            (snapshot.persons.get(person) or {}).get("mortgage_payoff_year") or ""
        ),
        "notes": [
            "Base Expenses = recurring bills + food + dining + entertainment + travel (+ rent).",
            "Other / education / shopping are shown for editing but not added into Base for now.",
            "Mortgage is separate (Mortgage Annual until payoff year).",
            "Property tax is separate. The projection adds Property Tax Annual on its own.",
            "Amounts are annual USD. Edit monthly or annual — they stay in sync in the UI.",
        ],
    }


# Lifestyle categories folded into Base Expenses (averages from card/bank spend)
LIFESTYLE_AI_CATEGORIES: tuple[str, ...] = ("food", "dining", "entertainment", "travel")
_AI_CATEGORY_ALIASES: dict[str, str] = {
    "groceries": "food",
    "grocery": "food",
    "food_and_drink": "food",
    "restaurants": "dining",
    "restaurant": "dining",
    "coffee": "dining",
    "discretionary": "entertainment",
    "fun": "entertainment",
    "movies": "entertainment",
    "misc": "other",
    "miscellaneous": "other",
}


def _llm_ready() -> tuple[dict[str, Any] | None, Any, str, Any]:
    """Return (error_payload | None, settings, api_key, resolved helpers)."""
    from providers.assumption_research import _complete_with_retry
    from providers.llm.factory import get_llm_provider
    from providers.llm.models_catalog import resolve_model_choice
    from security.llm_keychain import get_api_key
    from settings import load_llm_settings

    settings = load_llm_settings()
    if not settings.configured or settings.provider == "none":
        return (
            {
                "ok": False,
                "message": "Configure LLM Settings (provider + API key) first.",
            },
            None,
            "",
            None,
        )
    api_key = get_api_key(settings.provider)
    if not api_key:
        return (
            {
                "ok": False,
                "message": f"Add an API key for {settings.provider} in LLM Settings first.",
            },
            None,
            "",
            None,
        )
    return None, settings, api_key, (_complete_with_retry, get_llm_provider, resolve_model_choice)


_CATEGORIZE_BATCH_SIZE = 35
_CATEGORIZE_BATCH_SIZE_LOCAL = 20


def _backend_status(*, backend: str, model: str) -> dict[str, str]:
    if backend == "local":
        from providers.local_doc_ai import host_label

        label = host_label() if not model else f"http://127.0.0.1:11434 · {model}"
        return {
            "backend": "local",
            "backend_label": f"Local Ollama ({label})",
            "model": model,
        }
    return {
        "backend": "cloud",
        "backend_label": f"Cloud API ({backend}/{model})" if backend else f"Cloud API ({model})",
        "model": model,
    }


def _parse_llm_category_array(content: str) -> list[Any]:
    """Extract a JSON array from model output (fences, wrappers, mild truncation)."""
    import json
    import re

    stripped = (content or "").strip()
    if not stripped:
        raise ValueError("empty model response")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    # Direct array
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("items", "categories", "rows", "results", "data"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
    except json.JSONDecodeError:
        pass
    # Slice first [...] block (end may be missing if the model truncated output)
    start = stripped.find("[")
    if start >= 0:
        end = stripped.rfind("]")
        chunk = stripped[start : end + 1] if end > start else stripped[start:]
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        # Truncated / messy array: keep complete {...} objects
        objects = re.findall(r"\{[^{}]*\}", chunk)
        recovered: list[Any] = []
        for raw_obj in objects:
            try:
                obj = json.loads(raw_obj)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                recovered.append(obj)
        if recovered:
            return recovered
    # Object wrapper with embedded array
    start_o = stripped.find("{")
    end_o = stripped.rfind("}")
    if start_o >= 0 and end_o > start_o:
        try:
            obj = json.loads(stripped[start_o : end_o + 1])
            if isinstance(obj, dict):
                for key in ("items", "categories", "rows", "results", "data"):
                    if isinstance(obj.get(key), list):
                        return obj[key]
        except json.JSONDecodeError:
            pass
    raise ValueError("no JSON array found in model response")


def _normalize_ai_category(raw: str, *, allowed: set[str]) -> str:
    cat = _AI_CATEGORY_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if cat == "discretionary":
        cat = "entertainment"
    if cat not in allowed:
        return "other"
    return cat


def ai_categorize_expense_rows(
    rows: list[dict[str, Any]],
    *,
    focus_lifestyle: bool = True,
    backend: str = "local",
) -> dict[str, Any]:
    """Categorize merchants via local Ollama or cloud API (caller picks backend)."""
    import json

    from providers.expense_analyzer import EXPENSE_CATEGORIES
    from providers.local_doc_ai import DEFAULT_MODEL as LOCAL_MODEL
    from providers.local_doc_ai import local_ai_available, local_chat

    choice = (backend or "local").strip().lower()
    if choice not in {"local", "cloud"}:
        choice = "local"

    allowed = list(EXPENSE_CATEGORIES)
    allowed_set = set(allowed)
    lifestyle = list(LIFESTYLE_AI_CATEGORIES)
    payload_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        merchant = str(row.get("merchant") or "").strip()
        if not merchant:
            continue
        payload_rows.append(
            {
                "i": idx,
                "merchant": merchant[:80],
                "annual": round(_money(row.get("annual_amount")), 2),
                "current": str(row.get("category") or "other"),
            }
        )
    if not payload_rows:
        return {"ok": False, "message": "No expense merchants to categorize."}

    cloud_settings = None
    cloud_helpers = None
    cloud_key = ""
    model = LOCAL_MODEL
    provider_name = "ollama"

    if choice == "local":
        if not local_ai_available():
            return {
                "ok": False,
                "message": (
                    "Local Ollama is not running on 127.0.0.1:11434. "
                    "Start Ollama, or choose Cloud API for this job."
                ),
                **_backend_status(backend="local", model=LOCAL_MODEL),
            }
        model = LOCAL_MODEL
        batch_size = _CATEGORIZE_BATCH_SIZE_LOCAL
    else:
        err, settings, api_key, helpers = _llm_ready()
        if err:
            err.update(_backend_status(backend="cloud", model=""))
            return err
        assert settings is not None and helpers is not None
        cloud_settings, cloud_helpers, cloud_key = settings, helpers, api_key
        complete_with_retry, get_llm_provider, resolve_model_choice = helpers
        try:
            model = resolve_model_choice(settings.provider, settings.model, api_key=api_key)
            provider_name = settings.provider
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Cloud LLM setup failed: {exc}",
                **_backend_status(backend=settings.provider, model=settings.model or ""),
            }
        batch_size = _CATEGORIZE_BATCH_SIZE

    system = (
        "You categorize consumer merchants for household budgeting. "
        "Return ONLY a JSON array of objects like "
        '[{"i":0,"category":"food"},{"i":1,"category":"dining"}]. '
        "No markdown fences, no commentary. "
        "Prefer these lifestyle categories when the merchant is day-to-day spend: "
        f"{json.dumps(lifestyle)} where food=groceries/supermarket, dining=restaurants/coffee/delivery, "
        "entertainment=streaming/hobbies/movies/games (not travel airlines), other=unclear misc. "
        "Keep clear non-lifestyle categories unchanged when obvious "
        "(utilities, insurance, subscriptions, healthcare, education, transportation, travel, "
        "property_tax, mortgage). Never invent merchants. Cover every index i given."
    )
    if focus_lifestyle:
        system += (
            " For ambiguous merchants currently labeled other/discretionary, choose food, dining, "
            "entertainment, or other."
        )

    llm = None
    complete_with_retry = None
    if choice == "cloud":
        assert cloud_settings is not None and cloud_helpers is not None
        complete_with_retry, get_llm_provider, _resolve = cloud_helpers
        try:
            llm = get_llm_provider(cloud_settings.provider, cloud_key)
        except Exception as exc:
            return {
                "ok": False,
                "message": f"AI categorize failed: {exc}",
                **_backend_status(backend=provider_name, model=model),
            }

    by_index: dict[int, str] = {}
    batches = 0
    last_error = ""
    for offset in range(0, len(payload_rows), batch_size):
        batch = payload_rows[offset : offset + batch_size]
        user = (
            f"Allowed category ids: {json.dumps(allowed)}\n"
            f"Merchants (batch {batches + 1}):\n{json.dumps(batch)}\n"
            "Return a JSON array covering every index i in this batch."
        )
        max_tokens = min(8192, max(1024, 80 * len(batch) + 256))
        try:
            if choice == "local":
                content = local_chat(
                    system=system,
                    user=user,
                    model=model,
                    num_predict=max_tokens,
                    num_ctx=8192,
                    temperature=0.0,
                )
            else:
                assert complete_with_retry is not None and llm is not None
                completion = complete_with_retry(
                    llm,
                    system=system,
                    user=user,
                    model=model,
                    max_tokens=max_tokens,
                )
                content = str(getattr(completion, "content", "") or "")
            parsed = _parse_llm_category_array(content)
        except Exception as exc:
            last_error = str(exc)
            continue
        batches += 1
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("i"))
            except (TypeError, ValueError):
                continue
            raw_cat = str(entry.get("category") or "")
            by_index[idx] = _normalize_ai_category(raw_cat, allowed=allowed_set)

    status = _backend_status(
        backend="local" if choice == "local" else provider_name,
        model=model,
    )
    if not by_index:
        hint = f" Last error: {last_error}" if last_error else ""
        return {
            "ok": False,
            "message": (
                "AI returned empty or unreadable JSON for categories. "
                "Try the other backend (Local vs Cloud), or categorize a few rows manually."
                f"{hint}"
            ),
            **status,
        }

    changed = 0
    out_rows: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        next_row = dict(row)
        if idx in by_index:
            new_cat = by_index[idx]
            if str(row.get("category") or "") != new_cat:
                changed += 1
            next_row["category"] = new_cat
            updates.append(
                {
                    "merchant": row.get("merchant"),
                    "category": new_cat,
                    "annual_amount": row.get("annual_amount"),
                }
            )
        out_rows.append(next_row)

    return {
        "ok": True,
        "rows": out_rows,
        "updates": updates,
        "changed": changed,
        "batches": batches,
        "message": (
            f"{status['backend_label']}: categorized {len(by_index)} of {len(payload_rows)} "
            f"merchant(s) in {batches} batch(es) ({changed} changed). Review, then Apply."
        ),
        "provider": provider_name,
        **status,
    }


def fill_lifestyle_spending_from_expenses_ai(
    snapshot: PlanningSnapshot,
    *,
    person: str = "self",
    backend: str = "local",
) -> dict[str, Any]:
    """AI-categorize Plaid expense lines, then return food/dining/entertainment/other annuals."""
    person = person if person in {"self", "wife"} else "self"
    try:
        cached = DataService().load_cached_expense_snapshot()
    except Exception:
        cached = None
    if not cached or not cached.analysis or not cached.analysis.items:
        return {
            "ok": False,
            "message": "No Plaid expenses on file. Refresh expenses first.",
        }

    rows: list[dict[str, Any]] = []
    for item in cached.analysis.items:
        owner = str(item.owner or "")
        if owner == "exclude":
            continue
        if owner in {"self", "wife"} and owner != person:
            continue
        if owner in {"unassigned", ""} and person != "self":
            continue
        if not item.included and item.expense_type != "recurring":
            # Still send recurring even if unchecked so AI can classify; include both
            pass
        rows.append(
            {
                "merchant": item.merchant,
                "category": item.category,
                "annual_amount": item.annual_amount,
                "monthly_amount": item.monthly_amount,
                "expense_type": item.expense_type,
                "included": item.included,
                "owner": item.owner,
            }
        )
    if not rows:
        return {"ok": False, "message": "No expense lines for this person."}

    categorized = ai_categorize_expense_rows(rows, focus_lifestyle=True, backend=backend)
    if not categorized.get("ok"):
        return categorized

    # Persist AI categories onto cached expense snapshot
    cat_by_merchant = {
        str(u.get("merchant") or ""): str(u.get("category") or "")
        for u in categorized.get("updates") or []
    }
    for item in cached.analysis.items:
        new_cat = cat_by_merchant.get(item.merchant)
        if new_cat:
            item.category = new_cat
    try:
        DataService().save_expense_snapshot(cached)
    except Exception:
        pass

    amounts: dict[str, float] = {cid: 0.0 for cid in LIFESTYLE_AI_CATEGORIES}
    used = 0
    for row in categorized.get("rows") or []:
        # Prefer recurring + included for retirement base; also include included variable lifestyle
        included = bool(row.get("included", True))
        expense_type = str(row.get("expense_type") or "")
        cat = str(row.get("category") or "other")
        if cat not in amounts:
            continue
        if not included and expense_type != "recurring":
            continue
        if not included and expense_type == "recurring":
            # Recurring but unchecked — still use for lifestyle fill (user can edit)
            pass
        # Use recurring always for lifestyle; included variable lifestyle too
        if expense_type == "variable" and not included:
            continue
        if expense_type == "variable" and cat == "other":
            # skip large unchecked other; if included, allow
            if not included:
                continue
        annual = _money(row.get("annual_amount"))
        if annual <= 0:
            continue
        amounts[cat] += annual
        used += 1

    amounts = {k: round(v, 2) for k, v in amounts.items() if v > 0}
    if not amounts:
        return {
            "ok": False,
            "message": (
                "AI categorized merchants, but nothing mapped into food/dining/"
                "entertainment/other with usable amounts. Check Include flags."
            ),
            "rows": categorized.get("rows"),
        }

    # Merge into spending breakdown preview (caller fills UI; optional soft-save)
    if not hasattr(snapshot, "spending_breakdown") or snapshot.spending_breakdown is None:
        snapshot.spending_breakdown = {}
    bucket = dict(snapshot.spending_breakdown.get(person) or {})
    for cid, val in amounts.items():
        bucket[cid] = val
    snapshot.spending_breakdown[person] = bucket

    monthly = {k: round(v / 12.0, 2) for k, v in amounts.items()}
    return {
        "ok": True,
        "amounts": amounts,
        "monthly": monthly,
        "categories_filled": list(amounts.keys()),
        "merchants_used": used,
        "changed": categorized.get("changed", 0),
        "message": (
            f"{categorized.get('backend_label') or 'AI'}: filled {', '.join(amounts.keys())} "
            f"from {used} expense line(s) ({categorized.get('changed', 0)} re-labeled). "
            "Review monthly/annual, then Apply."
        ),
        "rows": categorized.get("rows"),
        "backend": categorized.get("backend"),
        "backend_label": categorized.get("backend_label"),
        "provider": categorized.get("provider"),
        "model": categorized.get("model"),
    }


def suggest_spending_averages_with_llm(
    snapshot: PlanningSnapshot,
    *,
    person: str = "self",
) -> dict[str, Any]:
    """Ask configured cloud LLM for typical annual spending by category (not personal docs)."""
    import json
    import re

    err, settings, api_key, helpers = _llm_ready()
    if err:
        return err
    assert settings is not None and helpers is not None
    complete_with_retry, get_llm_provider, resolve_model_choice = helpers

    person = person if person in {"self", "wife"} else "self"

    persons = snapshot.persons.get(person) or {}
    age = persons.get("current_age") or ""
    retire = persons.get("retirement_age") or ""
    income = persons.get("annual_earned_income") or ""
    state = snapshot.shared.get("state_of_residence") or snapshot.assumption_state or "US"
    household = snapshot.household_mode
    ids = [c["id"] for c in SPENDING_CATEGORIES]
    labels = {c["id"]: c["label"] for c in SPENDING_CATEGORIES}

    system = (
        "You estimate typical U.S. household annual spending by category for retirement planning. "
        "Return ONLY a JSON object mapping category ids to annual USD numbers (no $). "
        "Use regional cost-of-living judgment from the state code. Do not invent personal facts. "
        "If mortgage/rent is unknown, use a moderate regional average for homeowners or renters. "
        "Healthcare should reflect typical premiums + OOP for that age band."
    )
    user = (
        f"Household mode: {household}. Person: {person}. State: {state}. "
        f"Current age: {age}. Retirement age: {retire}. Current earned income: {income}.\n"
        f"Categories (id → meaning): {json.dumps(labels)}\n"
        f"Return JSON with exactly these keys: {json.dumps(ids)}.\n"
        "Values are annual dollars for a typical household in this situation."
    )

    try:
        model = resolve_model_choice(settings.provider, settings.model, api_key=api_key)
        llm = get_llm_provider(settings.provider, api_key)
        completion = complete_with_retry(llm, system=system, user=user, model=model)
        content = str(getattr(completion, "content", "") or "")
    except Exception as exc:
        return {"ok": False, "message": f"AI averages failed: {exc}"}

    start = content.find("{")
    end = content.rfind("}") + 1
    if start < 0 or end <= start:
        return {"ok": False, "message": "AI returned no JSON object for spending averages."}
    try:
        parsed = json.loads(content[start:end])
    except json.JSONDecodeError:
        return {"ok": False, "message": "AI returned invalid JSON for spending averages."}
    if not isinstance(parsed, dict):
        return {"ok": False, "message": "AI JSON was not an object."}

    amounts: dict[str, float] = {}
    for cid in ids:
        raw = parsed.get(cid)
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = re.sub(r"[$,]", "", raw).strip()
        val = _money(raw)
        if val > 0:
            amounts[cid] = round(val, 2)

    if not amounts:
        return {"ok": False, "message": "AI did not return usable category amounts."}

    base = sum(v for k, v in amounts.items() if k != "mortgage")
    mortgage = amounts.get("mortgage", 0.0)
    return {
        "ok": True,
        "amounts": amounts,
        "base_total": round(base, 2),
        "mortgage_total": round(mortgage, 2),
        "grand_total": round(base + mortgage, 2),
        "message": (
            f"Filled typical averages via {settings.provider}/{model}. "
            "Review and edit before applying."
        ),
        "provider": settings.provider,
        "model": model,
    }


def migrate_base_excludes_property_tax(snapshot: PlanningSnapshot) -> bool:
    """Rewrite a Spending-builder base so property tax and healthcare stay out.

    One shot, when a saved plan has no marker. The new base is the saved
    breakdown summed with counts_toward_base. Later loads leave it alone.
    """
    if snapshot.base_excludes_property_tax:
        return False
    changed = False
    base_field = FIELDS_BY_KEY["base_expenses"]
    for person in ("self", "wife"):
        if snapshot.get_source(person, "base_expenses") != "Spending builder":
            continue
        breakdown = (snapshot.spending_breakdown or {}).get(person) or {}
        if not breakdown:
            continue
        total = sum(
            _money(amount)
            for cid, amount in breakdown.items()
            if counts_toward_base(str(cid))
        )
        snapshot.set_raw(base_field, format_field_value(base_field, total), person=person)
        changed = True
    snapshot.base_excludes_property_tax = True
    return changed


def apply_spending_builder(
    snapshot: PlanningSnapshot,
    *,
    person: str,
    amounts: dict[str, Any],
    mortgage_payoff_year: str | None = None,
) -> dict[str, Any]:
    person = person if person in {"self", "wife"} else "self"
    cleaned: dict[str, float] = {}
    for cat in SPENDING_CATEGORIES:
        cid = cat["id"]
        cleaned[cid] = round(_money(amounts.get(cid)), 2)

    # Base living = recurring bills + food/dining/entertainment/travel (+ rent).
    # Healthcare is a premium field, not part of base, so it is not counted twice.
    base_total = sum(v for k, v in cleaned.items() if counts_toward_base(k))
    mortgage_total = cleaned.get("mortgage", 0.0)

    base_field = FIELDS_BY_KEY["base_expenses"]
    snapshot.set_raw(base_field, format_field_value(base_field, base_total), person=person)
    snapshot.set_source(person, "base_expenses", "Spending builder")

    mort_field = FIELDS_BY_KEY["mortgage_annual"]
    snapshot.set_raw(mort_field, format_field_value(mort_field, mortgage_total), person=person)
    snapshot.set_source(
        person,
        "mortgage_annual",
        "Spending builder" if mortgage_total > 0 else "Manual",
    )

    property_tax_total = cleaned.get("property_tax", 0.0)
    prop_field = FIELDS_BY_KEY.get("property_tax_annual")
    if prop_field is not None:
        snapshot.set_raw(
            prop_field,
            format_field_value(prop_field, property_tax_total),
            person=person,
        )
        snapshot.set_source(
            person,
            "property_tax_annual",
            "Spending builder" if property_tax_total > 0 else "Manual",
        )

    if mortgage_payoff_year is not None:
        payoff_field = FIELDS_BY_KEY["mortgage_payoff_year"]
        raw_year = str(mortgage_payoff_year).strip()
        if raw_year in {"", "0"}:
            snapshot.set_raw(payoff_field, "", person=person)
            snapshot.set_source(person, "mortgage_payoff_year", "Manual")
        else:
            snapshot.set_raw(payoff_field, raw_year, person=person)
            snapshot.set_source(person, "mortgage_payoff_year", "Spending builder")

    if not hasattr(snapshot, "spending_breakdown") or snapshot.spending_breakdown is None:
        snapshot.spending_breakdown = {}
    snapshot.spending_breakdown[person] = cleaned
    snapshot.base_excludes_property_tax = True

    healthcare_note = ""
    healthcare = cleaned.get("healthcare", 0.0)
    person_fields = snapshot.persons.get(person) or {}
    pre = _money(person_fields.get("health_insurance_pre_medicare"))
    post = _money(person_fields.get("health_insurance_post_retire"))
    if healthcare > 0 and pre <= 0 and post <= 0:
        health_field = FIELDS_BY_KEY.get("health_insurance_pre_medicare")
        if health_field is not None:
            snapshot.set_raw(
                health_field,
                format_field_value(health_field, healthcare),
                person=person,
            )
            snapshot.set_source(person, "health_insurance_pre_medicare", "Spending builder")
            healthcare_note = f" Healthcare ${healthcare:,.0f}/yr is the pre-Medicare premium, not base."

    return {
        "ok": True,
        "person": person,
        "base_expenses": base_total,
        "mortgage_annual": mortgage_total,
        "phase_suggestion": None,
        "message": (
            f"{'You' if person == 'self' else 'Spouse'}: "
            f"Base Expenses ${base_total:,.0f}/yr"
            + (f" + Mortgage ${mortgage_total:,.0f}/yr" if mortgage_total else "")
            + (
                f" + Property tax ${property_tax_total:,.0f}/yr"
                if property_tax_total
                else ""
            )
            + healthcare_note
        ),
    }

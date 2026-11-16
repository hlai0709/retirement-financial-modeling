"""UI grouping metadata for expert sections and guided wizard steps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.guided_catalog import guided_order_for_field
from config.model_fields import ALL_FIELDS, ModelField, PERSON_FIELDS, SHARED_FIELDS

PersonScope = Literal["self", "wife", "shared", "household"]


@dataclass(frozen=True)
class UiGroup:
    step_id: str
    label: str
    person_scope: PersonScope
    sort_order: int = 0
    expert_only: bool = False
    optional_in_guided: bool = False


UI_GROUPS: tuple[UiGroup, ...] = (
    UiGroup("setup", "Household setup", "household", 0),
    UiGroup("timeline", "Timeline & longevity", "shared", 10),
    UiGroup("income_self", "Your income & savings", "self", 20),
    UiGroup("income_wife", "Spouse income & savings", "wife", 30),
    UiGroup("assets_self", "Your assets", "self", 40),
    UiGroup("assets_wife", "Spouse assets", "wife", 50),
    UiGroup("spending_self", "Your retirement spending", "self", 60),
    UiGroup("spending_wife", "Spouse retirement spending", "wife", 70),
    UiGroup("housing", "Housing", "self", 75),
    UiGroup("healthcare_self", "Your healthcare", "self", 80),
    UiGroup("healthcare_wife", "Spouse healthcare", "wife", 90),
    UiGroup("tax_benefits", "Taxes & benefits", "shared", 100),
    UiGroup("tax_wife", "Spouse taxes & benefits", "wife", 105),
    UiGroup("market", "Market assumptions", "shared", 110),
    UiGroup("import_documents", "Import documents", "household", 115, optional_in_guided=True),
    UiGroup("live_data", "Connect accounts", "household", 120, optional_in_guided=True),
    UiGroup("llm_assist", "AI estimates", "household", 130, optional_in_guided=True),
    UiGroup("review", "Review & run", "household", 140),
    UiGroup("scenarios", "Advanced scenarios", "shared", 200, expert_only=True),
)

_UI_GROUP_BY_ID = {group.step_id: group for group in UI_GROUPS}

# Field key → ui_group step_id (complete catalog map; heuristics are fallback only).
FIELD_UI_GROUP: dict[str, str] = {
    "state_of_residence": "setup",
    "planning_precision": "setup",
    "life_expectancy": "timeline",
    "marriage_year": "timeline",
    "current_age": "timeline",
    "birth_year": "timeline",
    "retirement_age": "income_self",
    "annual_earned_income": "income_self",
    "annual_contributions": "income_self",
    "contribution_roth_pct": "income_self",
    "rental_income_start": "income_self",
    "rental_growth_rate": "income_self",
    "initial_assets": "assets_self",
    "initial_taxable_assets": "assets_self",
    "initial_traditional_ira": "assets_self",
    "initial_roth_ira": "assets_self",
    "asset_share_pct": "assets_self",
    "roth_portfolio_pct": "assets_self",
    "base_expenses": "spending_self",
    "expense_phase_mode": "spending_self",
    "expense_go_go_end_age": "spending_self",
    "expense_slow_go_end_age": "spending_self",
    "expense_go_go_pct": "spending_self",
    "expense_slow_go_pct": "spending_self",
    "expense_no_go_pct": "spending_self",
    "mortgage_annual": "housing",
    "mortgage_payoff_year": "housing",
    "mortgage_payoff_month": "housing",
    "mortgage_second_annual": "housing",
    "mortgage_second_payoff_year": "housing",
    "mortgage_second_payoff_month": "housing",
    "property_tax_annual": "housing",
    "one_time_expense_year": "spending_self",
    "one_time_expense_amount": "spending_self",
    "long_term_care_annual": "spending_self",
    "health_insurance_pre_medicare": "healthcare_self",
    "health_insurance_post_retire": "healthcare_self",
    "employer_health_mode": "healthcare_self",
    "employer_health_monthly": "healthcare_self",
    "cobra_months": "healthcare_self",
    "medigap_plan": "healthcare_self",
    "medigap_monthly": "healthcare_self",
    "ltc_mode": "healthcare_self",
    "ltc_start_age": "healthcare_self",
    "ltc_duration_years": "healthcare_self",
    "ltc_care_setting": "healthcare_self",
    "ltc_inflation_rate": "healthcare_self",
    "medicare_enabled": "healthcare_self",
    "medicare_start_age": "healthcare_self",
    "medicare_part_b_monthly": "healthcare_self",
    "medicare_part_d_monthly": "healthcare_self",
    "medicare_irmaa_lookback_years": "healthcare_self",
    "aca_premium_mode": "healthcare_self",
    "aca_benchmark_monthly_age_21": "healthcare_self",
    "aca_slcsp_monthly_age_21": "healthcare_self",
    "aca_selected_plan_monthly_age_21": "healthcare_self",
    "pre_retire_tax_rate": "tax_benefits",
    "post_retire_tax_rate": "tax_benefits",
    "ss_tax_method": "tax_benefits",
    "ss_taxable_fraction": "tax_benefits",
    "rmd_start_age": "tax_benefits",
    "use_birth_year_rmd": "tax_benefits",
    "taxable_gain_fraction": "tax_benefits",
    "withdrawal_order": "tax_benefits",
    "niit_enabled": "tax_benefits",
    "mfs_lived_apart": "tax_benefits",
    "working_year_tax_enabled": "tax_benefits",
    "roth_conversion_target_bracket": "tax_benefits",
    "survivor_filing_status": "tax_benefits",
    "itemized_salt_annual": "tax_benefits",
    "itemized_mortgage_interest": "tax_benefits",
    "itemized_mortgage_interest_second": "tax_benefits",
    "itemized_charitable": "tax_benefits",
    "itemized_medical": "tax_benefits",
    "tax_calculation": "tax_benefits",
    "filing_status_before_marriage": "tax_benefits",
    "filing_status_after_marriage": "tax_benefits",
    "filing_status": "tax_benefits",
    "state_move_year": "tax_benefits",
    "state_after_move": "tax_benefits",
    "state_tax_enabled": "tax_benefits",
    "state_tax_override_pct": "tax_benefits",
    "qcd_annual": "tax_benefits",
    "pension_start_age": "tax_benefits",
    "pension_amount": "tax_benefits",
    "ss_start_age": "tax_benefits",
    "ss_benefit_mode": "tax_benefits",
    "ss_amount": "tax_benefits",
    "ss_pia_monthly": "tax_benefits",
    "ss_aime": "tax_benefits",
    "ss_career_avg_earnings": "tax_benefits",
    "roth_conversion_mode": "tax_benefits",
    "roth_conversion_annual": "tax_benefits",
    "roth_conversion_start_age": "tax_benefits",
    "roth_conversion_end_age": "tax_benefits",
    "inflation_rate": "market",
    "investment_return_mean": "market",
    "tsla_current_price": "market",
    "tsla_target_price": "scenarios",
    "tsla_target_year": "scenarios",
    "doomsday_drop_year": "scenarios",
    "doomsday_drop_pct": "scenarios",
    "rebalance_to_sp500": "scenarios",
}

_ROLE_TO_GROUP: dict[str, str] = {
    "base_expense": "spending_self",
    "retirement_expense_additive": "spending_self",
    "health_pre_medicare": "healthcare_self",
    "health_post_medicare": "healthcare_self",
    "rental_income": "income_self",
    "rental_growth": "income_self",
}


def _heuristic_group(field: ModelField) -> str:
    if field.model_role in _ROLE_TO_GROUP:
        return _ROLE_TO_GROUP[field.model_role]
    key = field.key
    if "mortgage" in key or "one_time_expense" in key or "long_term_care" in key:
        return "spending_self"
    if "health" in key or "medicare" in key or "aca" in key or "medigap" in key or "ltc" in key:
        return "healthcare_self"
    if "expense" in key or "spending" in key:
        return "spending_self"
    if (
        "ss_" in key
        or "social" in key
        or "pension" in key
        or "roth" in key
        or "rmd" in key
        or "filing" in key
        or "tax" in key
        or "itemized" in key
        or "niit" in key
        or "state_" in key
    ):
        return "tax_benefits"
    if "asset" in key or "contribution" in key or "income" in key or "salary" in key or "rental" in key:
        return "income_self" if field.scope == "person" else "market"
    if field.data_source == "market":
        return "market"
    return "timeline" if field.scope == "shared" else "income_self"


def ui_group_for_field(field: ModelField, *, person: str | None = None) -> str:
    mapped = FIELD_UI_GROUP.get(field.key)
    if mapped:
        group = mapped
    else:
        group = _heuristic_group(field)

    if person == "wife" and group.endswith("_self"):
        return group.replace("_self", "_wife")
    return group


def sort_fields_for_guided(fields: tuple[ModelField, ...] | list[ModelField]) -> tuple[ModelField, ...]:
    return tuple(sorted(fields, key=lambda field: (guided_order_for_field(field), field.key)))


def fields_for_ui_group(step_id: str, *, person: str | None = None) -> tuple[ModelField, ...]:
    group = _UI_GROUP_BY_ID.get(step_id)
    if group is None:
        return ()

    matched: list[ModelField] = []
    if group.person_scope == "shared":
        matched.extend(field for field in SHARED_FIELDS if ui_group_for_field(field) == step_id)
        matched.extend(
            field
            for field in PERSON_FIELDS
            if ui_group_for_field(field, person="self") == step_id
        )
    elif group.person_scope in ("self", "wife"):
        target_person = group.person_scope
        person_group_id = "tax_benefits" if step_id == "tax_wife" else step_id
        matched.extend(
            field
            for field in PERSON_FIELDS
            if ui_group_for_field(field, person=target_person) == person_group_id
        )
        matched.extend(field for field in SHARED_FIELDS if ui_group_for_field(field) == step_id)

    unique = _dedupe_fields_for_step(matched, group)
    return sort_fields_for_guided(unique)


def _dedupe_fields_for_step(fields: list[ModelField], group: UiGroup | None) -> list[ModelField]:
    """Prefer shared fields on shared steps; prefer person fields on person steps."""
    by_key: dict[str, ModelField] = {}
    for field in fields:
        existing = by_key.get(field.key)
        if existing is None:
            by_key[field.key] = field
            continue
        if group is None:
            continue
        if group.person_scope == "shared" and field.scope == "shared":
            by_key[field.key] = field
        elif group.person_scope in ("self", "wife") and field.scope == "person":
            by_key[field.key] = field
    return list(by_key.values())


def all_user_facing_fields_mapped() -> bool:
    """True when every non-scenario catalog field has an explicit ui_group."""
    skip = {"setup", "live_data", "llm_assist", "review", "scenarios"}
    for field in ALL_FIELDS:
        if FIELD_UI_GROUP.get(field.key) in skip or field.key in {"state_of_residence", "planning_precision"}:
            continue
        if field.key not in FIELD_UI_GROUP:
            return False
    return True


def all_catalog_fields() -> tuple[ModelField, ...]:
    return ALL_FIELDS
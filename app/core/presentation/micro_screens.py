"""Intra-step micro-screen pagination for dense guided wizard steps."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.guided_catalog import STEP_GUIDED_INTROS
from app.core.ui_groups import fields_for_ui_group, sort_fields_for_guided
from config.model_fields import ModelField

# Steps with more than ~8 fields are split into focused micro-screens.
_MICRO_SCREEN_FIELD_KEYS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "healthcare_self": (
        ("Employer & pre-Medicare coverage", (
            "health_insurance_pre_medicare",
            "health_insurance_post_retire",
            "employer_health_mode",
            "employer_health_monthly",
            "cobra_months",
        )),
        ("Medicare premiums", (
            "medicare_enabled",
            "medicare_start_age",
            "medicare_part_b_monthly",
            "medicare_part_d_monthly",
            "medicare_irmaa_lookback_years",
            "medigap_plan",
            "medigap_monthly",
        )),
        ("ACA before Medicare", (
            "aca_premium_mode",
            "aca_benchmark_monthly_age_21",
            "aca_slcsp_monthly_age_21",
            "aca_selected_plan_monthly_age_21",
        )),
        ("Long-term care", (
            "ltc_mode",
            "ltc_start_age",
            "ltc_duration_years",
            "ltc_care_setting",
            "ltc_inflation_rate",
        )),
    ),
    "healthcare_wife": (
        ("Spouse coverage", (
            "health_insurance_pre_medicare",
            "health_insurance_post_retire",
            "employer_health_mode",
            "employer_health_monthly",
            "cobra_months",
        )),
        ("Spouse Medigap", (
            "medigap_plan",
            "medigap_monthly",
        )),
        ("Spouse long-term care", (
            "ltc_mode",
            "ltc_start_age",
            "ltc_duration_years",
            "ltc_care_setting",
        )),
    ),
    "tax_benefits": (
        ("Social Security & pension", (
            "ss_start_age",
            "ss_benefit_mode",
            "ss_amount",
            "ss_pia_monthly",
            "ss_aime",
            "ss_career_avg_earnings",
            "pension_start_age",
            "pension_amount",
        )),
        ("Tax rates & filing", (
            "pre_retire_tax_rate",
            "post_retire_tax_rate",
            "filing_status",
            "filing_status_before_marriage",
            "filing_status_after_marriage",
            "tax_calculation",
        )),
        ("Withdrawals & Roth", (
            "withdrawal_order",
            "taxable_gain_fraction",
            "roth_conversion_mode",
            "roth_conversion_annual",
            "roth_conversion_start_age",
            "roth_conversion_end_age",
            "roth_conversion_target_bracket",
            "qcd_annual",
        )),
        ("SS tax & RMD rules", (
            "ss_tax_method",
            "ss_taxable_fraction",
            "rmd_start_age",
            "use_birth_year_rmd",
            "niit_enabled",
            "working_year_tax_enabled",
        )),
        ("Deductions & state tax", (
            "itemized_salt_annual",
            "itemized_mortgage_interest",
            "itemized_charitable",
            "itemized_medical",
            "state_move_year",
            "state_after_move",
            "state_tax_enabled",
            "state_tax_override_pct",
            "mfs_lived_apart",
            "survivor_filing_status",
        )),
    ),
    "tax_wife": (
        ("Spouse SS & pension", (
            "ss_start_age",
            "ss_benefit_mode",
            "ss_amount",
            "pension_start_age",
            "pension_amount",
        )),
        ("Spouse SS details", (
            "ss_pia_monthly",
            "ss_aime",
            "ss_career_avg_earnings",
        )),
        ("Spouse Roth & QCD", (
            "roth_conversion_mode",
            "roth_conversion_annual",
            "roth_conversion_start_age",
            "roth_conversion_end_age",
            "qcd_annual",
            "filing_status_before_marriage",
        )),
    ),
}


@dataclass(frozen=True)
class MicroScreenPage:
    title: str
    intro: str
    field_keys: tuple[str, ...]


def _pages_for_step(step_id: str) -> tuple[MicroScreenPage, ...]:
    raw = _MICRO_SCREEN_FIELD_KEYS.get(step_id)
    if not raw:
        return ()
    step_intro = STEP_GUIDED_INTROS.get(step_id, "")
    return tuple(
        MicroScreenPage(title=title, intro=step_intro if i == 0 else "", field_keys=keys)
        for i, (title, keys) in enumerate(raw)
    )


def micro_screen_count(step_id: str) -> int:
    pages = _pages_for_step(step_id)
    return len(pages) if pages else 1


def micro_screen_meta(step_id: str, sub_index: int) -> dict[str, str | int] | None:
    pages = _pages_for_step(step_id)
    if not pages:
        return None
    sub_index = max(0, min(sub_index, len(pages) - 1))
    page = pages[sub_index]
    return {
        "microStep": sub_index + 1,
        "microTotal": len(pages),
        "microTitle": page.title,
        "intro": page.intro,
    }


def fields_for_micro_screen(step_id: str, sub_index: int) -> tuple[ModelField, ...]:
    all_fields = fields_for_ui_group(step_id)
    pages = _pages_for_step(step_id)
    if not pages:
        return sort_fields_for_guided(all_fields)

    by_key = {field.key: field for field in all_fields}
    sub_index = max(0, min(sub_index, len(pages) - 1))
    page = pages[sub_index]
    ordered: list[ModelField] = []
    for key in page.field_keys:
        field = by_key.get(key)
        if field is not None:
            ordered.append(field)
    return tuple(ordered)


def validate_micro_screen_coverage(step_id: str) -> list[str]:
    """Return error strings when micro-screen keys do not match the wizard step catalog."""
    pages = _pages_for_step(step_id)
    if not pages:
        return []

    catalog_keys = {field.key for field in fields_for_ui_group(step_id)}
    page_keys: list[str] = []
    for page in pages:
        page_keys.extend(page.field_keys)

    errors: list[str] = []
    missing = catalog_keys - set(page_keys)
    extra = set(page_keys) - catalog_keys
    if missing:
        errors.append(f"{step_id}: micro-screens missing {sorted(missing)}")
    if extra:
        errors.append(f"{step_id}: micro-screens unknown {sorted(extra)}")
    if len(page_keys) != len(set(page_keys)):
        errors.append(f"{step_id}: duplicate field keys across micro-screens")
    return errors
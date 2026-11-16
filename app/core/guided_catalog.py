"""TurboTax-style guided questions and step intros for the field catalog."""

from __future__ import annotations

from config.model_fields import ModelField

# Step-level intro shown above fields in guided mode.
STEP_GUIDED_INTROS: dict[str, str] = {
    "timeline": "Start with your age. For a couple, add your spouse's age, and say whether you are already married.",
    "income_self": "Tell us about your work income and how much you're saving toward retirement.",
    "income_wife": "Now the same details for your spouse's income and savings.",
    "assets_self": "What do you have saved today, and how is it split across account types?",
    "assets_wife": "Enter your spouse's current savings and account mix.",
    "spending_self": "How much do you expect to spend in retirement, aside from the mortgage and property tax?",
    "housing": "Enter the household's mortgages and property tax once. Payments are principal and interest only, not escrow.",
    "spending_wife": "Enter your spouse's expected retirement spending.",
    "healthcare_self": "Healthcare costs often dominate retirement — employer coverage, Medicare, and long-term care.",
    "healthcare_wife": "Enter healthcare assumptions for your spouse.",
    "tax_benefits": "Social Security, pensions, Roth strategies, and tax settings shape your after-tax income.",
    "tax_wife": "Enter Social Security, pension, and tax details for your spouse.",
    "market": "These economy-wide assumptions drive inflation, returns, and stress scenarios.",
    "import_documents": "Import a tax return or other documents to pre-fill planning fields — review everything before applying.",
}

# Wife-specific wording overrides (falls back to FIELD_GUIDED_QUESTIONS).
WIFE_GUIDED_QUESTIONS: dict[str, str] = {
    "retirement_age": "At what age does your spouse plan to retire?",
    "annual_earned_income": "What is your spouse's current annual earned income?",
    "annual_contributions": "How much does your spouse save toward retirement each year?",
    "initial_assets": "What is your spouse's total investable assets today?",
    "initial_taxable_assets": "How much of your spouse's portfolio is in taxable brokerage accounts?",
    "initial_traditional_ira": "What is your spouse's traditional IRA / 401(k) balance?",
    "initial_roth_ira": "What is your spouse's Roth IRA / Roth 401(k) balance?",
    "asset_share_pct": "What share of household assets does your spouse hold?",
    "base_expenses": "What are your spouse's expected annual base living expenses in retirement?",
    "current_age": "How old is your spouse today?",
    "birth_year": "What year was your spouse born?",
    "life_expectancy": "What life expectancy should we use for your spouse?",
    "health_insurance_pre_medicare": "What does your spouse pay annually for health insurance before Medicare?",
    "health_insurance_post_retire": "What extra healthcare costs does your spouse expect after Medicare?",
}

# Field key → primary guided question (self / shared). Falls back to field label.
FIELD_GUIDED_QUESTIONS: dict[str, str] = {
    "life_expectancy": "How long should we plan for (life expectancy in years)?",
    "marriage_year": "In what year did you marry (if applicable)?",
    "current_age": "How old are you today?",
    "birth_year": "What year were you born?",
    "retirement_age": "At what age do you plan to retire?",
    "annual_earned_income": "What is your current annual earned income?",
    "annual_contributions": "How much do you save toward retirement each year?",
    "contribution_roth_pct": "What percentage of contributions go to Roth accounts?",
    "rental_income_start": "How much rental income do you receive annually?",
    "rental_growth_rate": "What annual growth rate do you expect for rental income?",
    "initial_assets": "What are your total investable assets today?",
    "initial_taxable_assets": "How much is in taxable brokerage accounts?",
    "initial_traditional_ira": "What is your traditional IRA / 401(k) balance?",
    "initial_roth_ira": "What is your Roth IRA / Roth 401(k) balance?",
    "asset_share_pct": "What share of household assets do you hold?",
    "roth_portfolio_pct": "What percentage of your retirement accounts is Roth? (Auto-fills from Roth ÷ traditional + Roth. Taxable accounts are separate.)",
    "base_expenses": "What are your expected annual base living expenses in retirement?",
    "expense_phase_mode": "Should spending stay flat or vary by age band (Go-Go / Slow-Go / No-Go)?",
    "expense_go_go_end_age": "Until what age is the active 'Go-Go' spending phase?",
    "expense_slow_go_end_age": "Until what age is the 'Slow-Go' spending phase?",
    "expense_go_go_pct": "What percentage of Base Expenses applies in Go-Go years? (Can be derived: permanent bills stay; lifestyle steps down. Mortgage is separate until payoff.)",
    "expense_slow_go_pct": "What percentage of Base Expenses applies in Slow-Go years?",
    "expense_no_go_pct": "What percentage of Base Expenses applies in No-Go years?",
    "mortgage_annual": "What do you pay each year on the first mortgage, principal and interest only?",
    "mortgage_payoff_year": "In what year is the first mortgage paid off?",
    "mortgage_payoff_month": "In which month is the last payment on the first mortgage?",
    "mortgage_second_annual": "What do you pay each year on a second mortgage, principal and interest only?",
    "mortgage_second_payoff_year": "In what year is the second mortgage paid off?",
    "mortgage_second_payoff_month": "In which month is the last payment on the second mortgage?",
    "property_tax_annual": "What do you pay in property tax each year?",
    "one_time_expense_year": "Is there a one-time expense? Enter the year (or leave blank).",
    "one_time_expense_amount": "How much is the one-time expense?",
    "long_term_care_annual": "What annual long-term care cost should we plan for?",
    "health_insurance_pre_medicare": "What do you pay annually for health insurance before Medicare?",
    "health_insurance_post_retire": "What extra healthcare costs do you expect after Medicare (dental, vision, etc.)?",
    "employer_health_mode": "How is employer health coverage modeled (active, COBRA, none)?",
    "employer_health_monthly": "What is your monthly employer health premium while working?",
    "cobra_months": "How many months of COBRA coverage do you expect after leaving work?",
    "medigap_plan": "Which Medigap plan letter do you expect (e.g. Plan G)?",
    "medigap_monthly": "What monthly Medigap premium do you expect?",
    "ltc_mode": "How should we model long-term care (none, annual flat, or scenario)?",
    "ltc_start_age": "At what age might long-term care costs begin?",
    "ltc_duration_years": "For how many years should we model long-term care?",
    "ltc_care_setting": "What care setting should we assume (home health, assisted living, nursing home)?",
    "ltc_inflation_rate": "What annual inflation rate should we use for long-term care costs?",
    "medicare_enabled": "Should we model Medicare premiums in your plan?",
    "medicare_start_age": "At what age does Medicare coverage begin?",
    "medicare_part_b_monthly": "What monthly Part B premium do you expect?",
    "medicare_part_d_monthly": "What monthly Part D premium do you expect?",
    "medicare_irmaa_lookback_years": "How many years of income lookback for IRMAA surcharges?",
    "aca_premium_mode": "How should ACA premiums be estimated (subsidized model or manual)?",
    "aca_benchmark_monthly_age_21": "Benchmark ACA monthly premium at age 21 (reference).",
    "aca_slcsp_monthly_age_21": "Second-lowest-cost Silver Plan monthly premium at age 21.",
    "aca_selected_plan_monthly_age_21": "Your selected plan monthly premium at age 21 (reference).",
    "pre_retire_tax_rate": "What effective tax rate applies while you're still working?",
    "post_retire_tax_rate": "What effective tax rate do you expect in retirement?",
    "ss_tax_method": "How should Social Security benefits be taxed (IRS formula or flat %)?",
    "ss_taxable_fraction": "What fraction of Social Security is taxable (flat method)?",
    "rmd_start_age": "At what age do required minimum distributions begin?",
    "use_birth_year_rmd": "Use IRS birth-year RMD tables instead of a fixed start age?",
    "taxable_gain_fraction": "What fraction of taxable account withdrawals are capital gains?",
    "withdrawal_order": "In what order should accounts be drawn down?",
    "niit_enabled": "Include the Net Investment Income Tax (3.8% NIIT) when applicable?",
    "mfs_lived_apart": "Did you live apart from your spouse when filing married filing separately?",
    "working_year_tax_enabled": "Model detailed working-year payroll and income taxes?",
    "roth_conversion_target_bracket": "Target marginal bracket for annual Roth conversions (%)",
    "survivor_filing_status": "What filing status should the survivor use?",
    "itemized_salt_annual": "Annual state and local taxes paid (SALT) for itemized deductions.",
    "itemized_mortgage_interest": "Annual mortgage interest for itemized deductions.",
    "itemized_charitable": "Annual charitable contributions for itemized deductions.",
    "itemized_medical": "Annual medical expenses for itemized deductions.",
    "tax_calculation": "Use standard or itemized tax calculation in CPA precision mode?",
    "filing_status_before_marriage": "What filing status applied before you married?",
    "filing_status_after_marriage": "What filing status applies after marriage?",
    "filing_status": "What is your current tax filing status?",
    "state_move_year": "Do you plan to relocate? Enter the year (or leave blank).",
    "state_after_move": "Which state will you move to?",
    "state_tax_enabled": "Include state income tax in projections?",
    "state_tax_override_pct": "Override state tax rate (%) if not using tables.",
    "qcd_annual": "How much do you donate annually via qualified charitable distributions?",
    "pension_start_age": "At what age does pension income begin?",
    "pension_amount": "What is your annual pension income?",
    "ss_start_age": "At what age will you claim Social Security?",
    "ss_benefit_mode": "How should we estimate Social Security (manual amount or earnings record)?",
    "ss_amount": "What is your expected annual Social Security benefit?",
    "ss_pia_monthly": "What is your Primary Insurance Amount (monthly)?",
    "ss_aime": "What is your Average Indexed Monthly Earnings?",
    "ss_career_avg_earnings": "What were your average annual career earnings?",
    "roth_conversion_mode": "Will you perform Roth conversions (none, fixed annual, or bracket-fill)?",
    "roth_conversion_annual": "How much do you convert to Roth each year?",
    "roth_conversion_start_age": "At what age do Roth conversions begin?",
    "roth_conversion_end_age": "At what age do Roth conversions end?",
    "inflation_rate": "What annual inflation rate should we assume?",
    "investment_return_mean": "What average annual investment return do you expect?",
    "tsla_current_price": "Current TSLA price for concentration scenarios (optional).",
}

# Lower numbers appear first within a wizard step.
FIELD_GUIDED_ORDER: dict[str, int] = {
    "current_age": 10,
    "birth_year": 20,
    "life_expectancy": 30,
    "marriage_year": 40,
    "retirement_age": 10,
    "annual_earned_income": 20,
    "annual_contributions": 30,
    "contribution_roth_pct": 40,
    "rental_income_start": 50,
    "rental_growth_rate": 60,
    "initial_assets": 10,
    "initial_taxable_assets": 20,
    "initial_traditional_ira": 30,
    "initial_roth_ira": 40,
    "roth_portfolio_pct": 50,
    "asset_share_pct": 60,
    "base_expenses": 10,
    "expense_phase_mode": 20,
    "expense_go_go_end_age": 30,
    "expense_slow_go_end_age": 40,
    "expense_go_go_pct": 50,
    "expense_slow_go_pct": 60,
    "expense_no_go_pct": 70,
    "mortgage_annual": 10,
    "mortgage_payoff_year": 20,
    "mortgage_payoff_month": 30,
    "mortgage_second_annual": 40,
    "mortgage_second_payoff_year": 50,
    "mortgage_second_payoff_month": 60,
    "property_tax_annual": 70,
    "one_time_expense_year": 100,
    "one_time_expense_amount": 110,
    "long_term_care_annual": 120,
    "ss_start_age": 10,
    "ss_benefit_mode": 20,
    "ss_amount": 30,
    "pension_start_age": 40,
    "pension_amount": 50,
    "inflation_rate": 10,
    "investment_return_mean": 20,
    "tsla_current_price": 30,
}


def guided_question_for_field(
    field: ModelField,
    *,
    person: str | None = None,
    household_mode: str | None = None,
) -> str:
    if person == "wife" and field.key in WIFE_GUIDED_QUESTIONS:
        return WIFE_GUIDED_QUESTIONS[field.key]
    if field.key == "marriage_year" and household_mode == "joint":
        return "In what year do you plan to marry?"
    question = FIELD_GUIDED_QUESTIONS.get(field.key)
    if question:
        return question
    label = field.label.strip()
    if not label.endswith("?"):
        return f"What is your {label.lower()}?"
    return label


def guided_help_for_field(field: ModelField) -> str:
    return (field.description or "").strip()


def guided_order_for_field(field: ModelField) -> int:
    return FIELD_GUIDED_ORDER.get(field.key, 1000)
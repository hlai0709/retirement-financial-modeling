"""
Single source of truth for projection input fields.

Add a field here once — the GUI, LLM research, and model engine pick it up automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldScope = Literal["shared", "person"]
FieldType = Literal["money", "rate_pct", "age", "year", "int", "bool", "text"]
DataSource = Literal["manual", "plaid", "market"]
CombineRule = Literal["sum", "min", "max", "avg", "first"]
ModelRole = Literal[
    "standard",
    "retirement_expense_additive",
    "health_pre_medicare",
    "health_post_medicare",
    "base_expense",
    "rental_income",
    "rental_growth",
]

RETIREMENT_EXPENSE_ROLES: tuple[ModelRole, ...] = (
    "retirement_expense_additive",
    "health_pre_medicare",
    "health_post_medicare",
)


@dataclass(frozen=True)
class ModelField:
    key: str
    label: str
    scope: FieldScope
    default: str | float | int | bool
    field_type: FieldType
    data_source: DataSource
    model_role: ModelRole = "standard"
    combine_rule: CombineRule = "sum"
    description: str = ""
    llm_research: bool | None = None  # None = auto for manual money/rate fields
    market_source_key: str | None = None
    snapshot_attr: str | None = None  # attribute on MarketSnapshot (defaults to market_source_key)
    person_defaults: dict[str, str | float | int | bool] | None = None

    def default_for(self, person: str | None = None) -> str | float | int | bool:
        if person and self.person_defaults and person in self.person_defaults:
            return self.person_defaults[person]
        return self.default

    def supports_llm_research(self) -> bool:
        if self.data_source != "manual":
            return False
        if self.llm_research is not None:
            return self.llm_research
        return self.field_type in ("money", "rate_pct")

    def research_category(self) -> str:
        return self.key

    def market_snapshot_attribute(self) -> str | None:
        if self.data_source != "market":
            return None
        return self.snapshot_attr or self.market_source_key


SHARED_FIELDS: tuple[ModelField, ...] = (
    ModelField("life_expectancy", "Life Expectancy", "shared", 95, "int", "manual", combine_rule="first"),
    ModelField(
        "inflation_rate",
        "Inflation Rate %",
        "shared",
        3.0,
        "rate_pct",
        "market",
        market_source_key="inflation_rate",
        description="Annual inflation assumption for expense and contribution growth.",
    ),
    ModelField(
        "investment_return_mean",
        "Investment Return Mean %",
        "shared",
        7.0,
        "rate_pct",
        "market",
        market_source_key="investment_return_mean",
        description="Expected annual portfolio return before retirement stress scenarios.",
    ),
    ModelField(
        "tsla_current_price",
        "TSLA Current Price",
        "shared",
        "",
        "money",
        "market",
        market_source_key="tsla_current_price",
        snapshot_attr="tsla_price",
        llm_research=False,
        description="Current TSLA share price for concentration scenarios.",
    ),
    ModelField(
        "pre_retire_tax_rate",
        "Pre-Retire Tax Rate %",
        "shared",
        35.0,
        "rate_pct",
        "manual",
        description="Marginal rate on salary — used for Roth contribution after-tax adjustment.",
    ),
    ModelField(
        "post_retire_tax_rate",
        "Post-Retire Tax Rate %",
        "shared",
        20.0,
        "rate_pct",
        "manual",
        description="Effective rate on taxable portfolio withdrawals and benefit income.",
    ),
    ModelField(
        "roth_portfolio_pct",
        "Roth Portfolio %",
        "shared",
        15.0,
        "rate_pct",
        "manual",
        description=(
            "Share of portfolio in Roth/tax-free accounts. Auto-calculated from "
            "Roth ÷ (taxable + traditional IRA/401k + Roth) when those balances are set."
        ),
        llm_research=False,
    ),
    ModelField(
        "contribution_roth_pct",
        "Contribution Roth %",
        "shared",
        20.0,
        "rate_pct",
        "manual",
        description="Share of annual contributions directed to Roth (after-tax); rest is pre-tax deferred.",
        llm_research=False,
    ),
    ModelField(
        "ss_tax_method",
        "SS Tax Method",
        "shared",
        "irs_provisional",
        "text",
        "manual",
        description=(
            "Social Security taxation: irs_provisional (IRS combined-income tiers) or "
            "flat_fraction (use SS Taxable Fraction %)."
        ),
        llm_research=False,
    ),
    ModelField(
        "ss_taxable_fraction",
        "SS Taxable Fraction %",
        "shared",
        50.0,
        "rate_pct",
        "manual",
        description="Fixed taxable share of SS — only used when SS Tax Method is flat_fraction.",
        llm_research=False,
    ),
    ModelField(
        "rmd_start_age",
        "RMD Start Age",
        "shared",
        73,
        "int",
        "manual",
        description="Fallback RMD start age when Use Birth Year RMD is off (73 or 75 per SECURE 2.0).",
        llm_research=False,
    ),
    ModelField(
        "use_birth_year_rmd",
        "Use Birth Year RMD",
        "shared",
        "True",
        "bool",
        "manual",
        description="Apply SECURE 2.0 RMD ages from birth year (75 if born 1960+, else 73).",
        llm_research=False,
    ),
    ModelField(
        "taxable_gain_fraction",
        "Taxable Gain Fraction %",
        "shared",
        50.0,
        "rate_pct",
        "manual",
        description=(
            "Estimated unrealized-gain share of taxable brokerage (LTCG vs return-of-basis). "
            "Used with Initial Taxable Assets for preferential capital-gains rates."
        ),
        llm_research=False,
    ),
    ModelField(
        "niit_enabled",
        "NIIT Enabled",
        "shared",
        "True",
        "bool",
        "manual",
        description="Include 3.8% Net Investment Income Tax when MAGI exceeds IRS thresholds.",
        llm_research=False,
    ),
    ModelField(
        "mfs_lived_apart",
        "MFS Lived Apart",
        "shared",
        "False",
        "bool",
        "manual",
        description=(
            "If filing married_filing_separately: lived apart all year (single SS thresholds) "
            "vs lived together (up to 85% of SS taxable)."
        ),
        llm_research=False,
    ),
    ModelField(
        "withdrawal_order",
        "Withdrawal Order",
        "shared",
        "tax_efficient",
        "text",
        "manual",
        description=(
            "Portfolio draw sequence: tax_efficient (taxable → traditional → Roth) or "
            "proportional (legacy blend by balance share)."
        ),
        llm_research=False,
    ),
    ModelField(
        "working_year_tax_enabled",
        "Working Year Tax Enabled",
        "shared",
        "True",
        "bool",
        "manual",
        description=(
            "Tax W-2 wages during accumulation (federal, state, FICA). "
            "During partial retirement, FICA still applies to earned income."
        ),
        llm_research=False,
    ),
    ModelField(
        "roth_conversion_target_bracket",
        "Roth Conversion Target Bracket %",
        "shared",
        22.0,
        "rate_pct",
        "manual",
        description=(
            "Federal bracket ceiling for bracket_fill Roth conversions (12, 22, 24, or 32). "
            "Fills ordinary income up to the top of this bracket."
        ),
        llm_research=False,
    ),
    ModelField(
        "expense_phase_mode",
        "Expense Phase Mode",
        "shared",
        "flat",
        "text",
        "manual",
        description=(
            "Retirement spending profile: flat (100% base) or age_banded "
            "(go-go / slow-go / no-go multipliers)."
        ),
        llm_research=False,
    ),
    ModelField(
        "expense_go_go_end_age",
        "Expense Go-Go End Age",
        "shared",
        75,
        "int",
        "manual",
        description="Last age for full go-go spending (default 75).",
        llm_research=False,
    ),
    ModelField(
        "expense_slow_go_end_age",
        "Expense Slow-Go End Age",
        "shared",
        85,
        "int",
        "manual",
        description="Last age for slow-go spending; no-go begins after this age (default 85).",
        llm_research=False,
    ),
    ModelField(
        "expense_go_go_pct",
        "Expense Go-Go %",
        "shared",
        100.0,
        "rate_pct",
        "manual",
        description=(
            "Share of Base Expenses in go-go years (default 100%). "
            "Auto-suggested from spending mix: permanent bills stay; lifestyle steps down. "
            "Mortgage is outside this % until its payoff year."
        ),
        llm_research=False,
    ),
    ModelField(
        "expense_slow_go_pct",
        "Expense Slow-Go %",
        "shared",
        80.0,
        "rate_pct",
        "manual",
        description=(
            "Share of Base Expenses in slow-go years. Suggested as "
            "(permanent + ~70% lifestyle) / base from your spending categories."
        ),
        llm_research=False,
    ),
    ModelField(
        "expense_no_go_pct",
        "Expense No-Go %",
        "shared",
        65.0,
        "rate_pct",
        "manual",
        description=(
            "Share of Base Expenses in no-go years. Suggested as "
            "(permanent + ~40% lifestyle) / base from your spending categories."
        ),
        llm_research=False,
    ),
    ModelField(
        "ltc_inflation_rate",
        "LTC Inflation Rate %",
        "shared",
        4.5,
        "rate_pct",
        "manual",
        description=(
            "Annual escalation for long-term care costs (typically higher than general CPI)."
        ),
        llm_research=False,
    ),
    ModelField(
        "survivor_filing_status",
        "Survivor Filing Status",
        "shared",
        "single",
        "text",
        "manual",
        description=(
            "Federal filing status after one spouse dies in joint mode (typically single)."
        ),
        llm_research=False,
    ),
    ModelField(
        "planning_precision",
        "Planning Precision",
        "shared",
        "standard",
        "text",
        "manual",
        description=(
            "standard = fast planning (standard deduction). "
            "cpa = itemized vs standard, AMT, strict state catalog (slower)."
        ),
        llm_research=False,
    ),
    ModelField(
        "itemized_salt_annual",
        "Itemized SALT (Annual)",
        "shared",
        0,
        "money",
        "manual",
        description="State and local taxes. The deduction uses the statutory cap, including the 2025–2029 increase.",
        llm_research=False,
    ),
    ModelField(
        "itemized_mortgage_interest",
        "Itemized Mortgage Interest",
        "shared",
        0,
        "money",
        "manual",
        description=(
            "Annual interest on the first mortgage. Used when it beats the standard deduction. "
            "A second loan's interest is separate and each amount stops after that loan's payoff."
        ),
        llm_research=False,
    ),
    ModelField(
        "itemized_mortgage_interest_second",
        "Second Mortgage Interest",
        "shared",
        0,
        "money",
        "manual",
        description=(
            "Annual interest on the second mortgage. It ends when that loan's payoff year "
            "has passed, rather than staying on until the other loan ends."
        ),
        llm_research=False,
    ),
    ModelField(
        "itemized_charitable",
        "Itemized Charitable",
        "shared",
        0,
        "money",
        "manual",
        description="Annual charitable contributions for CPA Schedule A.",
        llm_research=False,
    ),
    ModelField(
        "itemized_medical",
        "Itemized Medical (OOP)",
        "shared",
        0,
        "money",
        "manual",
        description="Out-of-pocket medical expenses; CPA mode deducts amount above 7.5% of AGI.",
        llm_research=False,
    ),
    ModelField(
        "tax_calculation",
        "Tax Calculation",
        "shared",
        "federal_brackets",
        "text",
        "manual",
        description="Retirement tax model: federal_brackets (progressive) or flat (Post-Retire Tax Rate %).",
        llm_research=False,
    ),
    ModelField(
        "marriage_year",
        "Marriage Year",
        "shared",
        "",
        "year",
        "manual",
        description=(
            "Calendar year you plan to marry. Before this year each partner files separately; "
            "from this year onward the household uses Filing Status After Marriage (Joint mode)."
        ),
        llm_research=False,
    ),
    ModelField(
        "filing_status_before_marriage",
        "Filing Status Before Marriage",
        "shared",
        "head_of_household",
        "text",
        "manual",
        description="Federal status while unmarried: head_of_household, single, or mfj.",
        llm_research=False,
    ),
    ModelField(
        "filing_status_after_marriage",
        "Filing Status After Marriage",
        "shared",
        "mfj",
        "text",
        "manual",
        description="Federal status after Marriage Year (typically mfj).",
        llm_research=False,
    ),
    ModelField(
        "filing_status",
        "Filing Status (Legacy)",
        "shared",
        "head_of_household",
        "text",
        "manual",
        description=(
            "Fallback when Marriage Year is blank. Prefer Marriage Year + before/after statuses "
            "for flexible unmarried-to-married planning."
        ),
        llm_research=False,
    ),
    ModelField(
        "state_of_residence",
        "State of Residence",
        "shared",
        "CA",
        "text",
        "manual",
        description=(
            "State income tax residence (e.g. CA, TX, FL, NV, AZ, OR, NY). "
            "Use State Move Year to model relocation."
        ),
        llm_research=False,
    ),
    ModelField(
        "state_move_year",
        "State Move Year",
        "shared",
        "",
        "year",
        "manual",
        description="Calendar year you plan to relocate — switches to State After Move from this year on.",
        llm_research=False,
    ),
    ModelField(
        "state_after_move",
        "State After Move",
        "shared",
        "NV",
        "text",
        "manual",
        description="Destination state after relocation (e.g. NV, TX, FL for no state income tax).",
        llm_research=False,
    ),
    ModelField(
        "state_tax_enabled",
        "State Tax Enabled",
        "shared",
        "True",
        "bool",
        "manual",
        description="Include state income tax in projections. Turn off for federal-only what-if runs.",
        llm_research=False,
    ),
    ModelField(
        "state_tax_override_pct",
        "State Tax Override %",
        "shared",
        "",
        "rate_pct",
        "manual",
        description="Optional flat state rate override (ignores catalog brackets for the active state).",
        llm_research=False,
    ),
    ModelField(
        "medicare_enabled",
        "Medicare Enabled",
        "shared",
        "True",
        "bool",
        "manual",
        description="Include Medicare Part B/D premiums and IRMAA surcharges in cash-flow needs.",
        llm_research=False,
    ),
    ModelField(
        "medicare_start_age",
        "Medicare Start Age",
        "shared",
        65,
        "int",
        "manual",
        description=(
            "Age when Medicare Part B/D premiums begin (default 65). Independent of retirement age — "
            "early retirees use Health Insurance Pre-Medicare until this age."
        ),
        llm_research=False,
    ),
    ModelField(
        "medicare_part_b_monthly",
        "Medicare Part B Monthly",
        "shared",
        174.70,
        "money",
        "manual",
        description="Base Part B premium per person per month (2024 CMS baseline; grows with inflation).",
        llm_research=False,
    ),
    ModelField(
        "medicare_part_d_monthly",
        "Medicare Part D Monthly",
        "shared",
        55.50,
        "money",
        "manual",
        description="Average Part D plan premium per person per month (before IRMAA).",
        llm_research=False,
    ),
    ModelField(
        "medicare_irmaa_lookback_years",
        "Medicare IRMAA Lookback Years",
        "shared",
        2,
        "int",
        "manual",
        description="IRMAA surcharges use MAGI from this many years prior (IRS uses 2-year lookback).",
        llm_research=False,
    ),
    ModelField(
        "aca_premium_mode",
        "ACA Premium Mode",
        "shared",
        "aca_subsidized",
        "text",
        "manual",
        description=(
            "Pre-Medicare health cost model: aca_subsidized (MAGI-based premium tax credit) or "
            "manual (use per-person Health Insurance Pre-Medicare field)."
        ),
        llm_research=False,
    ),
    ModelField(
        "aca_benchmark_monthly_age_21",
        "ACA Benchmark Monthly Age 21",
        "shared",
        475.0,
        "money",
        "manual",
        description=(
            "Legacy/default SLCSP monthly at age 21. Used when ACA SLCSP Monthly Age 21 is blank."
        ),
        llm_research=False,
    ),
    ModelField(
        "aca_slcsp_monthly_age_21",
        "ACA SLCSP Monthly Age 21",
        "shared",
        "",
        "money",
        "manual",
        description=(
            "Second-lowest-cost silver plan (SLCSP) monthly premium at age 21 for premium tax credit. "
            "Blank = use ACA Benchmark Monthly Age 21."
        ),
        llm_research=False,
    ),
    ModelField(
        "aca_selected_plan_monthly_age_21",
        "ACA Selected Plan Monthly Age 21",
        "shared",
        "",
        "money",
        "manual",
        description=(
            "Enrolled plan monthly premium at age 21 (bronze/gold/silver). "
            "Blank = same as SLCSP. Subsidy is based on SLCSP; net cost reflects selected plan."
        ),
        llm_research=False,
    ),
    ModelField("tsla_target_price", "TSLA Target Price", "shared", "", "money", "manual", llm_research=True),
    ModelField("tsla_target_year", "TSLA Target Year", "shared", "", "year", "manual", llm_research=False),
    ModelField("doomsday_drop_year", "Doomsday Drop Year", "shared", "", "year", "manual", llm_research=False),
    ModelField("doomsday_drop_pct", "Doomsday Drop Pct %", "shared", 25.0, "rate_pct", "manual"),
    ModelField("rebalance_to_sp500", "Rebalance to SP500", "shared", "False", "bool", "manual", llm_research=False),
)

PERSON_FIELDS: tuple[ModelField, ...] = (
    ModelField("current_age", "Current Age", "person", 40, "age", "manual", combine_rule="min"),
    ModelField(
        "birth_year",
        "Birth Year",
        "person",
        "",
        "year",
        "manual",
        description="Optional birth year for SECURE 2.0 RMD age (defaults from Current Age + current year).",
        llm_research=False,
    ),
    ModelField("retirement_age", "Retirement Age", "person", 65, "age", "manual", combine_rule="max"),
    ModelField(
        "initial_assets",
        "Initial Assets",
        "person",
        500000,
        "money",
        "plaid",
        person_defaults={"self": 500000, "wife": 300000},
        description=(
            "Total starting investable assets. Split into taxable / traditional / Roth via "
            "explicit bucket fields, or legacy Roth % when buckets are blank."
        ),
        llm_research=False,
    ),
    ModelField(
        "initial_taxable_assets",
        "Initial Taxable Assets",
        "person",
        0,
        "money",
        "manual",
        description="Taxable brokerage balance at projection start (optional; overrides legacy split).",
        llm_research=False,
    ),
    ModelField(
        "initial_traditional_ira",
        "Initial Traditional IRA",
        "person",
        "",
        "money",
        "manual",
        description="Traditional IRA/401(k) balance at start (optional).",
        llm_research=False,
    ),
    ModelField(
        "initial_roth_ira",
        "Initial Roth IRA",
        "person",
        "",
        "money",
        "manual",
        description="Roth IRA balance at start (optional).",
        llm_research=False,
    ),
    ModelField(
        "annual_contributions",
        "Annual Contributions",
        "person",
        20000,
        "money",
        "manual",
        person_defaults={"self": 20000, "wife": 15000},
        description=(
            "Annual contribution budget while working. Roth vs pre-tax split is set under "
            "Contribution Roth % in shared parameters."
        ),
    ),
    ModelField(
        "annual_earned_income",
        "Annual Earned Income",
        "person",
        0,
        "money",
        "manual",
        person_defaults={"self": 150000, "wife": 0},
        description=(
            "Salary or wages while working — covers base living expenses from paycheck, "
            "not from the portfolio."
        ),
    ),
    ModelField(
        "qcd_annual",
        "QCD Annual",
        "person",
        0,
        "money",
        "manual",
        description=(
            "Qualified charitable distribution from traditional IRA (age 70+). "
            "Excluded from taxable income and counts toward RMD."
        ),
        llm_research=False,
    ),
    ModelField(
        "employer_health_mode",
        "Employer Health Mode",
        "person",
        "none",
        "text",
        "manual",
        description=(
            "Pre-Medicare coverage while working or on COBRA: none, employer (active until "
            "retirement), or cobra (continuation after retirement)."
        ),
        llm_research=False,
    ),
    ModelField(
        "employer_health_monthly",
        "Employer Health Monthly",
        "person",
        0,
        "money",
        "manual",
        person_defaults={"self": 450, "wife": 0},
        description="Employee share of employer or COBRA premium per month.",
        llm_research=False,
    ),
    ModelField(
        "cobra_months",
        "COBRA Months",
        "person",
        18,
        "int",
        "manual",
        description="COBRA continuation months after retirement (default 18).",
        llm_research=False,
    ),
    ModelField(
        "medigap_plan",
        "Medigap Plan",
        "person",
        "none",
        "text",
        "manual",
        description=(
            "Structured Medigap supplement after Medicare: none, plan_g, plan_n, or plan_f "
            "(2024 national-average premiums). Override with Medigap Monthly."
        ),
        llm_research=False,
    ),
    ModelField(
        "medigap_monthly",
        "Medigap Monthly",
        "person",
        "",
        "money",
        "manual",
        description="Custom Medigap premium per month (overrides Medigap Plan lookup).",
        llm_research=False,
    ),
    ModelField(
        "filing_status_before_marriage",
        "Filing Status Before Marriage",
        "person",
        "",
        "text",
        "manual",
        description="Optional per-person override before Marriage Year (e.g. head_of_household vs single).",
        llm_research=False,
    ),
    ModelField(
        "asset_share_pct",
        "Asset Share %",
        "person",
        "",
        "rate_pct",
        "manual",
        description=(
            "Share of joint portfolio for RMD/withdrawals while filing separately. "
            "Blank = proportional to Initial Assets."
        ),
        llm_research=False,
    ),
    ModelField(
        "base_expenses",
        "Base Expenses",
        "person",
        60000,
        "money",
        "plaid",
        model_role="base_expense",
        description="Core annual living expenses (excluding supplemental retirement-only costs).",
        llm_research=False,
    ),
    ModelField(
        "health_insurance_pre_medicare",
        "Health Insurance Pre-Medicare",
        "person",
        0,
        "money",
        "manual",
        model_role="health_pre_medicare",
        person_defaults={"self": 22_000, "wife": 20_000},
        description=(
            "Manual pre-Medicare health premium (used only when ACA Premium Mode is manual). "
            "While retired and age < Medicare Start Age. For subsidized ACA modeling, leave at 0."
        ),
    ),
    ModelField(
        "health_insurance_post_retire",
        "Health Insurance Post-Medicare",
        "person",
        0,
        "money",
        "manual",
        model_role="health_post_medicare",
        person_defaults={"self": 4_000, "wife": 4_000},
        description=(
            "Extra costs after Medicare (dental/vision/out-of-pocket). Structured Medigap uses "
            "Medigap Plan — Part B/D and IRMAA are under shared Medicare fields."
        ),
    ),
    ModelField(
        "life_expectancy",
        "Life Expectancy",
        "person",
        "",
        "int",
        "manual",
        description="Per-person life expectancy (blank = shared Life Expectancy).",
        llm_research=False,
    ),
    ModelField(
        "long_term_care_annual",
        "Long-Term Care Annual",
        "person",
        0,
        "money",
        "manual",
        model_role="retirement_expense_additive",
        description=(
            "Annual LTC cost at baseline (home-health-aide). Used in annual or scenario mode."
        ),
    ),
    ModelField(
        "ltc_mode",
        "LTC Mode",
        "person",
        "annual",
        "text",
        "manual",
        description=(
            "Long-term care: none, annual (flat from retirement), or scenario "
            "(start age + duration + care setting)."
        ),
        llm_research=False,
    ),
    ModelField(
        "ltc_start_age",
        "LTC Start Age",
        "person",
        "",
        "age",
        "manual",
        description="Age LTC costs begin in scenario mode (blank = retirement age).",
        llm_research=False,
    ),
    ModelField(
        "ltc_duration_years",
        "LTC Duration Years",
        "person",
        3,
        "int",
        "manual",
        description="Years of LTC costs in scenario mode (default 3).",
        llm_research=False,
    ),
    ModelField(
        "ltc_care_setting",
        "LTC Care Setting",
        "person",
        "home_health_aide",
        "text",
        "manual",
        description=(
            "Care intensity in scenario mode: home_health_aide, assisted_living, or nursing_home."
        ),
        llm_research=False,
    ),
    ModelField(
        "mortgage_annual",
        "Mortgage Annual",
        "person",
        0,
        "money",
        "manual",
        description=(
            "Annual principal and interest on the first mortgage until its payoff year. "
            "Escrow is not included. Not inflated (fixed nominal)."
        ),
        llm_research=False,
    ),
    ModelField(
        "mortgage_payoff_year",
        "Mortgage Payoff Year",
        "person",
        "",
        "year",
        "manual",
        description=(
            "Calendar year the mortgage ends (often computed from balance, P&I, and rate). "
            "Blank/0 = payment continues every year while Mortgage Annual is set. "
            "After this year the first mortgage payment stops. "
            "In the payoff year only the months through Mortgage Payoff Month are charged."
        ),
        llm_research=False,
    ),
    ModelField(
        "mortgage_payoff_month",
        "Mortgage Payoff Month",
        "person",
        "",
        "int",
        "manual",
        description=(
            "Month number (1–12) of the first mortgage's last payment. "
            "Blank charges the full payoff year."
        ),
        llm_research=False,
    ),
    ModelField(
        "mortgage_second_annual",
        "Second Mortgage Annual",
        "person",
        0,
        "money",
        "manual",
        description=(
            "Principal and interest on another mortgage. It ends on its own payoff year, "
            "not the first loan's year."
        ),
        llm_research=False,
    ),
    ModelField(
        "mortgage_second_payoff_year",
        "Second Mortgage Payoff Year",
        "person",
        "",
        "year",
        "manual",
        description="Calendar year the second mortgage's last payment falls in.",
        llm_research=False,
    ),
    ModelField(
        "mortgage_second_payoff_month",
        "Second Mortgage Payoff Month",
        "person",
        "",
        "int",
        "manual",
        description="Month number (1–12) of the second mortgage's last payment. Blank charges that full year.",
        llm_research=False,
    ),
    ModelField(
        "property_tax_annual",
        "Property Tax Annual",
        "person",
        0,
        "money",
        "manual",
        description=(
            "Annual property taxes for all properties (cash out the door). "
            "Sum multiple county tax bills. Feeds Spending builder; distinct from "
            "Itemized SALT (tax deduction, often capped)."
        ),
        llm_research=False,
    ),
    ModelField(
        "one_time_expense_year",
        "One-Time Expense Year",
        "person",
        "",
        "year",
        "manual",
        description="Calendar year for a one-time expense (blank = none).",
        llm_research=False,
    ),
    ModelField(
        "one_time_expense_amount",
        "One-Time Expense Amount",
        "person",
        0,
        "money",
        "manual",
        description="One-time expense amount in One-Time Expense Year.",
        llm_research=False,
    ),
    ModelField(
        "rental_income_start",
        "Rental Income Start",
        "person",
        0,
        "money",
        "manual",
        model_role="rental_income",
        person_defaults={"self": 12000, "wife": 0},
    ),
    ModelField(
        "rental_growth_rate",
        "Rental Growth Rate %",
        "person",
        3.0,
        "rate_pct",
        "manual",
        model_role="rental_growth",
        combine_rule="avg",
    ),
    ModelField("pension_start_age", "Pension Start Age", "person", 65, "age", "manual", combine_rule="min"),
    ModelField(
        "pension_amount",
        "Pension Amount",
        "person",
        0,
        "money",
        "manual",
        description="Annual pension income once benefits start.",
    ),
    ModelField("ss_start_age", "SS Start Age", "person", 67, "age", "manual", combine_rule="min"),
    ModelField(
        "ss_benefit_mode",
        "SS Benefit Mode",
        "person",
        "manual",
        "text",
        "manual",
        description=(
            "Social Security income source: manual (SS Amount), pia (monthly PIA at FRA), "
            "aime (indexed monthly earnings), or earnings (career-average estimate)."
        ),
        llm_research=False,
    ),
    ModelField(
        "ss_amount",
        "SS Amount",
        "person",
        0,
        "money",
        "manual",
        person_defaults={"self": 25000, "wife": 20000},
        description="Annual Social Security income once benefits start (manual mode only).",
    ),
    ModelField(
        "ss_pia_monthly",
        "SS PIA Monthly",
        "person",
        "",
        "money",
        "manual",
        description="Primary Insurance Amount per month at full retirement age (pia mode).",
        llm_research=False,
    ),
    ModelField(
        "ss_aime",
        "SS AIME",
        "person",
        "",
        "money",
        "manual",
        description="Average Indexed Monthly Earnings for PIA calculation (aime mode).",
        llm_research=False,
    ),
    ModelField(
        "ss_career_avg_earnings",
        "SS Career Avg Earnings",
        "person",
        "",
        "money",
        "manual",
        description=(
            "Career-average annual earnings for simplified AIME estimate (earnings mode). "
            "Blank = Annual Earned Income."
        ),
        llm_research=False,
    ),
    ModelField(
        "roth_conversion_mode",
        "Roth Conversion Mode",
        "person",
        "none",
        "text",
        "manual",
        description=(
            "Roth IRA conversion strategy: none, fixed (annual amount), or bracket_fill "
            "(convert up to Roth Conversion Target Bracket %)."
        ),
        llm_research=False,
    ),
    ModelField(
        "roth_conversion_annual",
        "Roth Conversion Annual",
        "person",
        0,
        "money",
        "manual",
        description="Fixed annual traditional→Roth conversion (fixed mode).",
        llm_research=False,
    ),
    ModelField(
        "roth_conversion_start_age",
        "Roth Conversion Start Age",
        "person",
        "",
        "age",
        "manual",
        description="First age to execute Roth conversions (blank = retirement age).",
        llm_research=False,
    ),
    ModelField(
        "roth_conversion_end_age",
        "Roth Conversion End Age",
        "person",
        "",
        "age",
        "manual",
        description="Last age for Roth conversions (blank = no limit).",
        llm_research=False,
    ),
)

ALL_FIELDS: tuple[ModelField, ...] = SHARED_FIELDS + PERSON_FIELDS
FIELDS_BY_KEY: dict[str, ModelField] = {field.key: field for field in ALL_FIELDS}
SHARED_FIELD_KEYS: tuple[str, ...] = tuple(field.key for field in SHARED_FIELDS)
PERSON_FIELD_KEYS: tuple[str, ...] = tuple(field.key for field in PERSON_FIELDS)
RETIREMENT_EXPENSE_ADDITIVE_KEYS: tuple[str, ...] = tuple(
    field.key for field in PERSON_FIELDS if field.model_role in RETIREMENT_EXPENSE_ROLES
)


def fields_for_scope(scope: FieldScope) -> tuple[ModelField, ...]:
    return SHARED_FIELDS if scope == "shared" else PERSON_FIELDS


def llm_researchable_fields(scope: FieldScope | None = None) -> tuple[ModelField, ...]:
    fields = ALL_FIELDS if scope is None else fields_for_scope(scope)
    return tuple(field for field in fields if field.supports_llm_research())


def fields_with_data_source(
    source: DataSource,
    *,
    scope: FieldScope | None = None,
) -> tuple[ModelField, ...]:
    fields = ALL_FIELDS if scope is None else fields_for_scope(scope)
    return tuple(field for field in fields if field.data_source == source)


def field_with_model_role(role: ModelRole, *, scope: FieldScope = "person") -> ModelField | None:
    for field in fields_for_scope(scope):
        if field.model_role == role:
            return field
    return None


_FILING_STATUS_CHOICES: tuple[str, ...] = (
    "head_of_household",
    "single",
    "mfj",
    "mfs",
)
_OPTIONAL_FILING_CHOICES: tuple[str, ...] = ("", *_FILING_STATUS_CHOICES)

# Fixed choice lists for catalog fields (bool fields use True/False automatically).
FIELD_CHOICES: dict[str, tuple[str, ...]] = {
    "ss_tax_method": ("irs_provisional", "flat_fraction"),
    "withdrawal_order": ("tax_efficient", "proportional"),
    "expense_phase_mode": ("flat", "age_banded"),
    "survivor_filing_status": _FILING_STATUS_CHOICES,
    "planning_precision": ("standard", "cpa"),
    "tax_calculation": ("federal_brackets", "flat"),
    "filing_status_before_marriage": _FILING_STATUS_CHOICES,
    "filing_status_after_marriage": _FILING_STATUS_CHOICES,
    "filing_status": _FILING_STATUS_CHOICES,
    "aca_premium_mode": ("aca_subsidized", "manual"),
    "employer_health_mode": ("none", "employer", "cobra"),
    "medigap_plan": ("none", "plan_g", "plan_n", "plan_f"),
    "ltc_mode": ("none", "annual", "scenario"),
    "ltc_care_setting": ("home_health_aide", "assisted_living", "nursing_home"),
    "ss_benefit_mode": ("manual", "pia", "aime", "earnings"),
    "roth_conversion_mode": ("none", "fixed", "bracket_fill"),
    "rmd_start_age": ("73", "75"),
    "roth_conversion_target_bracket": ("12", "22", "24", "32"),
}


def _state_code_choices() -> tuple[str, ...]:
    from model.state_taxes import SUPPORTED_STATE_CODES

    return SUPPORTED_STATE_CODES


def field_choice_values(field: ModelField) -> tuple[str, ...] | None:
    """Return dropdown values when a field is boolean or has a fixed choice set."""
    if field.field_type == "bool":
        return ("True", "False")
    if field.key == "filing_status_before_marriage" and field.scope == "person":
        return _OPTIONAL_FILING_CHOICES
    if field.key in {"state_of_residence", "state_after_move"}:
        return _state_code_choices()
    return FIELD_CHOICES.get(field.key)


def field_uses_choice_menu(field: ModelField) -> bool:
    return field_choice_values(field) is not None


def default_display_value(field: ModelField, *, person: str | None = None) -> str:
    """String shown when the field widget is first created."""
    raw = field.default_for(person)
    if raw in ("", None):
        return ""
    if field.field_type == "bool":
        return "True" if str(raw).lower() == "true" else "False"
    if field.field_type == "rate_pct" and field.key == "roth_conversion_target_bracket":
        return str(int(float(raw)))
    return str(raw)
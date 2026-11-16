from datetime import date

import pandas as pd

from config.field_values import combine_person_values
from config.model_fields import FIELDS_BY_KEY, fields_for_scope
from model.accounts import ledger_from_persons
from model.engine import (
    MAX_SS_TAXABLE_FRACTION,
    PersonFlows,
    TSLA_BASELINE_PRICE,
    VALID_TAX_CALCULATIONS,
    additive_columns,
    inflate_flows,
    simulate_year,
    tax_policy_from_params,
)
from model.expenses import expense_policy_from_params
from model.filing import resolve_year_filing
from model.aca import aca_policy_from_params
from model.medicare import medicare_policy_from_params
from model.state_residence import resolve_year_state
from model.cpa_tax import normalize_planning_precision
from model.state_taxes import (
    canonical_state_code,
    is_supported_state,
    normalize_state_code,
    state_fallback_message,
)
from model.taxes import VALID_FILING_STATUSES, VALID_SS_TAX_METHODS, normalize_filing_status

SP500_REBALANCE_RETURN = 0.07  # re-export for tests


def _validate_params(params):
    """Validate projection inputs before running."""
    if params.get("mode") not in ("joint", "separate"):
        raise ValueError("Mode must be 'joint' or 'separate'")

    persons = params.get("persons")
    if not persons:
        raise ValueError("At least one person must be provided")

    life_expectancy = params.get("life_expectancy", 95)
    if life_expectancy <= 0:
        raise ValueError("Life expectancy must be positive")

    for rate_name in ("pre_retire_tax_rate", "post_retire_tax_rate"):
        rate = params.get(rate_name, 0)
        if rate < 0 or rate >= 1:
            raise ValueError(f"{rate_name} must be between 0% and 100% (exclusive)")

    for pct_name in ("roth_portfolio_pct", "contribution_roth_pct"):
        rate = params.get(pct_name, 0) or 0
        if rate < 0 or rate > 1:
            raise ValueError(f"{pct_name} must be between 0% and 100%")

    ss_tax = params.get("ss_taxable_fraction", 0.50) or 0
    if ss_tax < 0 or ss_tax > MAX_SS_TAXABLE_FRACTION:
        raise ValueError(
            f"ss_taxable_fraction must be between 0% and {MAX_SS_TAXABLE_FRACTION:.0%}"
        )

    rmd_start_age = int(params.get("rmd_start_age", 73) or 73)
    if rmd_start_age < 70 or rmd_start_age > 75:
        raise ValueError("rmd_start_age must be between 70 and 75")

    tax_calc = str(params.get("tax_calculation", "federal_brackets") or "federal_brackets").strip().lower()
    if tax_calc not in VALID_TAX_CALCULATIONS:
        raise ValueError("tax_calculation must be 'flat' or 'federal_brackets'")

    filing_status = normalize_filing_status(str(params.get("filing_status", "mfj") or "mfj"))
    if filing_status not in VALID_FILING_STATUSES:
        raise ValueError("filing_status must be 'mfj', 'single', or 'head_of_household'")

    ss_tax_method = str(params.get("ss_tax_method", "irs_provisional") or "irs_provisional").strip().lower()
    if ss_tax_method not in VALID_SS_TAX_METHODS:
        raise ValueError("ss_tax_method must be 'irs_provisional' or 'flat_fraction'")

    marriage_year = params.get("marriage_year")
    if marriage_year not in (None, "", 0, "0"):
        marriage_year = int(marriage_year)
        if marriage_year >= 1900:
            current_year = int(params.get("current_year", date.today().year))
            if marriage_year < current_year - 1:
                raise ValueError("marriage_year should not be more than one year in the past")

    for status_name in ("filing_status_before_marriage", "filing_status_after_marriage"):
        raw = params.get(status_name)
        if raw:
            normalized = normalize_filing_status(str(raw))
            if normalized not in VALID_FILING_STATUSES:
                raise ValueError(f"{status_name} must be 'mfj', 'single', or 'head_of_household'")

    for state_name in ("state_of_residence", "state_after_move"):
        raw = params.get(state_name)
        if raw:
            normalize_state_code(str(raw))

    state_move_year = params.get("state_move_year")
    if state_move_year not in (None, "", 0, "0"):
        state_move_year = int(state_move_year)
        # 0 / blank years mean "no move planned"
        if state_move_year >= 1900:
            current_year = int(params.get("current_year", date.today().year))
            if state_move_year < current_year - 1:
                raise ValueError("state_move_year should not be more than one year in the past")

    state_override = params.get("state_tax_override_pct")
    has_state_override = state_override not in (None, "")
    if has_state_override:
        override_val = float(state_override)
        if override_val < 0 or override_val >= 1:
            raise ValueError("state_tax_override_pct must be between 0% and 100% (exclusive)")

    precision = normalize_planning_precision(params.get("planning_precision"))
    if precision == "cpa" and str(params.get("state_tax_enabled", "True")).lower() != "false":
        for label, key in (
            ("state_of_residence", "state_of_residence"),
            ("state_after_move", "state_after_move"),
        ):
            raw = params.get(key)
            if raw in (None, ""):
                continue
            code = canonical_state_code(str(raw))
            if not is_supported_state(code) and not has_state_override:
                raise ValueError(
                    f"CPA precision requires a modeled state or State Tax Override % "
                    f"({label}={code!r} is not in the state catalog)"
                )

    medicare_start_age = int(params.get("medicare_start_age", 65) or 65)
    if medicare_start_age < 65 or medicare_start_age > 70:
        raise ValueError("medicare_start_age must be between 65 and 70")

    lookback = int(params.get("medicare_irmaa_lookback_years", 2) or 2)
    if lookback < 0 or lookback > 3:
        raise ValueError("medicare_irmaa_lookback_years must be between 0 and 3")

    for person_name, person in persons.items():
        current_age = person["current_age"]
        retirement_age = person.get("retirement_age", 65)
        if current_age < 0:
            raise ValueError(f"{person_name}: current age must be non-negative")
        if retirement_age < 0:
            raise ValueError(f"{person_name}: retirement age must be non-negative")
        if current_age > life_expectancy:
            raise ValueError(
                f"{person_name}: current age ({current_age}) exceeds life expectancy ({life_expectancy})"
            )
        person_filing = person.get("filing_status_before_marriage")
        if person_filing:
            normalized = normalize_filing_status(str(person_filing))
            if normalized not in VALID_FILING_STATUSES:
                raise ValueError(
                    f"{person_name}: filing_status_before_marriage must be "
                    "'mfj', 'single', or 'head_of_household'"
                )
        share = person.get("asset_share_pct")
        if share not in (None, ""):
            share_val = float(share)
            if share_val < 0 or share_val > 1:
                raise ValueError(f"{person_name}: asset_share_pct must be between 0% and 100%")


def collect_param_warnings(params: dict) -> list[str]:
    """Non-blocking warnings for standard-mode inputs that use planning approximations."""
    warnings: list[str] = []
    precision = normalize_planning_precision(params.get("planning_precision"))
    if precision != "standard":
        return warnings
    if str(params.get("state_tax_enabled", "True")).lower() == "false":
        return warnings
    for label, key in (
        ("State of residence", "state_of_residence"),
        ("State after move", "state_after_move"),
    ):
        raw = params.get(key)
        if raw in (None, ""):
            continue
        message = state_fallback_message(str(raw))
        if message:
            warnings.append(f"{label}: {message}")
    return warnings


def _combine_joint_persons(persons):
    """Aggregate person fields via catalog combine rules (display / legacy only)."""
    combined: dict[str, object] = {}
    for field in fields_for_scope("person"):
        values = [
            person.get(field.key, field.default_for(person_name))
            for person_name, person in persons.items()
        ]
        combined[field.key] = combine_person_values(field, values)
    return combined


def _projection_kwargs(params):
    """Shared deterministic / stochastic projection settings from ``params``."""
    tax = tax_policy_from_params(params)
    medicare = medicare_policy_from_params(params)
    aca = aca_policy_from_params(params)
    expense = expense_policy_from_params(params)
    return {
        "current_year": params.get("current_year", date.today().year),
        "life_expectancy": params.get("life_expectancy", 95),
        "expense": expense,
        "inflation_rate": params.get("inflation_rate", 0.03),
        "investment_return_mean": params.get("investment_return_mean", 0.07),
        "tax": tax,
        "medicare": medicare,
        "aca": aca,
        "doomsday_year": params.get("doomsday_drop_year"),
        "doomsday_pct": params.get("doomsday_drop_pct", 0.25),
        "rebalance": params.get("rebalance_to_sp500", False),
        "tsla_target_price": params.get("tsla_target_price"),
        "tsla_target_year": params.get("tsla_target_year"),
        "tsla_current_price": params.get("tsla_current_price", TSLA_BASELINE_PRICE),
        "data_sources": params.get("data_sources", {}),
    }


def _horizon_start_age(person_or_persons) -> int:
    if isinstance(person_or_persons, dict) and "current_age" in person_or_persons:
        return int(person_or_persons["current_age"])
    return min(int(person["current_age"]) for person in person_or_persons.values())


def run_retirement_projection(params):
    """
    Deterministic year-by-year retirement projection (joint household or separate persons).

    Joint mode uses one asset pool, per-person ages, partial retirement, Roth/tax-aware
    withdrawals, and COLA on Social Security and pension benefits.
    """
    _validate_params(params)

    settings = _projection_kwargs(params)
    persons = params["persons"]
    mode = params["mode"]

    results = {}
    if mode == "joint":
        results["joint"] = _project_for_household(persons, **settings)
    else:
        for person_name, person_params in persons.items():
            person = {
                **person_params,
                "retirement_age": person_params.get("retirement_age", 65),
            }
            results[person_name] = _project_for_person(person, **settings)

    return results


def _build_row(
    *,
    year: int,
    age: int,
    assets_start: float,
    year_result,
    include_notes: bool,
    extra_notes: list[str],
    data_sources: dict,
    year_return: float,
    year_inflation: float,
) -> dict:
    row = {
        "Year": year,
        "Age": age,
        "Phase": year_result.phase,
        "Assets_Start": assets_start,
        "Contributions": year_result.contributions,
        "Returns": year_result.returns,
        "Withdrawals_Gross": year_result.withdrawals_gross,
        "RMD": year_result.rmd,
        "Earned_Income": year_result.earned_income,
        "Expenses": year_result.expenses,
        **additive_columns(year_result.additive_totals),
        "Rental_Income": year_result.rental_income,
        "Pension": year_result.pension,
        "SS": year_result.ss_gross,
        "SS_PIA": year_result.ss_pia_monthly,
        "SS_FRA": year_result.ss_fra_years,
        "SS_Taxable": year_result.ss_taxable,
        "Other_Income": year_result.other_income,
        "Taxes": year_result.taxes,
        "Federal_Taxes": year_result.federal_taxes,
        "State_Taxes": year_result.state_taxes,
        "FICA": year_result.fica_taxes,
        "QCD": year_result.qcd,
        "Roth_Conversion": year_result.roth_conversion,
        "LTC_Cost": year_result.ltc_cost,
        "Expense_Phase": year_result.expense_phase,
        "Planning_Precision": year_result.planning_precision,
        "Deduction_Method": year_result.deduction_method,
        "AMT_Surcharge": year_result.amt_surcharge,
        "Federal_Deduction": year_result.federal_deduction,
        "Tax_Mode": year_result.tax_mode,
        "Filing_Layout": year_result.filing_layout,
        "Filing_Status": year_result.filing_status_label,
        "Tax_State": year_result.tax_state_label,
        "Medicare": year_result.medicare_total,
        "Medicare_IRMAA": year_result.medicare_irmaa,
        "Medicare_Phase": year_result.medicare_phase,
        "Health_Pre-Medicare": year_result.health_pre_medicare,
        "Health_Post-Medicare": year_result.health_post_medicare,
        "ACA_Gross": year_result.aca_gross,
        "ACA_Subsidy": year_result.aca_subsidy,
        "ACA_Net": year_result.aca_net,
        "ACA_SLCSP": year_result.aca_slcsp,
        "Employer_Health": year_result.employer_health,
        "Medicare_Medigap": year_result.medicare_medigap,
        "MAGI": year_result.magi,
        "Assets_Taxable": year_result.assets_taxable,
        "Assets_Traditional": year_result.assets_traditional,
        "Assets_Roth": year_result.assets_roth,
        "Assets_End": year_result.assets_end,
    }
    if include_notes:
        notes = list(year_result.notes) + list(extra_notes)
        notes.append(
            f"Returns rate={year_return:.2%}; inflation={year_inflation:.2%}"
        )
        for label, detail in data_sources.items():
            if detail:
                notes.append(f"{label}: {detail}")
        row["Formula_Notes"] = "; ".join(notes)
    return row


def _project_for_household(
    persons: dict[str, dict],
    *,
    current_year,
    life_expectancy,
    inflation_rate,
    investment_return_mean,
    tax,
    medicare,
    aca,
    expense,
    doomsday_year,
    doomsday_pct,
    rebalance,
    tsla_target_price,
    tsla_target_year,
    tsla_current_price,
    data_sources,
    annual_return_rates=None,
    annual_inflation_rates=None,
    include_notes=True,
):
    anchor_start_age = _horizon_start_age(persons)
    start_year = current_year
    end_year = current_year + (life_expectancy - anchor_start_age)
    years = range(start_year, end_year + 1)

    ledger = ledger_from_persons(
        persons,
        roth_portfolio_pct=max(0.0, min(tax.roth_portfolio_pct, 1.0)),
        household_mode=True,
    )
    flows = {
        name: PersonFlows.from_person(person, current_year=current_year)
        for name, person in persons.items()
    }
    rental_growth = combine_person_values(
        FIELDS_BY_KEY["rental_growth_rate"],
        [person.get("rental_growth_rate", 0.03) for person in persons.values()],
    )
    current_return = investment_return_mean
    rows = []
    inflation_factor = 1.0
    ltc_inflation_factor = 1.0
    magi_by_year: dict[int, float] = {}

    for year_index, year in enumerate(years):
        year_return = (
            annual_return_rates[year_index]
            if annual_return_rates is not None and year_index < len(annual_return_rates)
            else current_return
        )
        year_inflation = (
            annual_inflation_rates[year_index]
            if annual_inflation_rates is not None and year_index < len(annual_inflation_rates)
            else inflation_rate
        )
        year_ltc_inflation = expense.ltc_inflation_rate

        ages = {name: int(person["current_age"]) + year_index for name, person in persons.items()}
        anchor_age = anchor_start_age + year_index
        assets_start = ledger.household_total.total
        age_notes = [f"Household ages: {', '.join(f'{n}={ages[n]}' for n in sorted(persons))}"]
        irmaa_magi = None
        if medicare.irmaa_lookback_years > 0:
            irmaa_magi = magi_by_year.get(year - medicare.irmaa_lookback_years)
        filing = resolve_year_filing(
            year=year,
            marriage_year=tax.marriage_year,
            filing_before=tax.filing_status_before_marriage,
            filing_after=tax.filing_status_after_marriage,
            persons=persons,
            household_mode=True,
            legacy_filing_status=tax.filing_status,
        )
        state_ctx = resolve_year_state(
            year=year,
            state_residence=tax.state_residence,
            state_move_year=tax.state_move_year,
            state_after_move=tax.state_after_move,
        )

        ledger, year_result = simulate_year(
            ledger=ledger,
            household_mode=True,
            persons=persons,
            flows=flows,
            ages=ages,
            year=year,
            year_return=year_return,
            tax=tax,
            filing=filing,
            state=state_ctx,
            medicare=medicare,
            aca=aca,
            irmaa_magi=irmaa_magi,
            inflation_factor=inflation_factor,
            doomsday_year=doomsday_year,
            doomsday_pct=doomsday_pct,
            rebalance=rebalance,
            tsla_target_year=tsla_target_year,
            tsla_target_price=tsla_target_price,
            tsla_current_price=tsla_current_price,
            current_return=current_return,
            anchor_year=current_year,
            expense_policy=expense,
            ltc_inflation_factor=ltc_inflation_factor,
            household_life_expectancy=life_expectancy,
            include_tax_metadata=include_notes,
        )
        magi_by_year[year] = year_result.magi

        rows.append(
            _build_row(
                year=year,
                age=anchor_age,
                assets_start=assets_start,
                year_result=year_result,
                include_notes=include_notes,
                extra_notes=age_notes,
                data_sources=data_sources,
                year_return=year_return,
                year_inflation=year_inflation,
            )
        )

        for name in persons:
            inflate_flows(
                flows[name],
                year_inflation=year_inflation,
                rental_growth=float(rental_growth),
            )
        inflation_factor *= 1 + year_inflation
        ltc_inflation_factor *= 1 + year_ltc_inflation

    return pd.DataFrame(rows)


def _project_for_person(
    person,
    *,
    current_year,
    life_expectancy,
    inflation_rate,
    investment_return_mean,
    tax,
    medicare,
    aca,
    expense,
    doomsday_year,
    doomsday_pct,
    rebalance,
    tsla_target_price,
    tsla_target_year,
    tsla_current_price,
    data_sources,
    annual_return_rates=None,
    annual_inflation_rates=None,
    include_notes=True,
):
    start_year = current_year
    end_year = current_year + (life_expectancy - person["current_age"])
    years = range(start_year, end_year + 1)

    persons = {"self": person}
    ledger = ledger_from_persons(
        persons,
        roth_portfolio_pct=max(0.0, min(tax.roth_portfolio_pct, 1.0)),
        household_mode=False,
    )
    flows = {"self": PersonFlows.from_person(person, current_year=current_year)}
    rental_growth = float(person.get("rental_growth_rate", 0.03))
    current_return = investment_return_mean
    rows = []
    inflation_factor = 1.0
    ltc_inflation_factor = 1.0
    magi_by_year: dict[int, float] = {}

    for year_index, year in enumerate(years):
        year_return = (
            annual_return_rates[year_index]
            if annual_return_rates is not None and year_index < len(annual_return_rates)
            else current_return
        )
        year_inflation = (
            annual_inflation_rates[year_index]
            if annual_inflation_rates is not None and year_index < len(annual_inflation_rates)
            else inflation_rate
        )
        year_ltc_inflation = expense.ltc_inflation_rate

        age = person["current_age"] + (year - current_year)
        assets_start = ledger.household_total.total
        ages = {"self": age}
        irmaa_magi = None
        if medicare.irmaa_lookback_years > 0:
            irmaa_magi = magi_by_year.get(year - medicare.irmaa_lookback_years)
        filing = resolve_year_filing(
            year=year,
            marriage_year=tax.marriage_year,
            filing_before=tax.filing_status_before_marriage,
            filing_after=tax.filing_status_after_marriage,
            persons=persons,
            household_mode=False,
            legacy_filing_status=tax.filing_status,
        )
        state_ctx = resolve_year_state(
            year=year,
            state_residence=tax.state_residence,
            state_move_year=tax.state_move_year,
            state_after_move=tax.state_after_move,
        )

        ledger, year_result = simulate_year(
            ledger=ledger,
            household_mode=False,
            persons=persons,
            flows=flows,
            ages=ages,
            year=year,
            year_return=year_return,
            tax=tax,
            filing=filing,
            state=state_ctx,
            medicare=medicare,
            aca=aca,
            irmaa_magi=irmaa_magi,
            inflation_factor=inflation_factor,
            doomsday_year=doomsday_year,
            doomsday_pct=doomsday_pct,
            rebalance=rebalance,
            tsla_target_year=tsla_target_year,
            tsla_target_price=tsla_target_price,
            tsla_current_price=tsla_current_price,
            current_return=current_return,
            anchor_year=current_year,
            expense_policy=expense,
            ltc_inflation_factor=ltc_inflation_factor,
            household_life_expectancy=life_expectancy,
            include_tax_metadata=include_notes,
        )
        magi_by_year[year] = year_result.magi

        rows.append(
            _build_row(
                year=year,
                age=age,
                assets_start=assets_start,
                year_result=year_result,
                include_notes=include_notes,
                extra_notes=[],
                data_sources=data_sources,
                year_return=year_return,
                year_inflation=year_inflation,
            )
        )

        inflate_flows(
            flows["self"],
            year_inflation=year_inflation,
            rental_growth=rental_growth,
        )
        inflation_factor *= 1 + year_inflation
        ltc_inflation_factor *= 1 + year_ltc_inflation

    return pd.DataFrame(rows)


if __name__ == "__main__":
    test_params = {
        "mode": "joint",
        "current_year": 2026,
        "persons": {
            "self": {
                "current_age": 40,
                "retirement_age": 65,
                "initial_assets": 500000,
                "annual_contributions": 20000,
                "base_expenses": 60000,
                "rental_income_start": 12000,
                "rental_growth_rate": 0.03,
                "pension_start_age": 65,
                "pension_amount": 0,
                "ss_start_age": 67,
                "ss_amount": 25000,
            },
            "wife": {
                "current_age": 40,
                "retirement_age": 65,
                "initial_assets": 300000,
                "annual_contributions": 15000,
                "base_expenses": 60000,
                "rental_income_start": 0,
                "rental_growth_rate": 0.03,
                "pension_start_age": 65,
                "pension_amount": 0,
                "ss_start_age": 67,
                "ss_amount": 20000,
            },
        },
        "life_expectancy": 95,
        "inflation_rate": 0.03,
        "investment_return_mean": 0.07,
        "pre_retire_tax_rate": 0.35,
        "post_retire_tax_rate": 0.20,
        "roth_portfolio_pct": 0.15,
        "contribution_roth_pct": 0.20,
        "ss_taxable_fraction": 0.50,
        "tsla_target_price": None,
        "tsla_target_year": None,
        "doomsday_drop_year": None,
        "doomsday_drop_pct": 0.25,
        "rebalance_to_sp500": False,
    }
    results = run_retirement_projection(test_params)
    joint = results["joint"]
    print(f"Projection years: {joint['Year'].iloc[0]} - {joint['Year'].iloc[-1]} ({len(joint)} rows)")
    print(joint[["Year", "Age", "Phase", "Assets_Start", "Assets_End"]].head(3))
    print("...")
    print(joint[["Year", "Age", "Phase", "Expenses", "Assets_End"]].tail(5))
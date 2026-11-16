"""Multi-account portfolio ledger (taxable brokerage, traditional IRA, Roth IRA)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_WITHDRAWAL_ORDERS = frozenset({"proportional", "tax_efficient"})


@dataclass
class AccountBalances:
    taxable: float = 0.0
    traditional: float = 0.0
    roth: float = 0.0

    @property
    def total(self) -> float:
        return self.taxable + self.traditional + self.roth

    def roth_fraction(self) -> float:
        total = self.total
        if total <= 0:
            return 0.0
        return self.roth / total

    def ordinary_fraction(self) -> float:
        """Share of proportional withdrawals taxed as ordinary income (Phase 0)."""
        total = self.total
        if total <= 0:
            return 0.0
        return (self.traditional + self.taxable) / total

    def copy(self) -> AccountBalances:
        return AccountBalances(self.taxable, self.traditional, self.roth)


@dataclass
class WithdrawalAllocation:
    gross: float
    from_taxable: float
    from_traditional: float
    from_roth: float

    @property
    def ordinary_income(self) -> float:
        """Ordinary-income portion of withdrawal (Phase 0: taxable + traditional)."""
        return self.from_taxable + self.from_traditional


@dataclass
class HouseholdLedger:
    """Per-person account buckets; joint mode uses pooled totals via ``household_total``."""

    by_person: dict[str, AccountBalances] = field(default_factory=dict)

    @property
    def household_total(self) -> AccountBalances:
        total = AccountBalances()
        for balances in self.by_person.values():
            total.taxable += balances.taxable
            total.traditional += balances.traditional
            total.roth += balances.roth
        return total

    def person_share(self, name: str, persons: dict[str, dict[str, Any]]) -> AccountBalances:
        """Person balances for separate-filer RMD; falls back to ownership share of pool."""
        if name in self.by_person and self.by_person[name].total > 0:
            return self.by_person[name]
        from model.filing import asset_shares

        share = asset_shares(persons)[name]
        pool = self.household_total
        return AccountBalances(
            taxable=pool.taxable * share,
            traditional=pool.traditional * share,
            roth=pool.roth * share,
        )


def _explicit_buckets(person: dict[str, Any]) -> AccountBalances | None:
    taxable = person.get("initial_taxable_assets")
    traditional = person.get("initial_traditional_ira")
    roth = person.get("initial_roth_ira")
    if traditional in (None, "") and roth in (None, ""):
        if taxable in (None, ""):
            return None
        if float(taxable or 0) == 0:
            return None
    return AccountBalances(
        taxable=float(taxable or 0),
        traditional=float(traditional or 0),
        roth=float(roth or 0),
    )


def _legacy_buckets(person: dict[str, Any], *, roth_portfolio_pct: float) -> AccountBalances:
    total = max(0.0, float(person.get("initial_assets", 0) or 0))
    roth_share = max(0.0, min(roth_portfolio_pct, 1.0))
    roth = total * roth_share
    traditional = total - roth
    return AccountBalances(taxable=0.0, traditional=traditional, roth=roth)


def ledger_from_persons(
    persons: dict[str, dict[str, Any]],
    *,
    roth_portfolio_pct: float,
    household_mode: bool,
) -> HouseholdLedger:
    """Initialize ledger from person inputs (explicit buckets or legacy initial_assets split)."""
    per_person = {
        name: _explicit_buckets(person) or _legacy_buckets(person, roth_portfolio_pct=roth_portfolio_pct)
        for name, person in persons.items()
    }
    if household_mode:
        pooled = AccountBalances()
        for balances in per_person.values():
            pooled.taxable += balances.taxable
            pooled.traditional += balances.traditional
            pooled.roth += balances.roth
        return HouseholdLedger(by_person={"__household__": pooled})
    return HouseholdLedger(by_person=per_person)


def _active_balances(ledger: HouseholdLedger, household_mode: bool) -> AccountBalances:
    if household_mode:
        return ledger.by_person.get("__household__", AccountBalances())
    return ledger.household_total


def apply_contributions(
    ledger: HouseholdLedger,
    *,
    household_mode: bool,
    person_contributions: dict[str, tuple[float, float, float]],
) -> None:
    """Apply (deposit, traditional, roth) per person to account buckets."""
    if household_mode:
        pool = ledger.by_person.setdefault("__household__", AccountBalances())
        for _name, (_deposit, traditional, roth) in person_contributions.items():
            pool.traditional += traditional
            pool.roth += roth
        return
    for name, (_deposit, traditional, roth) in person_contributions.items():
        balances = ledger.by_person.setdefault(name, AccountBalances())
        balances.traditional += traditional
        balances.roth += roth


def apply_returns(
    ledger: HouseholdLedger,
    *,
    household_mode: bool,
    return_rate: float,
    contribution_deposit: float,
) -> float:
    """Apply investment returns; return total dollar gain."""
    balances = _active_balances(ledger, household_mode)
    start = balances.total
    gain_on_start = start * return_rate
    gain_on_contrib = contribution_deposit * return_rate * 0.5
    total_gain = gain_on_start + gain_on_contrib
    if start + contribution_deposit <= 0:
        return 0.0
    growth_factor = 1.0 + total_gain / (start + contribution_deposit)
    balances.taxable *= growth_factor
    balances.traditional *= growth_factor
    balances.roth *= growth_factor
    return total_gain


def allocate_withdrawal_proportional(
    balances: AccountBalances,
    gross: float,
) -> WithdrawalAllocation:
    """Proportional withdrawal from each bucket."""
    if gross <= 0 or balances.total <= 0:
        return WithdrawalAllocation(0.0, 0.0, 0.0, 0.0)
    share = gross / balances.total
    from_taxable = balances.taxable * share
    from_traditional = balances.traditional * share
    from_roth = balances.roth * share
    return WithdrawalAllocation(
        gross=gross,
        from_taxable=from_taxable,
        from_traditional=from_traditional,
        from_roth=from_roth,
    )


def allocate_withdrawal(
    balances: AccountBalances,
    gross: float,
    *,
    order: str = "tax_efficient",
    rmd_floor: float = 0.0,
    qcd: float = 0.0,
) -> WithdrawalAllocation:
    """Withdraw from portfolio using the selected draw order."""
    if order == "proportional":
        return allocate_withdrawal_proportional(balances, gross)
    return allocate_withdrawal_tax_efficient(
        balances, gross, rmd_floor=rmd_floor, qcd=qcd
    )


def allocate_withdrawal_tax_efficient(
    balances: AccountBalances,
    gross: float,
    *,
    rmd_floor: float = 0.0,
    qcd: float = 0.0,
) -> WithdrawalAllocation:
    """
    Tax-efficient draw: RMD/QCD from traditional → taxable → traditional → Roth.
    """
    if gross <= 0:
        return WithdrawalAllocation(0.0, 0.0, 0.0, 0.0)

    remaining = gross
    from_traditional = 0.0
    from_taxable = 0.0
    from_roth = 0.0

    if rmd_floor > 0:
        rmd_take = min(remaining, balances.traditional, rmd_floor)
        from_traditional += rmd_take
        remaining -= rmd_take

    if qcd > 0:
        qcd_take = min(remaining, balances.traditional - from_traditional, qcd)
        from_traditional += qcd_take
        remaining -= qcd_take

    from_taxable = min(remaining, balances.taxable)
    remaining -= from_taxable

    trad_extra = min(remaining, balances.traditional - from_traditional)
    from_traditional += trad_extra
    remaining -= trad_extra

    from_roth = min(remaining, balances.roth)

    return WithdrawalAllocation(
        gross=gross,
        from_taxable=from_taxable,
        from_traditional=from_traditional,
        from_roth=from_roth,
    )


def _decrement_balances(balances: AccountBalances, allocation: WithdrawalAllocation) -> None:
    balances.taxable = max(0.0, balances.taxable - allocation.from_taxable)
    balances.traditional = max(0.0, balances.traditional - allocation.from_traditional)
    balances.roth = max(0.0, balances.roth - allocation.from_roth)


def apply_withdrawal(
    ledger: HouseholdLedger,
    *,
    household_mode: bool,
    gross: float,
    person_name: str | None = None,
    order: str = "tax_efficient",
    rmd_floor: float = 0.0,
    qcd: float = 0.0,
    allocation: WithdrawalAllocation | None = None,
) -> WithdrawalAllocation:
    """Reduce balances after a withdrawal; return allocation breakdown."""
    if household_mode:
        balances = ledger.by_person.setdefault("__household__", AccountBalances())
    else:
        assert person_name is not None
        balances = ledger.by_person.setdefault(person_name, AccountBalances())
    if allocation is None:
        allocation = allocate_withdrawal(
            balances,
            gross,
            order=order,
            rmd_floor=rmd_floor,
            qcd=qcd,
        )
    _decrement_balances(balances, allocation)
    return allocation


def roth_fraction_for_tax(ledger: HouseholdLedger, *, household_mode: bool) -> float:
    return _active_balances(ledger, household_mode).roth_fraction()
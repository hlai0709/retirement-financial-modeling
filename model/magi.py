"""Unified MAGI worksheet — shared by federal tax, ACA, IRMAA, and NIIT (Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass

from model.taxes import taxable_social_security


@dataclass(frozen=True)
class MagiWorksheet:
    """IRS Form 1040–style income worksheet for Modified AGI."""

    earned_income: float = 0.0
    pension_income: float = 0.0
    rental_income: float = 0.0
    ss_gross: float = 0.0
    traditional_withdrawal: float = 0.0
    taxable_account_withdrawal: float = 0.0
    roth_conversion: float = 0.0
    tax_exempt_interest: float = 0.0
    foreign_excluded_income: float = 0.0

    @property
    def ordinary_income(self) -> float:
        return (
            self.earned_income
            + self.pension_income
            + self.rental_income
            + self.traditional_withdrawal
            + self.taxable_account_withdrawal
            + self.roth_conversion
        )

    def taxable_ss(
        self,
        *,
        filing_status: str,
        inflation_factor: float,
        ss_tax_method: str,
        ss_taxable_fraction: float,
        mfs_lived_apart: bool = False,
    ) -> float:
        return taxable_social_security(
            self.ss_gross,
            other_income=self.ordinary_income,
            filing_status=filing_status,
            inflation_factor=inflation_factor,
            ss_tax_method=ss_tax_method,
            ss_taxable_fraction=ss_taxable_fraction,
            mfs_lived_apart=mfs_lived_apart,
        )

    def agi(
        self,
        *,
        filing_status: str,
        inflation_factor: float,
        ss_tax_method: str,
        ss_taxable_fraction: float,
    ) -> float:
        return self.ordinary_income + self.taxable_ss(
            filing_status=filing_status,
            inflation_factor=inflation_factor,
            ss_tax_method=ss_tax_method,
            ss_taxable_fraction=ss_taxable_fraction,
        )

    def magi(
        self,
        *,
        filing_status: str,
        inflation_factor: float,
        ss_tax_method: str,
        ss_taxable_fraction: float,
    ) -> float:
        """MAGI = AGI + tax-exempt interest + excluded foreign income (simplified)."""
        return (
            self.agi(
                filing_status=filing_status,
                inflation_factor=inflation_factor,
                ss_tax_method=ss_tax_method,
                ss_taxable_fraction=ss_taxable_fraction,
            )
            + self.tax_exempt_interest
            + self.foreign_excluded_income
        )


def build_magi_worksheet(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    withdrawal_gross: float,
    roth_fraction: float,
    tax_exempt_interest: float = 0.0,
    roth_conversion: float = 0.0,
    foreign_excluded_income: float = 0.0,
) -> MagiWorksheet:
    """
    Build worksheet from portfolio withdrawal mix.

    ``roth_fraction`` is the share of the portfolio balance in Roth (tax-free withdrawals).
    Non-Roth withdrawals split between traditional IRA and taxable brokerage by balance share
    when explicit breakdown is not supplied.
    """
    roth_share = max(0.0, min(roth_fraction, 1.0))
    ordinary_share = 1.0 - roth_share
    ordinary_withdrawal = withdrawal_gross * ordinary_share
    return MagiWorksheet(
        earned_income=earned_income,
        pension_income=pension_gross,
        rental_income=rental_gross,
        ss_gross=ss_gross,
        traditional_withdrawal=ordinary_withdrawal,
        taxable_account_withdrawal=0.0,
        roth_conversion=roth_conversion,
        tax_exempt_interest=tax_exempt_interest,
        foreign_excluded_income=foreign_excluded_income,
    )


def build_magi_worksheet_from_accounts(
    *,
    ss_gross: float,
    pension_gross: float,
    rental_gross: float,
    earned_income: float,
    withdrawal_gross: float,
    withdrawal_traditional: float,
    withdrawal_taxable: float,
    tax_exempt_interest: float = 0.0,
    roth_conversion: float = 0.0,
    foreign_excluded_income: float = 0.0,
) -> MagiWorksheet:
    """Account-aware worksheet with explicit traditional vs taxable brokerage draws."""
    return MagiWorksheet(
        earned_income=earned_income,
        pension_income=pension_gross,
        rental_income=rental_gross,
        ss_gross=ss_gross,
        traditional_withdrawal=withdrawal_traditional,
        taxable_account_withdrawal=withdrawal_taxable,
        roth_conversion=roth_conversion,
        tax_exempt_interest=tax_exempt_interest,
        foreign_excluded_income=foreign_excluded_income,
    )


def magi_and_taxable_ss(
    worksheet: MagiWorksheet,
    *,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
    mfs_lived_apart: bool = False,
) -> tuple[float, float]:
    """Return (MAGI, taxable Social Security portion)."""
    ss_taxable = worksheet.taxable_ss(
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        ss_tax_method=ss_tax_method,
        ss_taxable_fraction=ss_taxable_fraction,
        mfs_lived_apart=mfs_lived_apart,
    )
    magi = worksheet.magi(
        filing_status=filing_status,
        inflation_factor=inflation_factor,
        ss_tax_method=ss_tax_method,
        ss_taxable_fraction=ss_taxable_fraction,
    )
    return magi, ss_taxable


def provisional_income(
    worksheet: MagiWorksheet,
    *,
    filing_status: str,
    inflation_factor: float,
    ss_tax_method: str,
    ss_taxable_fraction: float,
) -> float:
    """Combined income for SS taxation (other income + half of SS benefits)."""
    return worksheet.ordinary_income + worksheet.ss_gross * 0.5
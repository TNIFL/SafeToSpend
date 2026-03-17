from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.receipt_tax_effects import ReceiptTaxEffectsSummary
from services.risk import compute_tax_estimate
from services.tax_official_core import compute_tax_official_core


class _QueryStub:
    def __init__(self, *, rows=None, scalar_value=None):
        self._rows = list(rows or [])
        self._scalar_value = scalar_value

    def select_from(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value


def _official_monthly_due(*, taxable_income_annual_krw: int, annual_credit_krw: int = 0) -> int:
    core = compute_tax_official_core(
        taxable_income_annual_krw=int(taxable_income_annual_krw),
        target_year=2026,
    )
    monthly_before_credit = int(round((core.national_tax_annual_krw + core.local_tax_annual_krw) / 12))
    monthly_credit = int(round(max(0, int(annual_credit_krw or 0)) / 12))
    return int(max(0, monthly_before_credit - monthly_credit))


def _accuracy_percent(*, expected: int, actual: int) -> float:
    exp = int(max(0, expected))
    act = int(max(0, actual))
    if exp <= 0:
        return 100.0 if act <= 0 else 0.0
    diff = abs(exp - act)
    return max(0.0, 100.0 - ((diff / exp) * 100.0))


def _band(accuracy_pct: float) -> str:
    if accuracy_pct >= 99.0:
        return "99%+"
    if accuracy_pct >= 95.0:
        return "95~99%"
    return "<95%"


class TaxAccuracyCasesTest(unittest.TestCase):
    def _compute(
        self,
        *,
        profile: dict,
        income_rows: list[tuple[int, str, str, str]],
        expense_business_krw: int,
    ):
        zero_effects = ReceiptTaxEffectsSummary(
            reflected_expense_krw=0,
            pending_review_expense_krw=0,
            excluded_expense_krw=0,
            consult_tax_review_expense_krw=0,
            reflected_transaction_count=0,
            pending_transaction_count=0,
            excluded_transaction_count=0,
            consult_tax_review_transaction_count=0,
            skipped_manual_transaction_count=0,
            evaluated_transaction_count=0,
            entries=(),
        )
        with (
            patch("services.risk._get_settings", return_value=SimpleNamespace(default_tax_rate=0.15)),
            patch("services.risk.get_tax_profile", return_value=dict(profile)),
            patch("services.risk.is_tax_profile_complete", return_value=True),
            patch(
                "services.risk.pick_income_override_for_month",
                return_value={"applied": False, "entry": None, "target_year": 2026, "used_year": None},
            ),
            patch("services.risk.aggregate_income_override", return_value={}),
            patch("services.risk.compute_receipt_tax_effects_for_month", return_value=zero_effects),
            patch(
                "services.risk.db.session.query",
                side_effect=[
                    _QueryStub(rows=income_rows),
                    _QueryStub(scalar_value=int(expense_business_krw)),
                    _QueryStub(scalar_value=0),
                ],
            ),
        ):
            return compute_tax_estimate(user_pk=1, month_key="2026-03")

    def test_tax_accuracy_case_table(self) -> None:
        cases = [
            {
                "id": "TAX-ACC-01",
                "profile": {
                    "official_taxable_income_annual_krw": 20_000_000,
                    "withholding_3_3": "no",
                    "industry_group": "it",
                    "prev_income_band": "30m_80m",
                },
                "income_rows": [(2_000_000, "A", "정산", "income")],
                "expense_business_krw": 300_000,
                "official_taxable_true": 20_000_000,
                "annual_credit_true": 0,
                "expected_band": "99%+",
            },
            {
                "id": "TAX-ACC-02",
                "profile": {
                    "official_taxable_income_annual_krw": 48_000_000,
                    "withholding_3_3": "yes",
                    "withheld_tax_annual_krw": 900_000,
                    "prepaid_tax_annual_krw": 300_000,
                    "industry_group": "it",
                    "prev_income_band": "30m_80m",
                },
                "income_rows": [(4_000_000, "B", "원천", "income")],
                "expense_business_krw": 800_000,
                "official_taxable_true": 48_000_000,
                "annual_credit_true": 1_200_000,
                "expected_band": "99%+",
            },
            {
                "id": "TAX-ACC-03",
                "profile": {
                    "official_taxable_income_annual_krw": 36_000_000,
                    "withholding_3_3": "yes",
                    "industry_group": "it",
                    "prev_income_band": "30m_80m",
                },
                "income_rows": [(3_000_000, "C", "정산", "income")],
                "expense_business_krw": 500_000,
                "official_taxable_true": 36_000_000,
                "annual_credit_true": int(round(3_000_000 * 0.033 * 12)),
                "expected_band": "99%+",
            },
            {
                "id": "TAX-ACC-04",
                "profile": {
                    "annual_gross_income_krw": 60_000_000,
                    "annual_deductible_expense_krw": 24_000_000,
                    "withholding_3_3": "no",
                    "industry_group": "it",
                    "prev_income_band": "30m_80m",
                },
                "income_rows": [(2_000_000, "D", "정산", "income")],
                "expense_business_krw": 100_000,
                "official_taxable_true": 40_000_000,
                "annual_credit_true": 0,
                "expected_band": "<95%",
            },
            {
                "id": "TAX-ACC-05",
                "profile": {
                    "annual_gross_income_krw": 50_000_000,
                    "withholding_3_3": "unknown",
                    "industry_group": "it",
                    "prev_income_band": "30m_80m",
                },
                "income_rows": [(1_500_000, "E", "정산", "income")],
                "expense_business_krw": 200_000,
                "official_taxable_true": 51_000_000,
                "annual_credit_true": 0,
                "expected_band": "95~99%",
            },
            {
                "id": "TAX-ACC-06",
                "profile": {"withholding_3_3": "unknown"},
                "income_rows": [],
                "expense_business_krw": 0,
                "official_taxable_true": 12_000_000,
                "annual_credit_true": 0,
                "expected_band": "<95%",
            },
        ]

        for case in cases:
            with self.subTest(case=case["id"]):
                est = self._compute(
                    profile=case["profile"],
                    income_rows=case["income_rows"],
                    expense_business_krw=case["expense_business_krw"],
                )
                expected_due = _official_monthly_due(
                    taxable_income_annual_krw=int(case["official_taxable_true"]),
                    annual_credit_krw=int(case["annual_credit_true"]),
                )
                actual_due = int(est.tax_due_est_krw or 0)
                accuracy_pct = _accuracy_percent(expected=int(expected_due), actual=int(actual_due))
                error_krw = abs(int(expected_due) - int(actual_due))

                self.assertEqual(_band(accuracy_pct), str(case["expected_band"]))
                self.assertGreaterEqual(error_krw, 0)
                self.assertGreaterEqual(accuracy_pct, 0.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.receipt_tax_effects import ReceiptTaxEffectsSummary
from services.risk import compute_tax_estimate


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


def _effects(
    *,
    reflected: int = 0,
    pending: int = 0,
    excluded: int = 0,
    consult: int = 0,
    reflected_count: int = 0,
    pending_count: int = 0,
):
    return ReceiptTaxEffectsSummary(
        reflected_expense_krw=int(reflected),
        pending_review_expense_krw=int(pending),
        excluded_expense_krw=int(excluded),
        consult_tax_review_expense_krw=int(consult),
        reflected_transaction_count=int(reflected_count),
        pending_transaction_count=int(pending_count),
        excluded_transaction_count=0,
        consult_tax_review_transaction_count=0,
        skipped_manual_transaction_count=0,
        evaluated_transaction_count=int(reflected_count + pending_count),
        entries=(),
    )


class ReceiptTaxEffectsIntegrationTest(unittest.TestCase):
    def _compute(self, *, receipt_effects: ReceiptTaxEffectsSummary):
        profile = {
            "annual_gross_income_krw": 60_000_000,
            "annual_deductible_expense_krw": 12_000_000,
            "withholding_3_3": "no",
            "income_classification": "business",
            "tax_basic_inputs_confirmed": True,
            "industry_group": "it",
            "prev_income_band": "30m_80m",
            "other_income": "no",
        }
        with (
            patch("services.risk._get_settings", return_value=SimpleNamespace(default_tax_rate=0.15)),
            patch("services.risk.get_tax_profile", return_value=dict(profile)),
            patch("services.risk.is_tax_profile_complete", return_value=True),
            patch(
                "services.risk.pick_income_override_for_month",
                return_value={"applied": False, "entry": None, "target_year": 2026, "used_year": None},
            ),
            patch("services.risk.aggregate_income_override", return_value={}),
            patch("services.risk.compute_receipt_tax_effects_for_month", return_value=receipt_effects),
            patch(
                "services.risk.db.session.query",
                side_effect=[
                    _QueryStub(rows=[(2_000_000, "A", "정산", "income")]),
                    _QueryStub(scalar_value=300_000),
                    _QueryStub(scalar_value=0),
                ],
            ),
        ):
            return compute_tax_estimate(user_pk=1, month_key="2026-03")

    def test_high_likelihood_reflected_expense_reduces_tax(self) -> None:
        baseline = self._compute(receipt_effects=_effects())
        reflected = self._compute(receipt_effects=_effects(reflected=200_000, reflected_count=1))
        self.assertEqual(reflected.receipt_reflected_expense_krw, 200_000)
        self.assertEqual(reflected.expense_business_base_krw, 300_000)
        self.assertEqual(reflected.expense_business_krw, 500_000)
        self.assertLess(reflected.buffer_target_krw, baseline.buffer_target_krw)
        self.assertLess(reflected.tax_delta_from_receipts_krw, 0)

    def test_pending_review_does_not_change_tax_amount(self) -> None:
        baseline = self._compute(receipt_effects=_effects())
        pending = self._compute(receipt_effects=_effects(pending=180_000, pending_count=1))
        self.assertEqual(pending.receipt_pending_expense_krw, 180_000)
        self.assertEqual(pending.buffer_target_krw, baseline.buffer_target_krw)
        self.assertEqual(pending.tax_delta_from_receipts_krw, 0)

    def test_reflection_delta_updates_when_state_changes(self) -> None:
        pending = self._compute(receipt_effects=_effects(pending=180_000, pending_count=1))
        reflected = self._compute(receipt_effects=_effects(reflected=180_000, reflected_count=1))
        self.assertEqual(pending.tax_delta_from_receipts_krw, 0)
        self.assertLess(reflected.tax_delta_from_receipts_krw, 0)
        self.assertGreater(reflected.receipt_reflected_expense_krw, pending.receipt_reflected_expense_krw)


if __name__ == "__main__":
    unittest.main()

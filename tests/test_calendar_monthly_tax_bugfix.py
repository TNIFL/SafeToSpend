from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

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


class CalendarMonthlyTaxBugfixTest(unittest.TestCase):
    def test_month_calendar_route_enables_monthly_signal_mode(self) -> None:
        body = Path("routes/web/web_calendar.py").read_text(encoding="utf-8")
        self.assertIn("prefer_monthly_signal=True", body)

    def test_tax_buffer_route_uses_monthly_signal_mode(self) -> None:
        body = Path("routes/web/calendar/tax.py").read_text(encoding="utf-8")
        self.assertIn("prefer_monthly_signal=True", body)

    def test_review_route_uses_monthly_signal_mode(self) -> None:
        body = Path("routes/web/calendar/review.py").read_text(encoding="utf-8")
        self.assertIn("prefer_monthly_signal=True", body)

    def test_overview_uses_monthly_signal_mode(self) -> None:
        body = Path("services/risk.py").read_text(encoding="utf-8")
        self.assertIn("compute_risk_summary(", body)
        self.assertIn("prefer_monthly_signal=True", body)

    def _compute(
        self,
        *,
        month_key: str,
        income_rows: list[tuple[int, str, str, str]],
        expense_business_krw: int,
        prefer_monthly_signal: bool,
    ):
        profile = {
            "withholding_3_3": "unknown",
            "industry_group": "it",
            "prev_income_band": "30m_80m",
            "other_income": "no",
        }
        pick_payload = {
            "applied": True,
            "entry": {"scope": "tax", "year": 2024},
            "target_year": 2024,
            "used_year": 2024,
        }
        agg_payload = {"annual_total_income_krw": 9_900_000}
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
            patch("services.risk.is_tax_profile_complete", return_value=False),
            patch("services.risk.pick_income_override_for_month", return_value=pick_payload),
            patch("services.risk.aggregate_income_override", return_value=agg_payload),
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
            return compute_tax_estimate(
                user_pk=1,
                month_key=month_key,
                prefer_monthly_signal=prefer_monthly_signal,
            )

    def test_default_proxy_can_stay_fixed_even_if_monthly_transactions_differ(self) -> None:
        jan = self._compute(
            month_key="2026-01",
            income_rows=[(1_000_000, "A", "정산", "income")],
            expense_business_krw=100_000,
            prefer_monthly_signal=False,
        )
        feb = self._compute(
            month_key="2026-02",
            income_rows=[(5_000_000, "B", "정산", "income")],
            expense_business_krw=200_000,
            prefer_monthly_signal=False,
        )
        self.assertEqual(str(jan.taxable_income_input_source), "income_hybrid_total_income_proxy")
        self.assertEqual(str(feb.taxable_income_input_source), "income_hybrid_total_income_proxy")
        self.assertEqual(int(jan.buffer_target_krw), int(feb.buffer_target_krw))

    def test_prefer_monthly_signal_changes_buffer_when_monthly_transactions_differ(self) -> None:
        jan = self._compute(
            month_key="2026-01",
            income_rows=[(1_000_000, "A", "정산", "income")],
            expense_business_krw=100_000,
            prefer_monthly_signal=True,
        )
        feb = self._compute(
            month_key="2026-02",
            income_rows=[(5_000_000, "B", "정산", "income")],
            expense_business_krw=200_000,
            prefer_monthly_signal=True,
        )
        self.assertEqual(str(jan.taxable_income_input_source), "monthly_profit_annualized_proxy")
        self.assertEqual(str(feb.taxable_income_input_source), "monthly_profit_annualized_proxy")
        self.assertNotEqual(int(jan.buffer_target_krw), int(feb.buffer_target_krw))

    def test_prefer_monthly_signal_allows_same_value_when_monthly_inputs_same(self) -> None:
        jan = self._compute(
            month_key="2026-01",
            income_rows=[(2_000_000, "A", "정산", "income")],
            expense_business_krw=300_000,
            prefer_monthly_signal=True,
        )
        feb = self._compute(
            month_key="2026-02",
            income_rows=[(2_000_000, "A", "정산", "income")],
            expense_business_krw=300_000,
            prefer_monthly_signal=True,
        )
        self.assertEqual(int(jan.buffer_target_krw), int(feb.buffer_target_krw))


if __name__ == "__main__":
    unittest.main()

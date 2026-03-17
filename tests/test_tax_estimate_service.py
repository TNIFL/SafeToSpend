from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.receipt_tax_effects import ReceiptTaxEffectsSummary
from services.onboarding import get_tax_profile, save_tax_profile
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


class TaxEstimateServiceTest(unittest.TestCase):
    def _compute(
        self,
        *,
        profile: dict,
        income_rows: list[tuple[int, str, str, str]],
        expense_business_krw: int,
        buffer_total_krw: int,
        override_pick: dict | None = None,
        override_agg: dict | None = None,
        prefer_monthly_signal: bool = False,
    ):
        pick_payload = override_pick or {
            "applied": False,
            "entry": None,
            "target_year": 2024,
            "used_year": None,
        }
        agg_payload = override_agg or {}
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
            patch("services.risk.pick_income_override_for_month", return_value=pick_payload),
            patch("services.risk.aggregate_income_override", return_value=agg_payload),
            patch("services.risk.compute_receipt_tax_effects_for_month", return_value=zero_effects),
            patch(
                "services.risk.db.session.query",
                side_effect=[
                    _QueryStub(rows=income_rows),
                    _QueryStub(scalar_value=int(expense_business_krw)),
                    _QueryStub(scalar_value=int(buffer_total_krw)),
                ],
            ),
        ):
            return compute_tax_estimate(
                user_pk=1,
                month_key="2026-03",
                prefer_monthly_signal=prefer_monthly_signal,
            )

    def test_official_input_uses_official_core_path(self) -> None:
        est = self._compute(
            profile={
                "official_taxable_income_annual_krw": 20_000_000,
                "withholding_3_3": "no",
                "annual_gross_income_krw": 50_000_000,
                "annual_deductible_expense_krw": 10_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "income_classification": "business",
                "tax_basic_inputs_confirmed": True,
                "tax_advanced_input_confirmed": True,
                "industry_group": "it",
                "prev_income_band": "30m_80m",
                "other_income": "no",
            },
            income_rows=[(2_000_000, "A", "정산", "income")],
            expense_business_krw=500_000,
            buffer_total_krw=10_000,
        )
        self.assertTrue(bool(est.official_calculable))
        self.assertFalse(bool(est.is_limited_estimate))
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.accuracy_level), "exact_ready")
        self.assertEqual(int(est.official_taxable_income_annual_krw), 20_000_000)
        self.assertEqual(int(est.taxable_income_used_annual_krw), 20_000_000)
        self.assertEqual(int(est.tax_due_est_krw), 159_500)

    def test_legacy_taxable_alias_still_enters_official_core_path(self) -> None:
        est = self._compute(
            profile={
                "annual_taxable_income_krw": 13_000_000,
                "withholding_3_3": "no",
                "annual_gross_income_krw": 42_000_000,
                "annual_deductible_expense_krw": 11_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "income_classification": "business",
                "tax_basic_inputs_confirmed": True,
                "tax_advanced_input_confirmed": True,
                "industry_group": "it",
                "prev_income_band": "30m_80m",
                "other_income": "no",
            },
            income_rows=[(1_200_000, "A", "정산", "income")],
            expense_business_krw=100_000,
            buffer_total_krw=0,
        )
        self.assertTrue(bool(est.official_calculable))
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.accuracy_level), "exact_ready")
        self.assertEqual(int(est.official_taxable_income_annual_krw), 13_000_000)
        self.assertEqual(int(est.tax_due_est_krw), 71_500)

    def test_official_input_without_explicit_withholding_is_high_confidence(self) -> None:
        est = self._compute(
            profile={
                "official_taxable_income_annual_krw": 20_000_000,
                "annual_gross_income_krw": 60_000_000,
                "annual_deductible_expense_krw": 18_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "income_classification": "business",
                "tax_basic_inputs_confirmed": True,
                "other_income": "no",
            },
            income_rows=[(2_000_000, "A", "정산", "income")],
            expense_business_krw=500_000,
            buffer_total_krw=0,
        )
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.accuracy_level), "high_confidence")

    def test_official_input_with_required_core_is_high_confidence(self) -> None:
        est = self._compute(
            profile={
                "official_taxable_income_annual_krw": 20_000_000,
                "annual_gross_income_krw": 60_000_000,
                "annual_deductible_expense_krw": 18_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "income_classification": "business",
                "tax_basic_inputs_confirmed": True,
                "other_income": "no",
            },
            income_rows=[(2_000_000, "A", "정산", "income")],
            expense_business_krw=500_000,
            buffer_total_krw=0,
        )
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.accuracy_level), "high_confidence")

    def test_missing_prepaid_input_blocks_high_confidence(self) -> None:
        est = self._compute(
            profile={
                "official_taxable_income_annual_krw": 20_000_000,
                "annual_gross_income_krw": 55_000_000,
                "annual_deductible_expense_krw": 15_000_000,
                "income_classification": "business",
                "withheld_tax_annual_krw": 500_000,
                "tax_basic_inputs_confirmed": True,
            },
            income_rows=[(2_000_000, "A", "원천", "income")],
            expense_business_krw=300_000,
            buffer_total_krw=0,
        )
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.accuracy_level), "limited")

    def test_missing_official_input_uses_income_hybrid_proxy(self) -> None:
        est = self._compute(
            profile={
                "withholding_3_3": "unknown",
                "industry_group": "it",
                "prev_income_band": "30m_80m",
                "other_income": "no",
            },
            income_rows=[(1_500_000, "B", "정산", "income")],
            expense_business_krw=0,
            buffer_total_krw=0,
            override_pick={
                "applied": True,
                "entry": {"scope": "tax", "year": 2024},
                "target_year": 2024,
                "used_year": 2024,
            },
            override_agg={"annual_total_income_krw": 24_000_000},
        )
        self.assertFalse(bool(est.official_calculable))
        self.assertTrue(bool(est.is_limited_estimate))
        self.assertEqual(str(est.tax_calculation_mode), "limited_proxy")
        self.assertEqual(str(est.accuracy_level), "limited")
        self.assertEqual(str(est.taxable_income_input_source), "income_hybrid_total_income_proxy")
        self.assertEqual(int(est.taxable_income_used_annual_krw), 24_000_000)
        self.assertEqual(str(est.official_block_reason), "proxy_from_annual_income")
        self.assertGreater(int(est.tax_due_est_krw), 0)

    def test_prefer_monthly_signal_uses_monthly_profit_proxy_for_calendar(self) -> None:
        est = self._compute(
            profile={
                "withholding_3_3": "unknown",
                "industry_group": "it",
                "prev_income_band": "30m_80m",
                "other_income": "no",
            },
            income_rows=[(3_000_000, "B", "정산", "income")],
            expense_business_krw=1_000_000,
            buffer_total_krw=0,
            override_pick={
                "applied": True,
                "entry": {"scope": "tax", "year": 2024},
                "target_year": 2024,
                "used_year": 2024,
            },
            override_agg={"annual_total_income_krw": 24_000_000},
            prefer_monthly_signal=True,
        )
        self.assertFalse(bool(est.official_calculable))
        self.assertTrue(bool(est.is_limited_estimate))
        self.assertEqual(str(est.tax_calculation_mode), "limited_proxy")
        self.assertEqual(str(est.taxable_income_input_source), "monthly_profit_annualized_proxy")
        self.assertEqual(int(est.taxable_income_used_annual_krw), 24_000_000)

    def test_profile_income_expense_proxy_used_when_official_taxable_missing(self) -> None:
        est = self._compute(
            profile={
                "annual_gross_income_krw": 60_000_000,
                "annual_deductible_expense_krw": 24_000_000,
                "withholding_3_3": "no",
                "industry_group": "it",
                "prev_income_band": "30m_80m",
                "other_income": "no",
            },
            income_rows=[(2_000_000, "C", "정산", "income")],
            expense_business_krw=0,
            buffer_total_krw=0,
        )
        self.assertFalse(bool(est.official_calculable))
        self.assertTrue(bool(est.is_limited_estimate))
        self.assertEqual(str(est.tax_calculation_mode), "limited_proxy")
        self.assertEqual(str(est.accuracy_level), "limited")
        self.assertEqual(str(est.taxable_income_input_source), "profile_income_expense_proxy")
        self.assertEqual(int(est.taxable_income_used_annual_krw), 36_000_000)
        self.assertEqual(int(est.tax_due_est_krw), 379_500)

    def test_basic_mode_user_confirmed_inputs_can_reach_high_confidence_without_official_taxable(self) -> None:
        est = self._compute(
            profile={
                "annual_gross_income_krw": 60_000_000,
                "annual_deductible_expense_krw": 24_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "income_classification": "business",
                "tax_basic_inputs_confirmed": True,
                "withholding_3_3": "no",
            },
            income_rows=[(2_000_000, "C", "정산", "income")],
            expense_business_krw=0,
            buffer_total_krw=0,
        )
        self.assertFalse(bool(est.official_calculable))
        self.assertTrue(bool(est.is_limited_estimate))
        self.assertEqual(str(est.tax_calculation_mode), "limited_proxy")
        self.assertEqual(str(est.accuracy_level), "high_confidence")

    def test_profile_annual_tax_credit_is_applied_before_heuristic(self) -> None:
        est = self._compute(
            profile={
                "official_taxable_income_annual_krw": 20_000_000,
                "withholding_3_3": "yes",
                "annual_gross_income_krw": 50_000_000,
                "annual_deductible_expense_krw": 10_000_000,
                "withheld_tax_annual_krw": 600_000,
                "prepaid_tax_annual_krw": 120_000,
                "income_classification": "business",
                "tax_basic_inputs_confirmed": True,
                "tax_advanced_input_confirmed": True,
                "industry_group": "it",
                "prev_income_band": "30m_80m",
                "other_income": "no",
            },
            income_rows=[(2_000_000, "A", "3.3 정산", "income")],
            expense_business_krw=300_000,
            buffer_total_krw=0,
        )
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.accuracy_level), "exact_ready")
        self.assertEqual(str(est.withholding_mode), "profile_annual_credit")
        self.assertEqual(int(est.annual_tax_credit_input_krw), 720_000)
        self.assertEqual(int(est.withheld_est_krw), 60_000)
        self.assertEqual(int(est.tax_due_est_krw), 99_500)

    def test_missing_inputs_stays_blocked_with_reason(self) -> None:
        est = self._compute(
            profile={"withholding_3_3": "unknown"},
            income_rows=[],
            expense_business_krw=0,
            buffer_total_krw=0,
        )
        self.assertFalse(bool(est.official_calculable))
        self.assertFalse(bool(est.is_limited_estimate))
        self.assertEqual(str(est.tax_calculation_mode), "blocked")
        self.assertEqual(str(est.accuracy_level), "blocked")
        self.assertEqual(str(est.official_block_reason), "missing_taxable_income")
        self.assertEqual(str(est.taxable_income_input_source), "missing")
        self.assertEqual(int(est.tax_due_est_krw), 0)

    def test_saved_profile_roundtrip_feeds_official_path(self) -> None:
        row = SimpleNamespace(profile_json={"industry_group": "it"})

        query_mock = MagicMock()
        query_mock.filter_by.return_value.first.return_value = row

        class _TaxProfileStub:
            query = query_mock

            def __init__(self, user_pk: int, profile_json: dict):
                self.user_pk = int(user_pk)
                self.profile_json = dict(profile_json or {})

        with (
            patch("services.onboarding.TaxProfile", _TaxProfileStub),
            patch("services.onboarding.db.session.add"),
            patch("services.onboarding.db.session.commit"),
        ):
            ok, _msg = save_tax_profile(
                user_pk=31,
                payload={
                    "official_taxable_income_annual_krw": "20,000,000",
                    "annual_gross_income_krw": "60,000,000",
                    "annual_deductible_expense_krw": "20,000,000",
                    "income_classification": "business",
                    "withheld_tax_annual_krw": "600,000",
                    "prepaid_tax_annual_krw": "120,000",
                    "tax_basic_inputs_confirmed": True,
                    "tax_advanced_input_confirmed": True,
                },
            )
            self.assertTrue(ok)
            profile = get_tax_profile(31)

        est = self._compute(
            profile=profile,
            income_rows=[(2_000_000, "A", "정산", "income")],
            expense_business_krw=0,
            buffer_total_krw=0,
        )
        self.assertTrue(bool(est.official_calculable))
        self.assertEqual(str(est.tax_calculation_mode), "official_exact")
        self.assertEqual(str(est.withholding_mode), "profile_annual_credit")
        self.assertEqual(int(est.tax_due_est_krw), 99_500)


class TaxProfileTaxableFieldNormalizationTest(unittest.TestCase):
    @staticmethod
    def _make_tax_profile_stub(*, row: SimpleNamespace | None):
        query_mock = MagicMock()
        query_mock.filter_by.return_value.first.return_value = row

        class _TaxProfileStub:
            query = query_mock

            def __init__(self, user_pk: int, profile_json: dict):
                self.user_pk = int(user_pk)
                self.profile_json = dict(profile_json or {})

        return _TaxProfileStub

    def test_save_tax_profile_normalizes_taxable_income_alias(self) -> None:
        row = SimpleNamespace(profile_json={"industry_group": "it"})
        tax_profile_stub = self._make_tax_profile_stub(row=row)
        with (
            patch("services.onboarding.TaxProfile", tax_profile_stub),
            patch("services.onboarding.db.session.add"),
            patch("services.onboarding.db.session.commit"),
        ):
            ok, msg = save_tax_profile(
                user_pk=7,
                payload={"taxable_income_annual_krw": "20,000,000"},
            )
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
        self.assertEqual(int(row.profile_json.get("official_taxable_income_annual_krw") or 0), 20_000_000)
        self.assertEqual(int(row.profile_json.get("taxable_income_annual_krw") or 0), 20_000_000)

    def test_save_tax_profile_normalizes_credit_and_income_alias(self) -> None:
        row = SimpleNamespace(profile_json={"industry_group": "it"})
        tax_profile_stub = self._make_tax_profile_stub(row=row)
        with (
            patch("services.onboarding.TaxProfile", tax_profile_stub),
            patch("services.onboarding.db.session.add"),
            patch("services.onboarding.db.session.commit"),
        ):
            ok, msg = save_tax_profile(
                user_pk=9,
                payload={
                    "annual_total_income_krw": "70,000,000",
                    "annual_expense_krw": "25,000,000",
                    "withholding_tax_annual_krw": "1,100,000",
                    "interim_prepaid_tax_annual_krw": "200,000",
                    "income_classification": "mixed",
                },
            )
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
        self.assertEqual(int(row.profile_json.get("annual_gross_income_krw") or 0), 70_000_000)
        self.assertEqual(int(row.profile_json.get("annual_deductible_expense_krw") or 0), 25_000_000)
        self.assertEqual(int(row.profile_json.get("withheld_tax_annual_krw") or 0), 1_100_000)
        self.assertEqual(int(row.profile_json.get("prepaid_tax_annual_krw") or 0), 200_000)
        self.assertEqual(str(row.profile_json.get("income_classification") or ""), "mixed")

    def test_get_tax_profile_reads_legacy_taxable_key(self) -> None:
        row = SimpleNamespace(profile_json={"annual_taxable_income_krw": "13,000,000"})
        tax_profile_stub = self._make_tax_profile_stub(row=row)
        with patch("services.onboarding.TaxProfile", tax_profile_stub):
            profile = get_tax_profile(5)
        self.assertEqual(int(profile.get("official_taxable_income_annual_krw") or 0), 13_000_000)
        self.assertEqual(int(profile.get("taxable_income_annual_krw") or 0), 13_000_000)

    def test_get_tax_profile_reads_legacy_credit_alias_keys(self) -> None:
        row = SimpleNamespace(
            profile_json={
                "gross_income_annual_krw": "55,000,000",
                "deductible_expense_annual_krw": "15,000,000",
                "withheld_tax_paid_annual_krw": "900,000",
                "paid_tax_annual_krw": "120,000",
                "income_classification": "business",
            }
        )
        tax_profile_stub = self._make_tax_profile_stub(row=row)
        with patch("services.onboarding.TaxProfile", tax_profile_stub):
            profile = get_tax_profile(11)
        self.assertEqual(int(profile.get("annual_gross_income_krw") or 0), 55_000_000)
        self.assertEqual(int(profile.get("annual_deductible_expense_krw") or 0), 15_000_000)
        self.assertEqual(int(profile.get("withheld_tax_annual_krw") or 0), 900_000)
        self.assertEqual(int(profile.get("prepaid_tax_annual_krw") or 0), 120_000)
        self.assertEqual(str(profile.get("income_classification") or ""), "business")


if __name__ == "__main__":
    unittest.main()

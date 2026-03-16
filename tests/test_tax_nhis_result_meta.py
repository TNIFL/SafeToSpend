from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.accuracy_reason_codes import (
    NHIS_REASON_CODES,
    TAX_REASON_CODES,
)
from services.nhis_runtime import build_nhis_result_meta
from services.risk import build_tax_result_meta


class TaxResultMetaTest(unittest.TestCase):
    def test_official_exact_maps_to_normal(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="official_exact",
            official_calculable=True,
            is_limited_estimate=False,
            official_block_reason="",
            official_taxable_income_annual_krw=20_000_000,
            annual_tax_credit_input_krw=0,
            withholding_mode="heuristic",
            applied_flags=(),
        )
        meta = build_tax_result_meta(est)
        self.assertEqual(str(meta.get("level") or ""), "normal")
        self.assertFalse(bool(meta.get("is_limited")))
        self.assertEqual(str(meta.get("accuracy_level") or ""), "high_confidence")
        self.assertIn("공식 기준", str(meta.get("message") or ""))

    def test_official_exact_with_complete_inputs_maps_to_exact_ready(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="official_exact",
            official_calculable=True,
            is_limited_estimate=False,
            official_block_reason="",
            official_taxable_income_annual_krw=20_000_000,
            annual_tax_credit_input_krw=720_000,
            withholding_mode="profile_annual_credit",
            applied_flags=("profile_complete",),
            accuracy_level="exact_ready",
        )
        meta = build_tax_result_meta(est)
        self.assertEqual(str(meta.get("level") or ""), "normal")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "exact_ready")

    def test_limited_proxy_maps_to_limited(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="limited_proxy",
            official_calculable=False,
            is_limited_estimate=True,
            official_block_reason="missing_official_taxable_income",
            taxable_income_input_source="profile_income_expense_proxy",
        )
        meta = build_tax_result_meta(est)
        self.assertEqual(str(meta.get("level") or ""), "limited")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "limited")
        self.assertTrue(bool(meta.get("is_limited")))
        self.assertEqual(str(meta.get("reason") or ""), "proxy_from_annual_income")
        self.assertIn("annual_gross_income_krw", list(meta.get("auto_fillable_fields") or []))
        self.assertIn("official_taxable_income_annual_krw", list(meta.get("low_confidence_inferable_fields") or []))
        self.assertIn("보수적으로", str(meta.get("message") or ""))

    def test_limited_proxy_with_high_confidence_level_maps_to_normal(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="limited_proxy",
            official_calculable=False,
            is_limited_estimate=True,
            official_block_reason="proxy_from_annual_income",
            taxable_income_input_source="profile_income_expense_proxy",
            accuracy_level="high_confidence",
        )
        meta = build_tax_result_meta(est)
        self.assertEqual(str(meta.get("level") or ""), "normal")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "high_confidence")
        self.assertIn("확인 기반 추정", str(meta.get("label") or ""))

    def test_blocked_maps_to_blocked_and_zero_risk_copy(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="blocked",
            official_calculable=False,
            is_limited_estimate=False,
            official_block_reason="missing_taxable_income",
        )
        meta = build_tax_result_meta(est)
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "blocked")
        self.assertIn("0원", str(meta.get("message") or ""))

    def test_high_confidence_uses_missing_withheld_tax_reason(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="official_exact",
            official_calculable=True,
            is_limited_estimate=False,
            official_block_reason="",
            official_taxable_income_annual_krw=20_000_000,
            annual_tax_credit_input_krw=0,
            withheld_tax_input_annual_krw=0,
            prepaid_tax_input_annual_krw=0,
            withholding_mode="heuristic",
            applied_flags=("profile_complete",),
            accuracy_level="high_confidence",
        )
        meta = build_tax_result_meta(est)
        self.assertEqual(str(meta.get("reason") or ""), "missing_withheld_tax")
        self.assertIn("withheld_tax_annual_krw", list(meta.get("needs_user_input_fields") or []))

    def test_tax_reason_codes_are_canonical(self) -> None:
        est = SimpleNamespace(
            tax_calculation_mode="limited_proxy",
            official_calculable=False,
            is_limited_estimate=True,
            official_block_reason="missing_official_taxable_income",
            taxable_income_input_source="income_hybrid_total_income_proxy",
        )
        meta = build_tax_result_meta(est)
        reason = str(meta.get("reason") or "")
        self.assertIn(reason, TAX_REASON_CODES)
        self.assertNotIn(reason, {"missing_official_taxable_income", "limited_proxy", "unknown"})


class NhisResultMetaTest(unittest.TestCase):
    def test_normal_when_official_ready_and_confident(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"mode": "bill_score", "confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={
                "member_type": "regional",
                "annual_income_krw": 48_000_000,
                "non_salary_annual_income_krw": 0,
                "property_tax_base_total_krw": 120_000_000,
            },
        )
        self.assertEqual(str(meta.get("level") or ""), "normal")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "exact_ready")
        self.assertIn("공식 기준", str(meta.get("message") or ""))

    def test_normal_medium_confidence_maps_to_high_confidence(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"mode": "rules_regional", "confidence_level": "medium", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={
                "member_type": "regional",
                "annual_income_krw": 36_000_000,
                "non_salary_annual_income_krw": 0,
                "property_tax_base_total_krw": 90_000_000,
            },
        )
        self.assertEqual(str(meta.get("level") or ""), "normal")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "high_confidence")

    def test_limited_when_input_is_insufficient(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"confidence_level": "low", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "unknown"},
        )
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "blocked")
        self.assertEqual(str(meta.get("reason") or ""), "missing_membership_type")

    def test_blocked_when_official_not_ready(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=False,
        )
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("reason") or ""), "missing_snapshot")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "blocked")

    def test_limited_when_dataset_update_error_exists(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "fetch_failed", "is_fallback_default": False},
            official_ready=True,
            profile={
                "member_type": "regional",
                "annual_income_krw": 48_000_000,
                "non_salary_annual_income_krw": 0,
                "property_tax_base_total_krw": 100_000_000,
            },
        )
        self.assertEqual(str(meta.get("level") or ""), "limited")
        self.assertEqual(str(meta.get("reason") or ""), "dataset_fallback")

    def test_limited_reason_missing_membership_type(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "unknown", "confidence_level": "low", "can_estimate": False},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": ""},
        )
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("reason") or ""), "missing_membership_type")

    def test_limited_reason_unknown_membership_type(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "unknown", "confidence_level": "low", "can_estimate": False},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "freelancer"},
        )
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("reason") or ""), "unknown_membership_type")

    def test_limited_reason_missing_salary_monthly(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "insufficient", "confidence_level": "low", "can_estimate": False},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "employee", "salary_monthly_krw": None},
        )
        self.assertEqual(str(meta.get("reason") or ""), "missing_salary_monthly")
        self.assertIn("salary_monthly_krw", list(meta.get("needs_user_input_fields") or []))

    def test_nhis_reason_codes_are_canonical(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "insufficient", "confidence_level": "low", "can_estimate": False},
            status={"is_stale": False, "update_error": "timeout", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "employee"},
        )
        reason = str(meta.get("reason") or "")
        self.assertIn(reason, NHIS_REASON_CODES)
        self.assertNotIn(
            reason,
            {"official_not_ready", "input_insufficient", "dataset_update_error", "dataset_fallback_default", "dataset_stale"},
        )


if __name__ == "__main__":
    unittest.main()

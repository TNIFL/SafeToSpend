from __future__ import annotations

import unittest

from services.nhis_runtime import build_nhis_result_meta, evaluate_nhis_required_inputs


class NhisRequiredInputsTest(unittest.TestCase):
    def test_employee_required_inputs_ready(self) -> None:
        status = evaluate_nhis_required_inputs(
            estimate={"member_type": "employee", "mode": "employee", "confidence_level": "medium", "can_estimate": True},
            profile={"member_type": "employee", "salary_monthly_krw": 3_200_000, "non_salary_annual_income_krw": 0},
            official_ready=True,
        )
        self.assertTrue(bool(status.get("high_confidence_inputs_ready")))
        self.assertEqual(str(status.get("blocked_reason") or ""), "")

    def test_employee_missing_salary_is_not_ready(self) -> None:
        status = evaluate_nhis_required_inputs(
            estimate={"member_type": "employee", "mode": "insufficient", "confidence_level": "low", "can_estimate": False},
            profile={"member_type": "employee", "non_salary_annual_income_krw": 0},
            official_ready=True,
        )
        self.assertFalse(bool(status.get("high_confidence_inputs_ready")))
        self.assertEqual(str(status.get("limited_reason") or ""), "missing_salary_monthly")

    def test_regional_missing_property_is_not_ready(self) -> None:
        status = evaluate_nhis_required_inputs(
            estimate={"member_type": "regional", "mode": "rules_regional", "confidence_level": "medium", "can_estimate": True},
            profile={"member_type": "regional", "annual_income_krw": 36_000_000, "non_salary_annual_income_krw": 0},
            official_ready=True,
        )
        self.assertFalse(bool(status.get("high_confidence_inputs_ready")))
        self.assertEqual(str(status.get("limited_reason") or ""), "missing_property_tax_base")

    def test_regional_meta_marks_property_as_user_input_required(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "regional", "mode": "rules_regional", "confidence_level": "medium", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "regional", "annual_income_krw": 24_000_000, "non_salary_annual_income_krw": 0},
        )
        self.assertEqual(str(meta.get("reason") or ""), "missing_property_tax_base")
        self.assertIn("property_tax_base_total_krw", list(meta.get("needs_user_input_fields") or []))

    def test_unknown_membership_is_blocked(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "unknown", "mode": "insufficient", "confidence_level": "low", "can_estimate": False},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "unknown"},
        )
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("reason") or ""), "missing_membership_type")

    def test_missing_snapshot_blocks_even_with_inputs(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "employee", "confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=False,
            profile={"member_type": "employee", "salary_monthly_krw": 3_500_000, "non_salary_annual_income_krw": 0},
        )
        self.assertEqual(str(meta.get("level") or ""), "blocked")
        self.assertEqual(str(meta.get("reason") or ""), "missing_snapshot")

    def test_exact_ready_requires_bill_mode(self) -> None:
        normal = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "employee", "confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "employee", "salary_monthly_krw": 3_500_000, "non_salary_annual_income_krw": 0},
        )
        bill = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "bill_score", "confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "employee", "salary_monthly_krw": 3_500_000, "non_salary_annual_income_krw": 0},
        )
        self.assertEqual(str(normal.get("accuracy_level") or ""), "high_confidence")
        self.assertEqual(str(bill.get("accuracy_level") or ""), "exact_ready")


if __name__ == "__main__":
    unittest.main()

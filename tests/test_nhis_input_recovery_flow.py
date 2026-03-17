from __future__ import annotations

import unittest
from pathlib import Path

from services.nhis_runtime import build_nhis_recovery_cta, build_nhis_result_meta


ROOT = Path(__file__).resolve().parents[1]


class NhisInputRecoveryFlowTest(unittest.TestCase):
    def test_blocked_missing_membership_type_requires_recovery(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"mode": "insufficient", "confidence_level": "low", "can_estimate": False},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": ""},
        )
        cta = build_nhis_recovery_cta(meta, recovery_url="/dashboard/nhis#asset-diagnosis")
        self.assertEqual(str(meta.get("accuracy_level") or ""), "blocked")
        self.assertTrue(bool(cta.get("show")))
        self.assertIn("가입유형", list(cta.get("missing_labels") or []))

    def test_employee_missing_salary_stays_limited(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "rules_employee", "confidence_level": "medium", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={"member_type": "employee", "non_salary_annual_income_krw": 0},
        )
        self.assertEqual(str(meta.get("accuracy_level") or ""), "limited")
        self.assertIn("salary_monthly_krw", list(meta.get("needs_user_input_fields") or []))

    def test_employee_complete_inputs_can_be_exact_ready(self) -> None:
        meta = build_nhis_result_meta(
            estimate={"member_type": "employee", "mode": "bill_employee", "confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={
                "member_type": "employee",
                "salary_monthly_krw": 3_500_000,
                "non_salary_annual_income_krw": 0,
            },
        )
        self.assertEqual(str(meta.get("accuracy_level") or ""), "exact_ready")

    def test_nhis_template_shows_recovery_cta_context(self) -> None:
        body = (ROOT / "templates/nhis.html").read_text(encoding="utf-8")
        self.assertIn("nhis_recovery_cta_ctx", body)
        self.assertIn("nhis_recovery_cta_ctx.action_label", body)
        self.assertIn("asset-diagnosis", body)


if __name__ == "__main__":
    unittest.main()

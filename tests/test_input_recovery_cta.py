from __future__ import annotations

import unittest

from services.nhis_runtime import build_nhis_recovery_cta
from services.risk import build_tax_recovery_cta


class InputRecoveryCtaTest(unittest.TestCase):
    def test_tax_blocked_cta_is_recovery_first(self) -> None:
        cta = build_tax_recovery_cta(
            {
                "accuracy_level": "blocked",
                "needs_user_input_fields": ["official_taxable_income_annual_krw", "income_classification"],
                "required_inputs": {
                    "exact_ready_missing_fields": ["official_taxable_income_annual_krw", "income_classification"],
                    "high_confidence_missing_fields": ["official_taxable_income_annual_krw"],
                },
            },
            recovery_url="/dashboard/profile?step=2",
        )
        self.assertTrue(bool(cta.get("show")))
        self.assertTrue(bool(cta.get("blocked")))
        self.assertIn("연 과세표준", list(cta.get("missing_labels") or []))

    def test_tax_exact_ready_hides_recovery_cta(self) -> None:
        cta = build_tax_recovery_cta(
            {
                "accuracy_level": "exact_ready",
                "needs_user_input_fields": [],
                "required_inputs": {
                    "exact_ready_missing_fields": [],
                    "high_confidence_missing_fields": [],
                },
            },
            recovery_url="/dashboard/profile?step=2",
        )
        self.assertFalse(bool(cta.get("show")))

    def test_nhis_limited_shows_accuracy_improve_cta(self) -> None:
        cta = build_nhis_recovery_cta(
            {
                "accuracy_level": "limited",
                "needs_user_input_fields": ["salary_monthly_krw"],
                "required_inputs": {
                    "exact_ready_missing_fields": ["salary_monthly_krw"],
                    "high_confidence_missing_fields": ["salary_monthly_krw"],
                },
            },
            recovery_url="/dashboard/nhis#asset-diagnosis",
        )
        self.assertTrue(bool(cta.get("show")))
        self.assertTrue(bool(cta.get("limited")))
        self.assertIn("정확도", str(cta.get("title") or ""))
        self.assertIn("직장 월 보수", list(cta.get("missing_labels") or []))

    def test_nhis_high_confidence_hides_recovery_cta(self) -> None:
        cta = build_nhis_recovery_cta(
            {
                "accuracy_level": "high_confidence",
                "needs_user_input_fields": [],
                "required_inputs": {
                    "exact_ready_missing_fields": [],
                    "high_confidence_missing_fields": [],
                },
            },
            recovery_url="/dashboard/nhis#asset-diagnosis",
        )
        self.assertFalse(bool(cta.get("show")))


if __name__ == "__main__":
    unittest.main()

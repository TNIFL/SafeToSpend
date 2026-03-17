from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.nhis_profile import save_nhis_profile_from_form
from services.nhis_runtime import evaluate_nhis_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class NhisSingleStepFlowTest(unittest.TestCase):
    def _row(self) -> SimpleNamespace:
        return SimpleNamespace(
            member_type="unknown",
            target_month="2026-03",
            household_has_others=None,
            annual_income_krw=None,
            salary_monthly_krw=None,
            non_salary_annual_income_krw=None,
            property_tax_base_total_krw=None,
            rent_deposit_krw=None,
            rent_monthly_krw=None,
            has_reduction_or_relief=None,
            has_housing_loan_deduction=None,
            last_bill_total_krw=None,
            last_bill_health_only_krw=None,
            last_bill_score_points=None,
            updated_at=None,
        )

    def test_membership_only_save_path_is_supported(self) -> None:
        row = self._row()
        with (
            patch("services.nhis_profile.get_or_create_nhis_profile", return_value=row),
            patch("services.nhis_profile.db.session.add"),
            patch("services.nhis_profile.db.session.commit"),
        ):
            ok, msg = save_nhis_profile_from_form(
                user_pk=1,
                form_data={"member_type": "employee", "target_month": "2026-03"},
                allow_membership_only=True,
            )
        self.assertTrue(ok)
        self.assertIn("가입유형", msg)
        self.assertEqual(row.member_type, "employee")

    def test_membership_saved_moves_from_blocked_reason_to_next_required_reason(self) -> None:
        blocked = evaluate_nhis_required_inputs(
            estimate={"member_type": "unknown"},
            profile={"member_type": "unknown"},
            official_ready=True,
        )
        self.assertEqual(str(blocked.get("blocked_reason") or ""), "missing_membership_type")

        after = evaluate_nhis_required_inputs(
            estimate={"member_type": "employee"},
            profile={"member_type": "employee"},
            official_ready=True,
        )
        self.assertEqual(str(after.get("blocked_reason") or ""), "")
        self.assertIn("salary_monthly_krw", list(after.get("high_confidence_missing_fields") or []))

    def test_ui_contains_membership_first_single_step_copy(self) -> None:
        overview = (ROOT / "templates/overview.html").read_text(encoding="utf-8")
        nhis = (ROOT / "templates/nhis.html").read_text(encoding="utf-8")
        tax_buffer = (ROOT / "templates/calendar/tax_buffer.html").read_text(encoding="utf-8")
        profile_route = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")

        self.assertIn("nhis_membership_type_quick_save", profile_route)
        self.assertIn("save_membership_only", profile_route)
        self.assertIn("가입유형 먼저 저장", overview)
        self.assertIn("가입유형 1문항", overview)
        self.assertIn("가입유형 먼저 저장", nhis)
        self.assertIn("가입유형 먼저 저장", tax_buffer)


if __name__ == "__main__":
    unittest.main()

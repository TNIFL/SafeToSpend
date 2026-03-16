from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.nhis_profile import save_nhis_profile_from_form


class NhisRequiredInputFlowTest(unittest.TestCase):
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

    def test_unknown_membership_is_rejected(self) -> None:
        with patch("services.nhis_profile.get_or_create_nhis_profile", return_value=self._row()):
            ok, msg = save_nhis_profile_from_form(user_pk=1, form_data={"member_type": "unknown"})
        self.assertFalse(ok)
        self.assertIn("가입유형", msg)

    def test_employee_missing_required_fields_is_rejected(self) -> None:
        with patch("services.nhis_profile.get_or_create_nhis_profile", return_value=self._row()):
            ok, msg = save_nhis_profile_from_form(
                user_pk=1,
                form_data={
                    "member_type": "employee",
                    "salary_monthly_krw": "",
                    "non_salary_annual_income_krw": "",
                },
            )
        self.assertFalse(ok)
        self.assertIn("직장가입자", msg)
        self.assertIn("월 보수", msg)

    def test_regional_missing_property_base_is_rejected(self) -> None:
        with patch("services.nhis_profile.get_or_create_nhis_profile", return_value=self._row()):
            ok, msg = save_nhis_profile_from_form(
                user_pk=1,
                form_data={
                    "member_type": "regional",
                    "annual_income_krw": "24000000",
                    "non_salary_annual_income_krw": "0",
                    "property_tax_base_total_krw": "",
                },
            )
        self.assertFalse(ok)
        self.assertIn("지역가입자", msg)
        self.assertIn("재산세 과세표준", msg)

    def test_employee_required_fields_allows_save(self) -> None:
        row = self._row()
        with (
            patch("services.nhis_profile.get_or_create_nhis_profile", return_value=row),
            patch("services.nhis_profile.db.session.add"),
            patch("services.nhis_profile.db.session.commit"),
        ):
            ok, msg = save_nhis_profile_from_form(
                user_pk=1,
                form_data={
                    "member_type": "employee",
                    "target_month": "2026-03",
                    "salary_monthly_krw": "3200000",
                    "non_salary_annual_income_krw": "0",
                    "annual_income_krw": "38400000",
                },
            )
        self.assertTrue(ok)
        self.assertEqual(msg, "저장됐어요.")
        self.assertEqual(row.member_type, "employee")
        self.assertEqual(int(row.salary_monthly_krw or 0), 3_200_000)


if __name__ == "__main__":
    unittest.main()

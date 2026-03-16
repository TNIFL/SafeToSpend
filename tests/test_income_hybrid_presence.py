from __future__ import annotations

import unittest
from unittest.mock import patch

from services.assets_profile import _save_income_hybrid_from_form


class IncomeHybridPresenceTests(unittest.TestCase):
    @patch("services.assets_profile.save_income_hybrid_entry")
    @patch("services.assets_profile.set_income_hybrid_enabled")
    def test_presence_no_forces_zero_and_enabled(self, set_enabled_mock, save_entry_mock) -> None:
        ok, msg = _save_income_hybrid_from_form(
            user_pk=1,
            month_key="2026-03",
            form={
                "income_hybrid_present": "1",
                "income_hybrid_enabled": "0",
                "income_hybrid_year": "2025",
                "income_hybrid_scope": "both",
                "income_hybrid_input_basis": "income_amount_pre_tax",
                "income_hybrid_is_pre_tax": "1",
                "fin_income_presence": "no",
            },
        )
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        set_enabled_mock.assert_not_called()
        save_entry_mock.assert_called_once()
        kwargs = save_entry_mock.call_args.kwargs
        self.assertTrue(kwargs["enabled"])
        self.assertEqual(kwargs["fields"]["fin_income_amount_krw"], 0)

    @patch("services.assets_profile.save_income_hybrid_entry")
    @patch("services.assets_profile.set_income_hybrid_enabled")
    def test_presence_yes_requires_positive_amount(self, set_enabled_mock, save_entry_mock) -> None:
        ok, msg = _save_income_hybrid_from_form(
            user_pk=1,
            month_key="2026-03",
            form={
                "income_hybrid_present": "1",
                "income_hybrid_enabled": "1",
                "income_hybrid_year": "2025",
                "income_hybrid_scope": "both",
                "income_hybrid_input_basis": "income_amount_pre_tax",
                "income_hybrid_is_pre_tax": "1",
                "fin_income_presence": "yes",
                "fin_income_amount_krw": "",
            },
        )
        self.assertFalse(ok)
        self.assertIn("1원 이상", msg)
        set_enabled_mock.assert_not_called()
        save_entry_mock.assert_not_called()

    @patch("services.assets_profile.save_income_hybrid_entry")
    @patch("services.assets_profile.set_income_hybrid_enabled")
    def test_presence_unknown_without_values_disables_override(self, set_enabled_mock, save_entry_mock) -> None:
        ok, msg = _save_income_hybrid_from_form(
            user_pk=1,
            month_key="2026-03",
            form={
                "income_hybrid_present": "1",
                "income_hybrid_enabled": "0",
                "fin_income_presence": "unknown",
            },
        )
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        set_enabled_mock.assert_called_once()
        save_entry_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

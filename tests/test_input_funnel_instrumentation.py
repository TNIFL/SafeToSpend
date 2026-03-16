from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.analytics_events import INPUT_FUNNEL_EVENT_NAMES, record_input_funnel_event


ROOT = Path(__file__).resolve().parents[1]


class InputFunnelInstrumentationTest(unittest.TestCase):
    def test_required_funnel_event_names_are_registered(self) -> None:
        required = {
            "tax_recovery_cta_shown",
            "tax_recovery_cta_clicked",
            "tax_inline_income_classification_shown",
            "tax_inline_income_classification_saved",
            "tax_basic_next_step_viewed",
            "tax_basic_next_step_saved",
            "tax_basic_step_viewed",
            "tax_basic_step_saved",
            "tax_advanced_step_viewed",
            "tax_advanced_step_saved",
            "tax_recovery_completed",
            "nhis_recovery_cta_shown",
            "nhis_recovery_cta_clicked",
            "nhis_inline_membership_type_shown",
            "nhis_inline_membership_type_saved",
            "nhis_detail_next_step_viewed",
            "nhis_detail_next_step_saved",
            "nhis_membership_step_viewed",
            "nhis_membership_step_saved",
            "nhis_detail_step_viewed",
            "nhis_detail_step_saved",
            "nhis_recovery_completed",
        }
        self.assertTrue(required.issubset(INPUT_FUNNEL_EVENT_NAMES))

    def test_record_input_funnel_event_writes_action_log_payload(self) -> None:
        added_rows: list[object] = []

        class _DummyActionLog:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        dummy_session = SimpleNamespace(
            add=lambda row: added_rows.append(row),
            commit=lambda: None,
            rollback=lambda: None,
        )
        dummy_db = SimpleNamespace(session=dummy_session)

        with (
            patch("services.analytics_events.ActionLog", _DummyActionLog),
            patch("services.analytics_events.db", dummy_db),
        ):
            record_input_funnel_event(
                user_pk=7,
                event="tax_basic_step_saved",
                route="web_profile.tax_profile",
                screen="tax_profile_step2",
                accuracy_level_before="blocked",
                accuracy_level_after="limited",
                reason_code_before="missing_income_classification",
                reason_code_after="proxy_from_annual_income",
                extra={"step": 2},
            )

        self.assertEqual(len(added_rows), 1)
        payload = added_rows[0].kwargs
        self.assertEqual(payload["action_type"], "label_update")
        self.assertEqual(payload["user_pk"], 7)
        before_state = payload["before_state"]
        self.assertEqual(before_state["metric_type"], "input_funnel")
        self.assertEqual(before_state["metric_event"], "tax_basic_step_saved")
        self.assertEqual(before_state["accuracy_level_before"], "blocked")
        self.assertEqual(before_state["accuracy_level_after"], "limited")
        self.assertEqual(before_state["reason_code_before"], "missing_income_classification")
        self.assertEqual(before_state["reason_code_after"], "proxy_from_annual_income")
        self.assertEqual(before_state["reason_code"], "proxy_from_annual_income")

    def test_routes_include_funnel_recording_calls(self) -> None:
        profile_route = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        tax_route = (ROOT / "routes/web/calendar/tax.py").read_text(encoding="utf-8")
        overview_route = (ROOT / "routes/web/overview.py").read_text(encoding="utf-8")

        self.assertIn("tax_recovery_cta_clicked", profile_route)
        self.assertIn("tax_inline_income_classification_shown", profile_route)
        self.assertIn("tax_inline_income_classification_saved", profile_route)
        self.assertIn("tax_basic_next_step_viewed", profile_route)
        self.assertIn("tax_basic_next_step_saved", profile_route)
        self.assertIn("tax_basic_step_viewed", profile_route)
        self.assertIn("tax_basic_step_saved", profile_route)
        self.assertIn("nhis_recovery_cta_clicked", profile_route)
        self.assertIn("nhis_inline_membership_type_shown", profile_route)
        self.assertIn("nhis_inline_membership_type_saved", profile_route)
        self.assertIn("nhis_detail_next_step_viewed", profile_route)
        self.assertIn("nhis_detail_next_step_saved", profile_route)
        self.assertIn("nhis_detail_step_viewed", profile_route)
        self.assertIn("nhis_detail_step_saved", profile_route)
        self.assertIn("tax_recovery_cta_shown", tax_route)
        self.assertIn("tax_inline_income_classification_shown", tax_route)
        self.assertIn("nhis_inline_membership_type_shown", tax_route)
        self.assertIn("nhis_recovery_cta_shown", tax_route)
        self.assertIn("tax_recovery_cta_shown", overview_route)
        self.assertIn("tax_inline_income_classification_shown", overview_route)
        self.assertIn("nhis_inline_membership_type_shown", overview_route)
        self.assertIn("nhis_recovery_cta_shown", overview_route)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.analytics_events import SEASONAL_CARD_EVENT_NAMES, record_seasonal_card_event
from services.seasonal_ux import (
    MAY_FILING_FOCUS,
    build_seasonal_cards,
    build_seasonal_screen_context,
    decorate_seasonal_cards_for_tracking,
    decorate_seasonal_context_for_tracking,
)


ROOT = Path(__file__).resolve().parents[1]


class SeasonalUxMetricsEventsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_required_seasonal_event_names_are_registered(self) -> None:
        required = {
            "seasonal_card_shown",
            "seasonal_card_clicked",
            "seasonal_card_landed",
            "seasonal_card_completed",
        }
        self.assertTrue(required.issubset(SEASONAL_CARD_EVENT_NAMES))

    def test_record_seasonal_card_event_writes_action_log_payload(self) -> None:
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
            record_seasonal_card_event(
                user_pk=11,
                event="seasonal_card_clicked",
                route="web_overview.seasonal_card_click",
                season_focus="may_filing_focus",
                card_type="may_accuracy",
                cta_target="profile",
                source_screen="overview",
                priority=1,
                completion_state_before="todo",
                completion_state_after="in_progress",
                month_key="2026-05",
                extra={"cta_label": "기본 정보 보완하기"},
            )

        self.assertEqual(len(added_rows), 1)
        payload = added_rows[0].kwargs
        self.assertEqual(payload["action_type"], "label_update")
        self.assertEqual(payload["user_pk"], 11)
        before_state = payload["before_state"]
        self.assertEqual(before_state["metric_type"], "seasonal_ux")
        self.assertEqual(before_state["metric_event"], "seasonal_card_clicked")
        self.assertEqual(before_state["season_focus"], "may_filing_focus")
        self.assertEqual(before_state["card_type"], "may_accuracy")
        self.assertEqual(before_state["cta_target"], "profile")
        self.assertEqual(before_state["source_screen"], "overview")
        self.assertEqual(before_state["completion_state_before"], "todo")
        self.assertEqual(before_state["completion_state_after"], "in_progress")
        self.assertEqual(before_state["month_key"], "2026-05")

    def test_tracking_urls_are_attached_to_cards_and_context(self) -> None:
        cards = build_seasonal_cards(
            MAY_FILING_FOCUS,
            {
                "has_transactions": True,
                "tax_accuracy_gap": True,
                "profile_completion_percent": 25,
                "receipt_pending_count": 3,
                "reinforcement_pending_count": 1,
                "package_ready": False,
                "package_status": "warn",
                "can_download_package": True,
                "buffer_shortage_krw": 150000,
                "receipt_pending_expense_krw": 55000,
            },
            {
                "review": "/dashboard/review?month=2026-05",
                "tax_buffer": "/dashboard/tax-buffer?month=2026-05",
                "package": "/dashboard/package?month=2026-05",
                "profile": "/dashboard/profile?step=2",
            },
        )
        experience = {"season_focus": MAY_FILING_FOCUS, "cards": cards, "season_label": "5월 신고 시즌", "strength": "strong"}
        decorate_seasonal_cards_for_tracking(
            experience,
            source_screen="overview",
            month_key="2026-05",
            click_url_builder=lambda metric_payload, target_url: f"/track/{metric_payload['card_type']}?to={target_url}",
        )
        self.assertIn("metric_payload", experience["cards"][0])
        self.assertIn("/track/", experience["cards"][0]["metric_cta_url"])

        context = build_seasonal_screen_context(experience, "review")
        decorate_seasonal_context_for_tracking(
            context,
            month_key="2026-05",
            click_url_builder=lambda metric_payload, target_url: f"/track-context/{metric_payload['card_type']}?to={target_url}",
        )
        self.assertEqual(context["source_screen"], "review")
        self.assertTrue(context["metric_cta_url"].startswith("/track-context/"))

    def test_routes_and_templates_wire_shown_clicked_and_landed(self) -> None:
        overview_route = self._read("routes/web/overview.py")
        review_route = self._read("routes/web/calendar/review.py")
        tax_route = self._read("routes/web/calendar/tax.py")
        package_route = self._read("routes/web/package.py")
        overview_template = self._read("templates/overview.html")
        review_template = self._read("templates/calendar/review.html")
        tax_template = self._read("templates/calendar/tax_buffer.html")
        package_template = self._read("templates/package/index.html")

        self.assertIn("seasonal_card_click", overview_route)
        self.assertIn("seasonal_card_shown", overview_route)
        self.assertIn("seasonal_card_clicked", overview_route)
        self.assertIn("seasonal_card_landed", review_route)
        self.assertIn("seasonal_card_landed", tax_route)
        self.assertIn("seasonal_card_landed", package_route)
        self.assertIn("metric_cta_url", overview_template)
        self.assertIn("metric_cta_url", review_template)
        self.assertIn("metric_cta_url", tax_template)
        self.assertIn("metric_cta_url", package_template)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from services.nhis_runtime import evaluate_nhis_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class NhisInlineFirstFieldFlowTest(unittest.TestCase):
    def test_missing_membership_type_is_top_blocking_reason(self) -> None:
        required = evaluate_nhis_required_inputs(
            estimate={"member_type": "unknown"},
            profile={"member_type": "unknown"},
            official_ready=True,
        )
        self.assertEqual(str(required.get("blocked_reason") or ""), "missing_membership_type")

    def test_inline_membership_card_is_rendered_on_overview_and_nhis(self) -> None:
        overview = (ROOT / "templates/overview.html").read_text(encoding="utf-8")
        nhis = (ROOT / "templates/nhis.html").read_text(encoding="utf-8")

        self.assertIn("nhis_result_meta and nhis_result_meta.reason == 'missing_membership_type'", overview)
        self.assertIn("nhis_membership_type_quick_save", overview)
        self.assertIn("가입유형 1문항", overview)
        self.assertIn("바로 저장", overview)

        self.assertIn("nhis_meta and nhis_meta.reason == 'missing_membership_type'", nhis)
        self.assertIn("nhis_membership_type_quick_save", nhis)
        self.assertIn("가입유형 먼저 저장", nhis)

    def test_membership_quick_save_routes_to_next_step_view(self) -> None:
        profile_route = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("event=\"nhis_inline_membership_type_saved\"", profile_route)
        self.assertIn("event=\"nhis_detail_next_step_viewed\"", profile_route)
        self.assertIn("inline_saved=\"member_type\"", profile_route)


if __name__ == "__main__":
    unittest.main()

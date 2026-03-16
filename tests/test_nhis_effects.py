from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from services.nhis_effects import (
    NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE,
    NHIS_EFFECT_STATUS_REVIEW_NEEDED,
    NHIS_EFFECT_STATUS_STALE,
    build_nhis_visual_feedback,
    build_nhis_effect_state,
    is_nhis_snapshot_stale,
)


class NhisEffectsTest(unittest.TestCase):
    def _doc(self, **overrides):
        base = {
            "id": 1,
            "document_type": "nhis_payment_confirmation",
            "parse_status": "parsed",
            "trust_grade": "B",
            "structure_validation_status": "passed",
            "verified_reference_date": date(2026, 3, 3),
            "extracted_payload_json": {"total_paid_amount_krw": 333000},
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _eligibility_doc(self, **overrides):
        base = {
            "id": 11,
            "document_type": "nhis_eligibility_status",
            "parse_status": "parsed",
            "trust_grade": "B",
            "structure_validation_status": "passed",
            "verified_reference_date": date(2026, 3, 11),
            "extracted_payload_json": {
                "subscriber_type": "지역가입자",
                "eligibility_status": "유지",
                "eligibility_start_date": "2025-07-01",
                "latest_status_change_date": "2026-02-01",
            },
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_stale_rule(self) -> None:
        self.assertTrue(is_nhis_snapshot_stale(date(2025, 10, 1), today=date(2026, 3, 16)))
        self.assertFalse(is_nhis_snapshot_stale(date(2026, 3, 1), today=date(2026, 3, 16)))

    def test_reference_available_for_fresh_document(self) -> None:
        state = build_nhis_effect_state([self._doc()], today=date(2026, 3, 16))
        self.assertEqual(state["nhis_effect_status"], NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE)
        self.assertEqual(state["nhis_latest_paid_amount_krw"], 333000)
        self.assertFalse(state["nhis_recheck_required"])

    def test_stale_document_requires_recheck(self) -> None:
        state = build_nhis_effect_state([self._doc(verified_reference_date=date(2025, 10, 1))], today=date(2026, 3, 16))
        self.assertEqual(state["nhis_effect_status"], NHIS_EFFECT_STATUS_STALE)
        self.assertTrue(state["nhis_recheck_required"])

    def test_d_grade_document_is_review_needed(self) -> None:
        state = build_nhis_effect_state([self._doc(trust_grade="D")], today=date(2026, 3, 16))
        self.assertEqual(state["nhis_effect_status"], NHIS_EFFECT_STATUS_REVIEW_NEEDED)

    def test_nhis_never_reports_full_certainty_copy(self) -> None:
        state = build_nhis_effect_state([self._doc(trust_grade="A")], today=date(2026, 3, 16))
        self.assertNotIn("확정", state["nhis_effect_reason"])

    def test_eligibility_document_enriches_reason_without_setting_paid_amount(self) -> None:
        state = build_nhis_effect_state([self._eligibility_doc()], today=date(2026, 3, 16))
        self.assertEqual(state["nhis_effect_status"], NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE)
        self.assertEqual(state["nhis_latest_paid_amount_krw"], 0)
        self.assertIn("지역가입자 유지", state["nhis_effect_reason"])
        self.assertTrue(state["nhis_recheck_required"])

    def test_payment_and_eligibility_documents_can_be_combined(self) -> None:
        state = build_nhis_effect_state([self._doc(), self._eligibility_doc()], today=date(2026, 3, 16))
        self.assertEqual(state["nhis_effect_status"], NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE)
        self.assertEqual(state["nhis_latest_paid_amount_krw"], 333000)
        self.assertIn("자격 상태 참고", state["nhis_effect_reason"])

    def test_visual_feedback_highlights_reference_without_animation(self) -> None:
        state = build_nhis_effect_state([self._doc(), self._eligibility_doc()], today=date(2026, 3, 16))
        feedback = build_nhis_visual_feedback(state)
        self.assertTrue(feedback["show"])
        self.assertTrue(feedback["should_highlight_reference"])
        self.assertFalse(feedback["should_animate"])
        self.assertIn("납부확인 참고", feedback["source_labels"])
        self.assertIn("자격자료 참고", feedback["source_labels"])

    def test_visual_feedback_marks_review_needed_without_highlight(self) -> None:
        state = build_nhis_effect_state([self._doc(trust_grade="D")], today=date(2026, 3, 16))
        feedback = build_nhis_visual_feedback(state)
        self.assertEqual(feedback["nhis_effect_status"], NHIS_EFFECT_STATUS_REVIEW_NEEDED)
        self.assertFalse(feedback["should_highlight_reference"])
        self.assertEqual(feedback["nhis_feedback_level"], "review")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from services.official_data_effects import (
    TAX_EFFECT_STATUS_APPLIED,
    TAX_EFFECT_STATUS_REFERENCE_ONLY,
    TAX_EFFECT_STATUS_REVIEW_NEEDED,
    TAX_EFFECT_STATUS_STALE,
    build_official_tax_visual_feedback,
    build_official_tax_visual_feedback_for_overview,
    build_official_tax_effect_state,
)


class OfficialDataEffectsTest(unittest.TestCase):
    def _doc(self, **overrides):
        base = {
            "id": 1,
            "document_type": "hometax_withholding_statement",
            "parse_status": "parsed",
            "trust_grade": "B",
            "structure_validation_status": "passed",
            "verified_reference_date": date(2026, 3, 5),
            "document_period_start": date(2026, 3, 1),
            "document_period_end": date(2026, 3, 31),
            "extracted_payload_json": {"total_withheld_tax_krw": 120000},
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _payment_doc(self, **overrides):
        base = {
            "id": 2,
            "document_type": "hometax_tax_payment_history",
            "parse_status": "parsed",
            "trust_grade": "B",
            "structure_validation_status": "passed",
            "verified_reference_date": date(2026, 3, 12),
            "document_period_start": date(2026, 3, 1),
            "document_period_end": date(2026, 3, 31),
            "extracted_payload_json": {"paid_tax_total_krw": 70000},
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_a_or_b_documents_are_applied(self) -> None:
        state = build_official_tax_effect_state([self._doc(trust_grade="A")], month_key="2026-03", today=date(2026, 3, 16))
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_APPLIED)
        self.assertEqual(state["official_tax_effect_strength"], "strong")
        self.assertEqual(state["official_withheld_tax_krw"], 120000)

    def test_c_grade_is_reference_only(self) -> None:
        state = build_official_tax_effect_state([self._doc(trust_grade="C")], month_key="2026-03", today=date(2026, 3, 16))
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_REFERENCE_ONLY)
        self.assertEqual(state["official_tax_effect_strength"], "weak")
        self.assertEqual(state["official_withheld_tax_krw"], 0)

    def test_d_grade_is_review_needed(self) -> None:
        state = build_official_tax_effect_state([self._doc(trust_grade="D")], month_key="2026-03", today=date(2026, 3, 16))
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_REVIEW_NEEDED)
        self.assertEqual(state["official_tax_effect_strength"], "none")

    def test_stale_document_is_not_auto_applied(self) -> None:
        state = build_official_tax_effect_state(
            [self._doc(verified_reference_date=date(2025, 6, 1), document_period_start=date(2026, 3, 1), document_period_end=date(2026, 3, 31))],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_STALE)
        self.assertEqual(state["official_withheld_tax_krw"], 0)

    def test_supported_reference_document_stays_reference_only(self) -> None:
        state = build_official_tax_effect_state(
            [
                self._doc(
                    document_type="hometax_business_card_usage",
                    extracted_payload_json={"total_card_usage_krw": 800000},
                )
            ],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_REFERENCE_ONLY)
        self.assertIn("참고 정보", state["official_tax_effect_reason"])

    def test_payment_history_document_is_applied_to_paid_tax(self) -> None:
        state = build_official_tax_effect_state([self._payment_doc()], month_key="2026-03", today=date(2026, 3, 16))
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_APPLIED)
        self.assertEqual(state["official_paid_tax_krw"], 70000)
        self.assertEqual(state["official_withheld_tax_krw"], 0)
        self.assertIn("이미 납부한 세금", state["official_tax_effect_reason"])

    def test_withholding_and_payment_history_can_be_combined(self) -> None:
        state = build_official_tax_effect_state(
            [self._doc(trust_grade="A"), self._payment_doc()],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_APPLIED)
        self.assertEqual(state["official_withheld_tax_krw"], 120000)
        self.assertEqual(state["official_paid_tax_krw"], 70000)
        self.assertIn("이미 빠진 세금과 이미 납부한 세금", state["official_tax_effect_reason"])

    def test_c_grade_payment_history_stays_reference_only(self) -> None:
        state = build_official_tax_effect_state(
            [self._payment_doc(trust_grade="C")],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        self.assertEqual(state["official_tax_effect_status"], TAX_EFFECT_STATUS_REFERENCE_ONLY)
        self.assertEqual(state["official_paid_tax_krw"], 0)

    def test_visual_feedback_animates_only_for_applied_delta(self) -> None:
        state = build_official_tax_effect_state(
            [self._doc(trust_grade="A"), self._payment_doc()],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        feedback = build_official_tax_visual_feedback_for_overview(
            state,
            before_tax_due_krw=150000,
            after_tax_due_krw=0,
        )
        self.assertTrue(feedback["show"])
        self.assertEqual(feedback["tax_delta_krw"], -150000)
        self.assertTrue(feedback["should_animate"])
        self.assertIn("원천징수 반영", feedback["source_labels"])
        self.assertIn("납부내역 반영", feedback["source_labels"])

    def test_visual_feedback_disables_animation_for_reference_only(self) -> None:
        state = build_official_tax_effect_state(
            [self._doc(trust_grade="C")],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        feedback = build_official_tax_visual_feedback(
            state,
            before_tax_due_krw=150000,
            after_tax_due_krw=150000,
        )
        self.assertEqual(feedback["status"], TAX_EFFECT_STATUS_REFERENCE_ONLY)
        self.assertFalse(feedback["should_animate"])
        self.assertEqual(feedback["feedback_level"], "soft")

    def test_visual_feedback_stays_idle_without_delta(self) -> None:
        state = build_official_tax_effect_state(
            [self._doc(trust_grade="A")],
            month_key="2026-03",
            today=date(2026, 3, 16),
        )
        feedback = build_official_tax_visual_feedback(
            state,
            before_tax_due_krw=50000,
            after_tax_due_krw=50000,
        )
        self.assertFalse(feedback["should_animate"])
        self.assertEqual(feedback["tax_delta_krw"], 0)


if __name__ == "__main__":
    unittest.main()

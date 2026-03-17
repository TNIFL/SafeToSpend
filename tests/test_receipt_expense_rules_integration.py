from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from services.receipt_expense_guidance import build_receipt_expense_inline_guidance


ROOT = Path(__file__).resolve().parents[1]


def _tx(
    *,
    counterparty: str = "",
    memo: str = "",
    amount_krw: int = 0,
    occurred_at: datetime | None = None,
    direction: str = "out",
):
    return SimpleNamespace(
        counterparty=counterparty,
        memo=memo,
        amount_krw=amount_krw,
        occurred_at=occurred_at,
        direction=direction,
        source="csv",
    )


class ReceiptExpenseRulesIntegrationTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_inline_guidance_uses_canonical_evaluator_levels(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="KTX",
                memo="출장 교통비",
                amount_krw=58900,
                occurred_at=datetime(2026, 3, 13, 11, 20),
            ),
            focus_kind="receipt_required",
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["level"], "high_likelihood")
        self.assertEqual(payload["tone"], "high")
        self.assertEqual(payload["anchor"], "high-likelihood")
        self.assertEqual(payload["guide_anchor"], "high-likelihood")

    def test_inline_guidance_exposes_follow_up_questions_for_review_cases(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="스타벅스",
                memo="거래처 미팅 커피",
                amount_krw=18000,
                occurred_at=datetime(2026, 3, 15, 21, 30),
            ),
            draft={"attendee_note": "거래처 2명"},
            focus_kind="receipt_attach",
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["level"], "needs_review")
        self.assertEqual(payload["tone"], "review")
        self.assertTrue(payload["follow_up_questions"])
        self.assertEqual(payload["follow_up_questions"][0]["question_key"], "business_meal_with_client")
        self.assertEqual(payload["guide_anchor"], "needs-review")

    def test_inline_guidance_restores_follow_up_answers(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="스타벅스",
                memo="거래처 미팅 커피",
                amount_krw=18000,
                occurred_at=datetime(2026, 3, 12, 14, 30),
            ),
            draft={"attendee_note": "거래처 2명"},
            focus_kind="receipt_attach",
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅 · 참석자 2명",
                }
            },
        )
        self.assertIsNotNone(payload)
        self.assertTrue(payload["applied_follow_up_answers"])
        self.assertEqual(payload["follow_up_questions"][0]["current_value"], "yes")
        self.assertEqual(payload["follow_up_questions"][0]["current_text"], "A사 미팅 · 참석자 2명")

    def test_inline_guidance_blocks_non_outgoing_transactions(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="급여입금",
                memo="입금",
                amount_krw=2500000,
                occurred_at=datetime(2026, 3, 12, 9, 0),
                direction="in",
            ),
            focus_kind="receipt_attach",
        )
        self.assertIsNone(payload)

    def test_hint_partial_renders_follow_up_and_evidence_sections(self) -> None:
        body = self._read("templates/calendar/partials/receipt_expense_hint.html")
        self.assertIn("expense_guidance.follow_up_questions", body)
        self.assertIn("expense_guidance.evidence_requirements", body)
        self.assertIn("expense_guidance.applied_follow_up_answers", body)
        self.assertIn("followup__", body)
        self.assertIn("guidance_tone", body)

    def test_review_route_and_templates_still_bind_guidance_partial(self) -> None:
        route_body = self._read("routes/web/calendar/review.py")
        review_body = self._read("templates/calendar/review.html")
        upload_body = self._read("templates/calendar/partials/receipt_wizard_upload.html")
        confirm_body = self._read("templates/calendar/partials/receipt_wizard_confirm.html")
        match_body = self._read("templates/calendar/partials/receipt_wizard_match.html")
        self.assertIn("build_receipt_expense_inline_guidance", route_body)
        self.assertIn("review_expense_followup_save", route_body)
        self.assertIn("save_receipt_follow_up_answers_and_re_evaluate", route_body)
        self.assertIn('include "calendar/partials/receipt_expense_hint.html"', review_body)
        self.assertIn("expense_followup_action", review_body)
        self.assertIn('include "calendar/partials/receipt_expense_hint.html"', upload_body)
        self.assertIn('include "calendar/partials/receipt_expense_hint.html"', confirm_body)
        self.assertIn("expense_followup_action", confirm_body)
        self.assertIn('include "calendar/partials/receipt_expense_hint.html"', match_body)
        self.assertIn("expense_followup_action", match_body)


if __name__ == "__main__":
    unittest.main()

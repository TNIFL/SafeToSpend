from __future__ import annotations

import unittest

from services.receipt_expense_rules import evaluate_receipt_expense_with_follow_up


class ReceiptExpenseFollowupRulesTest(unittest.TestCase):
    def test_client_meal_followup_stays_needs_review_until_reinforced(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "스타벅스",
                "memo": "거래처 미팅 커피",
                "amount_krw": 18000,
                "approved_at": "2026-03-12 14:30",
                "attendee_note": "거래처 2명",
            },
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅 · 참석자 2명",
                }
            },
        )
        self.assertEqual(decision["level"], "needs_review")
        self.assertTrue(decision["applied_follow_up_answers"])
        self.assertIn("보강 정보", decision["summary"])

    def test_weekend_transport_reason_updates_why_and_promotes(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "택시",
                "memo": "출장 후 귀가",
                "amount_krw": 24000,
                "approved_at": "2026-03-14 23:30",
            },
            follow_up_answers={
                "weekend_or_late_night_business_reason": {
                    "answer_text": "토요일 고객사 방문 후 이동",
                }
            },
        )
        self.assertEqual(decision["level"], "high_likelihood")
        self.assertIn("업무 관련 이동 사유", decision["summary"])
        self.assertTrue(decision["applied_follow_up_answers"])

    def test_personal_meal_without_sufficient_reason_stays_blocked(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "김밥천국",
                "memo": "본인 식사",
                "amount_krw": 9000,
                "approved_at": "2026-03-12 12:20",
            },
            follow_up_answers={
                "personal_meal_exception_reason": {
                    "answer_text": "점심",
                }
            },
        )
        self.assertEqual(decision["level"], "do_not_auto_allow")

    def test_high_value_asset_stays_consult_tax_review(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "Apple Store",
                "memo": "맥북 구입",
                "amount_krw": 2190000,
                "approved_at": "2026-03-08 13:00",
            },
            follow_up_answers={
                "asset_vs_consumable": {
                    "answer_value": "asset",
                    "answer_text": "업무용 편집 장비",
                }
            },
        )
        self.assertEqual(decision["level"], "consult_tax_review")
        self.assertIn("자산", decision["why"])

    def test_ceremonial_spend_stays_conservative(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "모바일상품권",
                "memo": "거래처 경조사 선물",
                "amount_krw": 50000,
                "approved_at": "2026-03-09 10:00",
            },
            follow_up_answers={
                "ceremonial_business_related": {
                    "answer_value": "yes",
                    "answer_text": "거래처 경조사",
                }
            },
        )
        self.assertEqual(decision["level"], "consult_tax_review")
        self.assertTrue(decision["applied_follow_up_answers"])

    def test_without_followup_answers_decision_stays_first_pass(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "스타벅스",
                "memo": "거래처 미팅 커피",
                "amount_krw": 18000,
                "approved_at": "2026-03-12 14:30",
                "attendee_note": "거래처 2명",
            }
        )
        self.assertEqual(decision["level"], "needs_review")
        self.assertTrue(decision["follow_up_questions"])


if __name__ == "__main__":
    unittest.main()

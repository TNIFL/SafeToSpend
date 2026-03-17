from __future__ import annotations

import unittest

from services.receipt_expense_rules import evaluate_receipt_expense_with_follow_up


class ReceiptExpenseReinforcementRulesTest(unittest.TestCase):
    def test_business_meal_requires_reinforcement_for_limited_promotion(self) -> None:
        first = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "스타벅스",
                "memo": "거래처 미팅 커피",
                "amount_krw": 18000,
                "approved_at": "2026-03-12 14:30",
            },
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅",
                }
            },
        )
        self.assertEqual(first["level"], "needs_review")
        self.assertIn("업무 관련 설명", first["remaining_gaps"])

        second = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "스타벅스",
                "memo": "거래처 미팅 커피",
                "amount_krw": 18000,
                "approved_at": "2026-03-12 14:30",
            },
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅",
                }
            },
            reinforcement_data={
                "business_context_note": "A사 제안 미팅 중 음료 결제",
                "attendee_names": "A사 김팀장, 박대리",
                "client_or_counterparty_name": "A사",
            },
        )
        self.assertEqual(second["level"], "high_likelihood")
        self.assertEqual(second["reinforcement_readiness"], "sufficient")
        self.assertFalse(second["remaining_gaps"])

    def test_weekend_transport_reason_can_promote_and_reduce_gaps(self) -> None:
        first = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "택시",
                "memo": "이동",
                "amount_krw": 24000,
                "approved_at": "2026-03-14 23:30",
            },
        )
        self.assertEqual(first["level"], "needs_review")
        self.assertIn("주말·심야 사유", first["remaining_gaps"])

        second = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "택시",
                "memo": "이동",
                "amount_krw": 24000,
                "approved_at": "2026-03-14 23:30",
            },
            reinforcement_data={
                "weekend_or_late_night_note": "토요일 고객사 야간 점검 후 복귀",
            },
        )
        self.assertEqual(second["level"], "high_likelihood")
        self.assertEqual(second["reinforcement_readiness"], "sufficient")
        self.assertIn("업무 관련 이동 사유", second["summary"])

    def test_personal_meal_stays_conservative_even_with_reinforcement(self) -> None:
        decision = evaluate_receipt_expense_with_follow_up(
            payload={
                "merchant_name": "김밥천국",
                "memo": "본인 식사",
                "amount_krw": 9000,
                "approved_at": "2026-03-12 12:20",
            },
            reinforcement_data={
                "business_context_note": "점심 중 메모",
                "attendee_names": "혼자",
            },
        )
        self.assertEqual(decision["level"], "do_not_auto_allow")
        self.assertIn(decision["reinforcement_readiness"], {"partial", "sufficient"})

    def test_high_value_asset_stays_consult_even_after_reinforcement(self) -> None:
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
                    "answer_text": "업무용 장비",
                }
            },
            reinforcement_data={
                "asset_usage_note": "영상 편집 업무용 메인 장비",
                "business_context_note": "영상 제작 업무용",
                "supporting_file_key": "u7/2026-03/tx11/support.pdf",
                "supporting_file_name": "usage-note.pdf",
            },
        )
        self.assertEqual(decision["level"], "consult_tax_review")
        self.assertEqual(decision["reinforcement_readiness"], "sufficient")
        self.assertIn("자산", decision["why"])

    def test_condolence_stays_conservative_even_after_reinforcement(self) -> None:
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
            reinforcement_data={
                "ceremonial_relation_note": "거래처 대표 모친상",
                "business_context_note": "거래처 관계 유지 목적",
                "supporting_file_name": "부고문자.txt",
                "supporting_file_key": "u7/2026-03/tx11/condolence.txt",
            },
        )
        self.assertEqual(decision["level"], "consult_tax_review")
        self.assertIn("보강 파일", [row["label"] for row in decision["applied_reinforcement"]])
        self.assertIn(decision["reinforcement_readiness"], {"partial", "sufficient"})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from services.receipt_expense_rules import (
    ReceiptExpenseInput,
    evaluate_receipt_expense,
    normalize_receipt_expense_input,
)


def _tx(
    *,
    counterparty: str = "",
    memo: str = "",
    amount_krw: int | str = 0,
    occurred_at: datetime | str | None = None,
    payment_method: str = "",
):
    return SimpleNamespace(
        id=101,
        counterparty=counterparty,
        memo=memo,
        amount_krw=amount_krw,
        occurred_at=occurred_at,
        payment_method=payment_method,
    )


class ReceiptExpenseRulesEngineNormalizationTest(unittest.TestCase):
    def test_normalize_receipt_input_happy_path(self) -> None:
        normalized = normalize_receipt_expense_input(
            {
                "merchant_name": " KTX ",
                "approved_at": "2026-03-13 11:20",
                "amount_krw": "58,900원",
                "payment_method": " card ",
                "source_text_raw": "출장 교통비 영수증",
                "candidate_transaction_id": "123",
            }
        )
        self.assertIsInstance(normalized, ReceiptExpenseInput)
        self.assertEqual(normalized.merchant_name, "KTX")
        self.assertEqual(normalized.amount_krw, 58900)
        self.assertEqual(normalized.payment_method, "card")
        self.assertEqual(normalized.candidate_transaction_id, 123)
        self.assertEqual(normalized.approved_at, datetime(2026, 3, 13, 11, 20))
        self.assertFalse(normalized.weekend_flag)
        self.assertFalse(normalized.late_night_flag)
        self.assertEqual(normalized.source_text_normalized, "출장 교통비 영수증")

    def test_normalize_receipt_input_handles_missing_fields(self) -> None:
        normalized = normalize_receipt_expense_input({})
        self.assertEqual(normalized.merchant_name, "")
        self.assertEqual(normalized.amount_krw, 0)
        self.assertIsNone(normalized.approved_at)
        self.assertFalse(normalized.weekend_flag)
        self.assertFalse(normalized.late_night_flag)

    def test_normalize_receipt_input_parses_string_amount_and_tx_fallbacks(self) -> None:
        normalized = normalize_receipt_expense_input(
            {"amount_krw": "15,800"},
            tx=_tx(counterparty="스타벅스", memo="회의 커피", occurred_at="2026-03-14T23:10:00"),
        )
        self.assertEqual(normalized.merchant_name, "스타벅스")
        self.assertEqual(normalized.counterparty, "스타벅스")
        self.assertEqual(normalized.memo, "회의 커피")
        self.assertEqual(normalized.amount_krw, 15800)
        self.assertTrue(normalized.weekend_flag)
        self.assertTrue(normalized.late_night_flag)

    def test_normalize_receipt_input_preserves_merchant_counterparty_memo_combinations(self) -> None:
        normalized = normalize_receipt_expense_input(
            {"merchant_name": "교보문고", "memo": "업무 참고 도서"},
            tx=_tx(counterparty="KB카드"),
        )
        self.assertEqual(normalized.merchant_name, "교보문고")
        self.assertEqual(normalized.counterparty, "KB카드")
        self.assertEqual(normalized.memo, "업무 참고 도서")


class ReceiptExpenseRulesEngineDecisionTest(unittest.TestCase):
    def _evaluate(self, **payload):
        return evaluate_receipt_expense(payload)

    def _question_prompts(self, decision):
        return [str(row.get("prompt") or "") for row in decision.get("follow_up_questions") or []]

    def test_transport_case_maps_to_high_likelihood(self) -> None:
        decision = self._evaluate(
            merchant_name="KTX",
            memo="출장 교통비",
            amount_krw=58900,
            approved_at="2026-03-13 11:20",
        )
        self.assertEqual(decision["level"], "high_likelihood")
        self.assertEqual(decision["guide_anchor"], "high-likelihood")
        self.assertFalse(decision["follow_up_questions"])

    def test_weekend_transport_drops_to_needs_review(self) -> None:
        decision = self._evaluate(
            merchant_name="택시",
            memo="출장 후 귀가",
            amount_krw=24000,
            approved_at="2026-03-14 23:30",
        )
        self.assertEqual(decision["level"], "needs_review")
        self.assertIn("주말·심야 결제 사유를 남길 수 있나요?", self._question_prompts(decision))

    def test_books_case_maps_to_high_likelihood(self) -> None:
        decision = self._evaluate(
            merchant_name="교보문고",
            memo="업무 참고 도서",
            amount_krw=28000,
            approved_at="2026-03-12 14:00",
        )
        self.assertEqual(decision["level"], "high_likelihood")

    def test_paid_course_case_maps_to_high_likelihood(self) -> None:
        decision = self._evaluate(
            merchant_name="패스트캠퍼스",
            memo="업무 강의 수강",
            amount_krw=129000,
            approved_at="2026-03-11 09:20",
        )
        self.assertEqual(decision["level"], "high_likelihood")

    def test_office_supplies_case_maps_to_high_likelihood(self) -> None:
        decision = self._evaluate(
            merchant_name="다이소",
            memo="사무용 소모품",
            amount_krw=18500,
            approved_at="2026-03-10 17:10",
        )
        self.assertEqual(decision["level"], "high_likelihood")

    def test_cafe_case_maps_to_needs_review(self) -> None:
        decision = self._evaluate(
            merchant_name="스타벅스",
            memo="커피",
            amount_krw=6100,
            approved_at="2026-03-12 15:05",
        )
        self.assertEqual(decision["level"], "needs_review")
        self.assertIn("거래처와의 식사인가요?", self._question_prompts(decision))

    def test_personal_meal_case_maps_to_do_not_auto_allow(self) -> None:
        decision = self._evaluate(
            merchant_name="김밥천국",
            memo="본인 식사",
            amount_krw=9000,
            approved_at="2026-03-12 12:20",
        )
        self.assertEqual(decision["level"], "do_not_auto_allow")
        self.assertEqual(decision["guide_anchor"], "do-not-auto")

    def test_client_meal_case_maps_to_needs_review(self) -> None:
        decision = self._evaluate(
            merchant_name="스타벅스",
            memo="거래처 미팅 커피",
            amount_krw=18000,
            approved_at="2026-03-12 14:30",
            attendee_note="거래처 2명",
        )
        self.assertEqual(decision["level"], "needs_review")
        self.assertIn("거래처와의 식사인가요?", self._question_prompts(decision))

    def test_high_value_asset_case_maps_to_consult_tax_review(self) -> None:
        decision = self._evaluate(
            merchant_name="Apple Store",
            memo="맥북 구입",
            amount_krw=2190000,
            approved_at="2026-03-08 13:00",
        )
        self.assertEqual(decision["level"], "consult_tax_review")
        self.assertIn("업무용 자산인가요, 소모품인가요?", self._question_prompts(decision))

    def test_condolence_case_maps_to_consult_tax_review(self) -> None:
        decision = self._evaluate(
            merchant_name="모바일상품권",
            memo="거래처 경조사 선물",
            amount_krw=50000,
            approved_at="2026-03-09 10:00",
        )
        self.assertEqual(decision["level"], "consult_tax_review")
        self.assertIn("업무 관련 경조사비인가요?", self._question_prompts(decision))

    def test_default_case_stays_conservative(self) -> None:
        decision = self._evaluate(
            merchant_name="알수없음",
            memo="",
            amount_krw=14000,
        )
        self.assertEqual(decision["level"], "needs_review")
        self.assertEqual(decision["guide_anchor"], "needs-review")
        self.assertTrue(decision["evidence_requirements"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from services.receipt_expense_guidance import (
    build_receipt_expense_inline_guidance,
    get_receipt_expense_guidance_content,
)


ROOT = Path(__file__).resolve().parents[1]


def _tx(
    *,
    counterparty: str = "",
    memo: str = "",
    amount_krw: int = 0,
    occurred_at: datetime | None = None,
    direction: str = "out",
    source: str = "csv",
):
    return SimpleNamespace(
        counterparty=counterparty,
        memo=memo,
        amount_krw=amount_krw,
        occurred_at=occurred_at,
        direction=direction,
        source=source,
    )


class ReceiptExpenseInlineGuidanceTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_transport_like_spend_maps_to_high_guidance(self) -> None:
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
        self.assertIn("비용처리 가능성이 높은 편이에요", payload["label"])

    def test_meal_like_spend_maps_to_review_guidance(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="스타벅스",
                memo="커피 미팅",
                amount_krw=12800,
                occurred_at=datetime(2026, 3, 15, 21, 30),
            ),
            focus_kind="receipt_attach",
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["level"], "needs_review")
        self.assertEqual(payload["tone"], "review")
        self.assertIn("추가 확인이 필요해요", payload["label"])
        self.assertTrue(payload["follow_up_questions"])
        self.assertEqual(payload["follow_up_questions"][0]["question_key"], "business_meal_with_client")

    def test_gift_or_condolence_like_spend_maps_to_block_guidance(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="모바일상품권",
                memo="거래처 선물 기프티콘",
                amount_krw=50000,
                occurred_at=datetime(2026, 3, 10, 14, 0),
            ),
            focus_kind="expense_confirm",
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["level"], "consult_tax_review")
        self.assertEqual(payload["tone"], "consult")
        self.assertIn("세무 검토가 필요할 수 있어요", payload["label"])

    def test_high_value_device_maps_to_consult_guidance(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(
                counterparty="전자랜드",
                memo="노트북 구입",
                amount_krw=1890000,
                occurred_at=datetime(2026, 3, 4, 16, 30),
            ),
            focus_kind="expense_confirm",
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["level"], "consult_tax_review")
        self.assertEqual(payload["tone"], "consult")
        self.assertIn("세무 검토가 필요할 수 있어요", payload["label"])

    def test_page_context_contains_four_guidance_buckets(self) -> None:
        payload = get_receipt_expense_guidance_content()
        anchors = [row["anchor"] for row in payload["quick_buckets"]]
        self.assertEqual(
            anchors,
            ["high-likelihood", "needs-review", "do-not-auto", "consult"],
        )

    def test_review_and_receipt_templates_include_inline_hint_partial(self) -> None:
        review_body = self._read("templates/calendar/review.html")
        upload_body = self._read("templates/calendar/partials/receipt_wizard_upload.html")
        partial_body = self._read("templates/calendar/partials/receipt_expense_hint.html")
        self.assertIn('include "calendar/partials/receipt_expense_hint.html"', review_body)
        self.assertIn('include "calendar/partials/receipt_expense_hint.html"', upload_body)
        self.assertIn("왜 이렇게 보나요?", partial_body)
        self.assertIn("서비스의 분류 결과는 보조 판단입니다.", partial_body)
        self.assertIn("expense_guidance.follow_up_questions", partial_body)
        self.assertIn("expense_guidance.applied_follow_up_answers", partial_body)


if __name__ == "__main__":
    unittest.main()

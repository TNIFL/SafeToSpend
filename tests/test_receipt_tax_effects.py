from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.receipt_tax_effects import (
    compute_receipt_tax_effects_for_month,
    summarize_receipt_tax_effect_entries,
)


def _tx(
    *,
    tx_id: int,
    counterparty: str,
    memo: str,
    amount_krw: int,
    occurred_at: str = "2026-03-12 14:30",
):
    return SimpleNamespace(
        id=tx_id,
        counterparty=counterparty,
        memo=memo,
        amount_krw=amount_krw,
        occurred_at=__import__("datetime").datetime.strptime(occurred_at, "%Y-%m-%d %H:%M"),
        direction="out",
    )


def _ev(*, file_key: str = "u/receipt.jpg", note: str = ""):
    return SimpleNamespace(file_key=file_key, note=note)


def _label(status: str):
    return SimpleNamespace(status=status)


class ReceiptTaxEffectsTest(unittest.TestCase):
    def test_summary_only_reflects_high_likelihood_and_dedupes_transactions(self) -> None:
        summary = summarize_receipt_tax_effect_entries(
            [
                {
                    "transaction_id": 1,
                    "amount_krw": 32000,
                    "level": "high_likelihood",
                    "summary": "교통비",
                    "reason": "출장 이동",
                    "expense_status": "unknown",
                },
                {
                    "transaction_id": 1,
                    "amount_krw": 32000,
                    "level": "needs_review",
                    "summary": "중복",
                    "reason": "중복",
                    "expense_status": "unknown",
                },
                {
                    "transaction_id": 2,
                    "amount_krw": 18000,
                    "level": "needs_review",
                    "summary": "카페",
                    "reason": "추가 확인",
                    "expense_status": "unknown",
                },
                {
                    "transaction_id": 3,
                    "amount_krw": 11000,
                    "level": "do_not_auto_allow",
                    "summary": "본인 식사",
                    "reason": "개인 지출",
                    "expense_status": "unknown",
                },
                {
                    "transaction_id": 4,
                    "amount_krw": 1250000,
                    "level": "consult_tax_review",
                    "summary": "맥북",
                    "reason": "고가 장비",
                    "expense_status": "unknown",
                },
            ]
        )
        self.assertEqual(summary.reflected_expense_krw, 32000)
        self.assertEqual(summary.pending_review_expense_krw, 18000)
        self.assertEqual(summary.excluded_expense_krw, 11000)
        self.assertEqual(summary.consult_tax_review_expense_krw, 1250000)
        self.assertEqual(summary.reflected_transaction_count, 1)
        self.assertEqual(summary.pending_transaction_count, 1)

    def test_compute_monthly_effects_skips_manual_business_and_personal_labels(self) -> None:
        rows = [
            (_tx(tx_id=11, counterparty="KTX", memo="출장", amount_krw=58900), _label("business"), _ev()),
            (_tx(tx_id=12, counterparty="택시", memo="출장", amount_krw=22000), _label("personal"), _ev()),
            (_tx(tx_id=13, counterparty="KTX", memo="출장", amount_krw=61100), _label("unknown"), _ev()),
        ]
        with (
            patch("services.receipt_tax_effects.load_receipt_follow_up_answers_map", return_value={}),
            patch("services.receipt_tax_effects.load_receipt_reinforcement_map", return_value={}),
        ):
            summary = compute_receipt_tax_effects_for_month(
                SimpleNamespace(),
                user_pk=7,
                month_key="2026-03",
                transaction_rows=rows,
            )
        self.assertEqual(summary.reflected_expense_krw, 61100)
        self.assertEqual(summary.skipped_manual_transaction_count, 2)
        self.assertEqual(summary.reflected_transaction_count, 1)

    def test_compute_monthly_effects_buckets_follow_up_and_reinforced_cases(self) -> None:
        rows = [
            (_tx(tx_id=21, counterparty="스타벅스", memo="거래처 미팅 커피", amount_krw=18000), _label("mixed"), _ev()),
            (_tx(tx_id=22, counterparty="김밥천국", memo="본인 식사", amount_krw=9000), _label("unknown"), _ev()),
            (_tx(tx_id=23, counterparty="Apple Store", memo="맥북 구입", amount_krw=2190000), _label("unknown"), _ev()),
        ]
        with (
            patch(
                "services.receipt_tax_effects.load_receipt_follow_up_answers_map",
                return_value={
                    21: {
                        "business_meal_with_client": {
                            "answer_value": "yes",
                            "answer_text": "A사 미팅",
                        }
                    }
                },
            ),
            patch(
                "services.receipt_tax_effects.load_receipt_reinforcement_map",
                return_value={
                    21: {
                        "business_context_note": "A사 제안 미팅 중 음료 결제",
                        "attendee_names": "A사 김팀장, 박대리",
                        "client_or_counterparty_name": "A사",
                    }
                },
            ),
        ):
            summary = compute_receipt_tax_effects_for_month(
                SimpleNamespace(),
                user_pk=7,
                month_key="2026-03",
                transaction_rows=rows,
            )
        self.assertEqual(summary.reflected_expense_krw, 18000)
        self.assertEqual(summary.excluded_expense_krw, 9000)
        self.assertEqual(summary.consult_tax_review_expense_krw, 2190000)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.receipt_batch import (
    ITEM_STATUS_FAILED,
    ITEM_STATUS_UPLOADED,
    can_retry_receipt_item,
    mark_receipt_item_failed,
    mark_receipt_item_paused,
    reset_receipt_item_for_retry,
)


class ReceiptRetryPolicyTest(unittest.TestCase):
    def _item(self) -> SimpleNamespace:
        return SimpleNamespace(
            status=ITEM_STATUS_FAILED,
            file_key="u1/2026-03/_draft/sample.jpg",
            error_message="",
            parsed_json={},
            updated_at=None,
        )

    def test_duplicate_error_is_non_retryable(self) -> None:
        item = self._item()
        mark_receipt_item_failed(item, "이미 처리 중이거나 완료된 같은 영수증이 있어요.")
        ok, reason = can_retry_receipt_item(item)
        self.assertFalse(ok)
        self.assertTrue(str(reason).startswith("non_retryable:duplicate"))

    def test_retry_limit_moves_to_dead_letter(self) -> None:
        item = self._item()
        for _ in range(3):
            mark_receipt_item_failed(item, "일시적인 timeout 오류")
        ok, reason = can_retry_receipt_item(item)
        self.assertFalse(ok)
        self.assertIn(reason, {"dead_letter", "retry_limit"})

    def test_backoff_blocks_immediate_retry(self) -> None:
        item = self._item()
        mark_receipt_item_failed(item, "일시적인 timeout 오류")
        ok, reason = can_retry_receipt_item(item)
        self.assertFalse(ok)
        self.assertTrue(str(reason).startswith("backoff:"))

    def test_reset_retry_reopens_failed_item(self) -> None:
        item = self._item()
        mark_receipt_item_failed(item, "일시적인 timeout 오류")
        reset_receipt_item_for_retry(item)
        self.assertEqual(item.status, ITEM_STATUS_UPLOADED)
        self.assertEqual(str(item.error_message or ""), "")

    def test_paused_item_is_retryable_without_dead_letter(self) -> None:
        item = self._item()
        for _ in range(3):
            mark_receipt_item_failed(item, "일시적인 timeout 오류")
        mark_receipt_item_paused(item, "사용자가 중단했어요. 필요하면 다시 시도해주세요.")
        ok, reason = can_retry_receipt_item(item)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NotificationBridgeTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_base_exposes_shared_notify_bridge(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn('const VALID_DELIVERY = new Set(["toast_only", "toast_and_center", "center_only"]);', body)
        self.assertIn("function notify(message, level = \"success\", options = {})", body)
        self.assertIn("window.SafeToSpendNotify = Object.freeze({", body)
        self.assertIn("notify,", body)
        self.assertIn("pushNotice,", body)
        self.assertIn("window.SafeToSpendNotify = {", body)
        self.assertIn("__pending: pending,", body)
        self.assertIn("const queuedBridgeCalls =", body)

    def test_notify_supports_toast_and_center_modes(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn("const shouldPersist = delivery === \"toast_and_center\" || delivery === \"center_only\";", body)
        self.assertIn("const shouldShowToast = !suppressToast && delivery !== \"center_only\";", body)
        self.assertIn("persist_to_center", body)
        self.assertIn("NOTICE_RECENT_DEDUPE_WINDOW_MS", body)
        self.assertIn("TOAST_RECENT_DEDUPE_WINDOW_MS", body)
        self.assertIn("if (fn === \"notify\") {", body)
        self.assertIn("notify(...args);", body)

    def test_nhis_toast_calls_bridge_for_persisted_messages(self) -> None:
        body = self._read("templates/nhis.html")
        self.assertIn("const notifyBridge = (window.SafeToSpendNotify && typeof window.SafeToSpendNotify.notify === \"function\")", body)
        self.assertIn("persist_to_center: true", body)
        self.assertIn("title: \"건보 입력 저장 완료\"", body)
        self.assertIn("title: \"건보 입력 확인 필요\"", body)
        self.assertIn("source: \"nhis_form_save\"", body)

    def test_tax_and_review_toasts_are_center_bridged(self) -> None:
        tax_body = self._read("templates/calendar/tax_buffer.html")
        review_body = self._read("templates/calendar/review.html")
        self.assertIn("window.SafeToSpendNotify", tax_body)
        self.assertIn("delivery: \"center_only\"", tax_body)
        self.assertIn("source: \"tax_buffer\"", tax_body)
        self.assertIn("window.SafeToSpendNotify", review_body)
        self.assertIn("delivery: \"center_only\"", review_body)
        self.assertIn("source: \"review_receipt_apply\"", review_body)

    def test_flash_messages_are_bridged_into_center(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn("function bridgeFlashMessagesToCenter()", body)
        self.assertIn("document.querySelectorAll(\".flash-wrap .flash\")", body)
        self.assertIn("delivery: \"center_only\"", body)
        self.assertIn("source: \"flash_message\"", body)


if __name__ == "__main__":
    unittest.main()

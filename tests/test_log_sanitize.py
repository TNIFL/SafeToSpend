from __future__ import annotations

import unittest

from core.log_sanitize import sanitize_log_text


class LogSanitizeTest(unittest.TestCase):
    def test_masks_sensitive_query_values(self) -> None:
        text = "GET /dashboard/billing/register/success?authKey=abc123&paymentKey=pay_456 HTTP/1.1"
        masked = sanitize_log_text(text)
        self.assertIn("authKey=***", masked)
        self.assertIn("paymentKey=***", masked)
        self.assertNotIn("abc123", masked)
        self.assertNotIn("pay_456", masked)


if __name__ == "__main__":
    unittest.main()

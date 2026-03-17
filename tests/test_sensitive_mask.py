import unittest

from services.sensitive_mask import mask_sensitive_numbers


class SensitiveMaskTests(unittest.TestCase):
    def test_masks_long_account_like_sequence_with_hint(self) -> None:
        src = "출금계좌 123-4567-890123 확인"
        out = mask_sensitive_numbers(src)
        self.assertNotIn("890123", out)
        self.assertIn("****0123", out)

    def test_masks_very_long_digits_without_hint(self) -> None:
        src = "입력값: 1234567890123456"
        out = mask_sensitive_numbers(src)
        self.assertNotIn("1234567890123456", out)
        self.assertIn("****3456", out)

    def test_keeps_phone_like_number(self) -> None:
        src = "문의 010-1234-5678"
        out = mask_sensitive_numbers(src)
        self.assertEqual(src, out)


if __name__ == "__main__":
    unittest.main()

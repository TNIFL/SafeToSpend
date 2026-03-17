from __future__ import annotations

import unittest

from services.evidence_vault import _contains_sensitive_filename
from services.tax_package import _is_sensitive_attachment_name


class SensitiveDataGuardTest(unittest.TestCase):
    def test_upload_filename_blocklist(self) -> None:
        self.assertTrue(_contains_sensitive_filename("주민등록증_앞면.jpg"))
        self.assertTrue(_contains_sensitive_filename("idcard_front.png"))
        self.assertFalse(_contains_sensitive_filename("receipt_2026_03.pdf"))

    def test_package_attachment_filter(self) -> None:
        self.assertTrue(_is_sensitive_attachment_name("familyregister.pdf"))
        self.assertFalse(_is_sensitive_attachment_name("영수증_커피.pdf"))


if __name__ == "__main__":
    unittest.main()

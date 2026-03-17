from __future__ import annotations

import unittest

from services.official_refs.source_policy import is_official_url


class SourcePolicyTest(unittest.TestCase):
    def test_official_domains_allowed(self) -> None:
        self.assertTrue(is_official_url("https://www.law.go.kr/"))
        self.assertTrue(is_official_url("https://www.nhis.or.kr/"))
        self.assertTrue(is_official_url("https://www.mohw.go.kr/"))
        self.assertTrue(is_official_url("https://www.nts.go.kr/"))

    def test_non_official_domain_blocked(self) -> None:
        self.assertFalse(is_official_url("https://www.realtyprice.kr/"))
        self.assertFalse(is_official_url("https://easylaw.go.kr/"))


if __name__ == "__main__":
    unittest.main()

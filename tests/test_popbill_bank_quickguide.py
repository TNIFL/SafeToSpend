from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.popbill_bank_quickguide import OFFICIAL_BANKS, load_popbill_bank_quickguide


class PopbillBankQuickguideTest(unittest.TestCase):
    def test_load_contains_only_official_banks(self):
        payload = load_popbill_bank_quickguide()
        banks = payload.get("banks") or []
        self.assertEqual(len(banks), len(OFFICIAL_BANKS))
        got_codes = [str(x.get("bank_code")) for x in banks if isinstance(x, dict)]
        expected_codes = [code for code, _ in OFFICIAL_BANKS]
        self.assertEqual(got_codes, expected_codes)
        for row in banks:
            if not isinstance(row, dict):
                continue
            self.assertTrue(str(row.get("service_name") or "").strip())
            self.assertTrue(str(row.get("homepage_url") or "").startswith("http"))
            self.assertTrue(str(row.get("intro_notice") or "").strip())
            corp = row.get("corporate_steps") or []
            personal = row.get("personal_steps") or []
            self.assertTrue(isinstance(corp, list))
            self.assertTrue(isinstance(personal, list))
            self.assertTrue(len(corp) > 0 or len(personal) > 0)

    def test_missing_file_falls_back_without_error(self):
        payload = load_popbill_bank_quickguide(path=Path("/tmp/not_exists_quickguide_zz.json"))
        banks = payload.get("banks") or []
        self.assertEqual(len(banks), len(OFFICIAL_BANKS))

    def test_invalid_json_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{invalid", encoding="utf-8")
            payload = load_popbill_bank_quickguide(path=p)
            banks = payload.get("banks") or []
            self.assertEqual(len(banks), len(OFFICIAL_BANKS))

    def test_special_banks_have_expected_notes(self):
        payload = load_popbill_bank_quickguide()
        by_code = {str(x.get("bank_code")): x for x in (payload.get("banks") or []) if isinstance(x, dict)}
        sc = by_code.get("0023") or {}
        shinhan = by_code.get("0088") or {}
        saemaul = by_code.get("0045") or {}
        kdb = by_code.get("0002") or {}

        self.assertTrue(any("First Biz" in str(s) or "Straight2Bank" in str(s) for s in (sc.get("corporate_steps") or [])))
        self.assertIn("회원가입", str(shinhan.get("extra_note") or ""))
        self.assertTrue(any("영업점" in str(s) for s in (saemaul.get("corporate_steps") or [])))
        self.assertTrue(any("USB 1개" in str(s) for s in (kdb.get("corporate_steps") or [])))
        self.assertTrue(any("USB 2개" in str(s) for s in (kdb.get("corporate_steps") or [])))


if __name__ == "__main__":
    unittest.main()

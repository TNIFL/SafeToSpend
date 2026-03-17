from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.popbill_bank_guides import (
    POPBILL_BANK_CATALOG,
    build_default_snapshot,
    load_popbill_bank_guides,
    normalize_snapshot,
)


class PopbillBankGuidesTest(unittest.TestCase):
    def test_missing_snapshot_falls_back_to_defaults(self):
        payload = load_popbill_bank_guides(snapshot_path=Path("/tmp/not_exists_popbill_guide_zz.json"))
        self.assertIsInstance(payload, dict)
        self.assertEqual(len(payload.get("banks") or []), len(POPBILL_BANK_CATALOG))
        first = (payload.get("banks") or [None])[0]
        self.assertIsInstance(first, dict)
        self.assertTrue(str(first.get("official_doc_url") or "").startswith("https://"))

    def test_normalize_keeps_catalog_even_with_partial_rows(self):
        raw = {
            "updated_at": "2026-03-08T00:00:00Z",
            "official_doc_url": "https://developers.popbill.com/guide/easyfinbank/introduction/regist-bank-account",
            "banks": [
                {
                    "bank_code": "0004",
                    "bank_name": "KB국민",
                    "quick_service_name": "빠른조회",
                    "intro_message": "등록 후 다시 연결",
                    "path_steps": ["로그인", "메뉴 이동", "빠른조회 등록"],
                    "required_fields": ["아이디", "비밀번호"],
                },
                {"bank_code": "9999", "bank_name": "무효은행"},
            ],
        }
        normalized = normalize_snapshot(raw)
        codes = {str(x.get("bank_code")) for x in (normalized.get("banks") or []) if isinstance(x, dict)}
        catalog_codes = {code for code, _name in POPBILL_BANK_CATALOG}
        self.assertTrue("0004" in codes)
        self.assertTrue(catalog_codes.issubset(codes))

    def test_load_with_invalid_json_uses_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{invalid", encoding="utf-8")
            payload = load_popbill_bank_guides(snapshot_path=p)
            self.assertEqual(len(payload.get("banks") or []), len(POPBILL_BANK_CATALOG))
            self.assertTrue(len(payload.get("notes") or []) >= 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataPolicyDocsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_source_guide_has_official_path_purpose_and_easy_names(self) -> None:
        body = self._read("docs/OFFICIAL_DATA_SOURCE_GUIDE.md")
        self.assertIn("공식 사이트", body)
        self.assertIn("공식 메뉴/경로", body)
        self.assertIn("쓸수있어에서 쓰는 목적", body)
        self.assertIn("현금영수증 지출증빙 내역", body)
        self.assertIn("보험료 납부확인서", body)

    def test_storage_and_retention_docs_cover_minimal_collection_and_destruction(self) -> None:
        storage = self._read("docs/OFFICIAL_DATA_STORAGE_POLICY.md")
        retention = self._read("docs/OFFICIAL_DATA_RETENTION_POLICY.md")
        formats = self._read("docs/OFFICIAL_DATA_SUPPORTED_FORMATS.md")
        parser_policy = self._read("docs/OFFICIAL_DATA_PARSER_POLICY.md")
        self.assertIn("최소수집", storage)
        self.assertIn("원본 비저장", storage)
        self.assertIn("보유기간", retention)
        self.assertIn("파기", retention)
        self.assertIn("기준일", retention)
        self.assertIn("화이트리스트", formats)
        self.assertIn("스캔 PDF", formats)
        self.assertIn("fail-closed", parser_policy)
        self.assertIn("supported_document_type", parser_policy)

    def test_consent_and_alignment_docs_cover_purpose_retention_refusal_and_safety(self) -> None:
        consent = self._read("docs/OFFICIAL_DATA_CONSENT_MAP.md")
        privacy = self._read("docs/PRIVACY_POLICY_UPDATE_NOTES.md")
        alignment = self._read("docs/TERMS_AND_PRIVACY_ALIGNMENT.md")
        self.assertIn("공식 자료 업로드/분석", consent)
        self.assertIn("거부 가능 범위", consent)
        self.assertIn("수집·이용 목적", privacy)
        self.assertIn("안전조치", privacy)
        self.assertIn("정합 기준", alignment)
        self.assertIn("영구 자동화", alignment)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataLegalDocsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_legal_boundary_docs_have_allow_forbid_hold_matrix(self) -> None:
        report = self._read("docs/OFFICIAL_DATA_LEGAL_BOUNDARY_REPORT.md")
        matrix = self._read("docs/OFFICIAL_DATA_LEGAL_MATRIX.md")
        self.assertIn("지금 구현 가능한 범위", report)
        self.assertIn("지금 구현 금지 범위", report)
        self.assertIn("유보 범위", report)
        self.assertIn("허용", matrix)
        self.assertIn("금지", matrix)
        self.assertIn("유보", matrix)
        self.assertIn("사용자 업로드 파일 파싱", matrix)
        self.assertIn("주민등록번호 전체 처리", matrix)
        self.assertIn("건강 관련 세부정보 처리", matrix)
        self.assertIn("자동 조회/스크래핑/대리 인증", matrix)

    def test_data_classification_and_storage_docs_cover_forbidden_and_nhis_caution(self) -> None:
        classification = self._read("docs/OFFICIAL_DATA_DATA_CLASSIFICATION.md")
        storage = self._read("docs/OFFICIAL_DATA_STORAGE_AND_DELETION_POLICY.md")
        self.assertIn("문서 메타데이터", classification)
        self.assertIn("핵심 추출값", classification)
        self.assertIn("파생 상태값", classification)
        self.assertIn("업로드 파일 전체", classification)
        self.assertIn("주민등록번호 전체", classification)
        self.assertIn("건강 상세정보", classification)
        self.assertIn("NHIS 별도 주의", classification)
        self.assertIn("기본 비저장", storage)
        self.assertIn("목적 달성 후 지체 없이 파기", storage)
        self.assertIn("preview", storage)

    def test_consent_and_readiness_docs_cover_notice_and_scope(self) -> None:
        consent = self._read("docs/OFFICIAL_DATA_CONSENT_AND_NOTICE_MAP.md")
        readiness = self._read("docs/OFFICIAL_DATA_IMPLEMENTATION_READINESS.md")
        verification = self._read("docs/OFFICIAL_DATA_VERIFICATION_SCOPE.md")
        privacy = self._read("docs/PRIVACY_POLICY_UPDATE_NOTES.md")
        self.assertIn("기준일", consent)
        self.assertIn("신뢰등급", consent)
        self.assertIn("거부 가능 범위", consent)
        self.assertIn("기관 확인 완료 여부에 따라 검증 수준이 다름", consent)
        self.assertIn("이번 단계 구현 대상", readiness)
        self.assertIn("이번 단계 비대상", readiness)
        self.assertIn("후속 대상", readiness)
        self.assertIn("구조 검증과 기관 확인의 차이", verification)
        self.assertIn("정부24", verification)
        self.assertIn("홈택스", verification)
        self.assertIn("NHIS", verification)
        self.assertIn("저장하지 않는 항목", privacy)
        self.assertIn("구조 검증 완료는 기관 진위확인과 동일하지 않음", privacy)


if __name__ == "__main__":
    unittest.main()

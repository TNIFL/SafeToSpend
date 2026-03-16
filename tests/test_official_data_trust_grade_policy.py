from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataTrustGradePolicyTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_trust_grade_policy_defines_abcd_and_banned_terms(self) -> None:
        body = self._read("docs/OFFICIAL_DATA_TRUST_GRADE_POLICY.md")
        self.assertIn("A: 기관 확인 완료", body)
        self.assertIn("B: 공식 양식 구조 검증 완료", body)
        self.assertIn("C: 사용자 업로드 자료 기준", body)
        self.assertIn("D: 사용자 수정 포함 또는 검토 필요", body)
        self.assertIn("금지 표현", body)
        self.assertIn("진본", body)
        self.assertIn("100% 정확", body)
        self.assertIn("법적으로 보증", body)

    def test_trust_grade_policy_limits_hash_and_structure_validation_claims(self) -> None:
        body = self._read("docs/OFFICIAL_DATA_TRUST_GRADE_POLICY.md")
        verification = self._read("docs/OFFICIAL_DATA_VERIFICATION_SCOPE.md")
        self.assertIn("해시는 업로드 이후 무결성 추적 도구", body)
        self.assertIn("구조 검증 완료는 기관 진위확인과 동일하지 않다", body)
        self.assertIn("CSV/XLSX는 편의용/참고용 성격", body)
        self.assertIn("공식 로고, 문서 제목, 양식 일치", verification)
        self.assertIn("기관 확인 완료", verification)
        self.assertIn("사용자 업로드만으로", verification)


if __name__ == "__main__":
    unittest.main()

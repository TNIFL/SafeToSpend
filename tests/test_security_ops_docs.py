from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SecurityOpsDocsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_code_export_procedure_mentions_sensitive_exclusions(self) -> None:
        text = self._read("docs/CODE_EXPORT_PROCEDURE.md")
        self.assertIn("uploads/**", text)
        self.assertIn("reports/**", text)
        self.assertIn(".env.*", text)
        self.assertIn("archive_verified", text)

    def test_operations_security_checklist_covers_four_risks(self) -> None:
        text = self._read("docs/OPERATIONS_SECURITY_CHECKLIST.md")
        self.assertIn("관리자 권한 부여", text)
        self.assertIn("계정 삭제 검증", text)
        self.assertIn("SECRET_KEY 배포 전 확인", text)
        self.assertIn("코드 전달용 압축 생성", text)
        self.assertIn("set-admin-role", text)

    def test_dev_testing_references_explicit_admin_grant_and_export_script(self) -> None:
        text = self._read("docs/DEV_TESTING.md")
        self.assertIn("set-admin-role", text)
        self.assertIn("export_code_bundle.py", text)
        self.assertIn("기본 `SECRET_KEY`는 `APP_ENV=development|dev|local|test` 이고 localhost 전용 실행일 때만 허용됩니다.", text)

    def test_env_example_no_longer_advertises_admin_email_fallback(self) -> None:
        text = self._read(".env.example")
        self.assertNotIn("ADMIN_FIXED_EMAIL", text)
        self.assertNotIn("ADMIN_FIXED_PASSWORD", text)
        self.assertIn("set-admin-role", text)


if __name__ == "__main__":
    unittest.main()

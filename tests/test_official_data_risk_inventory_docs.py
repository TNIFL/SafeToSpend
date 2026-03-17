from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataRiskInventoryDocsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_inventory_docs_exist_and_list_risks(self) -> None:
        inventory = self._read("docs/OFFICIAL_DATA_RISK_INVENTORY.md")
        remediation = self._read("docs/OFFICIAL_DATA_REMEDIATION_PLAN.md")
        guards = self._read("docs/OFFICIAL_DATA_RUNTIME_GUARDS_REPORT.md")
        self.assertIn("위험 인벤토리", inventory)
        self.assertIn("preview", inventory)
        self.assertIn("식별키", inventory)
        self.assertIn("즉시 수정", remediation)
        self.assertIn("NHIS payload 축소", remediation)
        self.assertIn("A등급", guards)
        self.assertIn("금지 표현", guards)

    def test_docs_call_out_immediate_fixes_and_banned_storage(self) -> None:
        inventory = self._read("docs/OFFICIAL_DATA_RISK_INVENTORY.md")
        remediation = self._read("docs/OFFICIAL_DATA_REMEDIATION_PLAN.md")
        guards = self._read("docs/OFFICIAL_DATA_RUNTIME_GUARDS_REPORT.md")
        combined = "\n".join((inventory, remediation, guards))
        self.assertIn("주민등록번호 전체", combined)
        self.assertIn("건강 상세정보", combined)
        self.assertIn("긴 원문 preview", combined)
        self.assertIn("기관 확인 메타 없이는 A등급", combined)


if __name__ == "__main__":
    unittest.main()

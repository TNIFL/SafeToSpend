from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaxNhisUiCopyTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_tax_buffer_has_estimate_and_limited_copy(self) -> None:
        body = self._read("templates/calendar/tax_buffer.html")
        self.assertIn("확정값이 아닌 추정치", body)
        self.assertIn("0원 또는 낮게 보일 수 있어요", body)
        self.assertIn("돈 받을 때 3.3%가 떼이는 경우 반영은 입력값 또는 거래 내역 표현 기준 추정", body)
        self.assertIn("입력 보완 후 계산 가능", body)
        self.assertIn("tax_calc_meta.message", body)

    def test_nhis_template_has_estimate_warning_copy(self) -> None:
        body = self._read("templates/nhis.html")
        self.assertIn("공단 고지액이 아닌 추정치", body)
        self.assertIn("가입유형/소득 반영시점/재산 기준", body)
        self.assertIn("핵심 숫자를 숨겼어요", body)
        self.assertIn("nhis_meta.message", body)

    def test_overview_and_package_keep_estimate_notice(self) -> None:
        overview = self._read("templates/overview.html")
        package = self._read("templates/package/index.html")
        self.assertIn("세금/건보료는 확정값이 아닌 추정치", overview)
        self.assertIn("핵심 입력이 부족해 이번 달 권장액을 강하게 표시하지 않아요", overview)
        self.assertIn("세금 값은 신고 확정세액이 아닌 추정치", package)


if __name__ == "__main__":
    unittest.main()

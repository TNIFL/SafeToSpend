from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaxNhisUiGuardBehaviorTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_overview_blocks_core_numbers_when_guarded(self) -> None:
        body = self._read("templates/overview.html")
        self.assertIn("{% if core_numbers_blocked %}", body)
        self.assertIn("입력 보완 후 계산 가능", body)
        self.assertIn("핵심 입력이 부족해 이번 달 권장액을 강하게 표시하지 않아요.", body)
        self.assertIn("{% if has_recovery_cta %}", body)
        self.assertIn("tax_recovery_cta.show", body)
        self.assertIn("nhis_recovery_cta.show", body)
        self.assertIn("{% else %}", body)
        self.assertIn("{{ \"{:,}\".format(total_setaside_recommended|int) }}원", body)

    def test_tax_buffer_uses_blocked_branch_for_key_tax_numbers(self) -> None:
        body = self._read("templates/calendar/tax_buffer.html")
        self.assertIn("{% if tax_blocked %}", body)
        self.assertIn("소득 유형/총수입/업무지출/원천·기납부 정보를 먼저 입력해 주세요.", body)
        self.assertIn("입력 보완 후 계산", body)
        self.assertIn("tax_recovery_cta and tax_recovery_cta.show", body)
        self.assertIn("자동 초안", body)
        self.assertIn("nhis_recovery_cta and nhis_recovery_cta.show", body)
        self.assertIn("{% if tax_blocked or nhis_blocked %}", body)
        self.assertIn("선택한 달 총 보관 권장액", body)

    def test_nhis_page_hides_kpi_when_blocked_or_not_ready(self) -> None:
        body = self._read("templates/nhis.html")
        self.assertIn("{% if refs_valid and (not nhis_blocked) %}", body)
        self.assertIn("nhis_recovery_cta_ctx and nhis_recovery_cta_ctx.show", body)
        self.assertIn("{% else %}", body)
        self.assertIn("입력이 부족해 핵심 숫자를 숨겼어요.", body)
        self.assertIn("nhis_recovery_cta_ctx.action_label", body)
        self.assertIn("다시 확인", body)

    def test_package_page_keeps_limited_estimate_guard_copy(self) -> None:
        body = self._read("templates/package/index.html")
        self.assertIn("세금 값은 신고 확정세액이 아닌 추정치", body)
        self.assertIn("tax_estimate_meta.get('official_calculable')", body)
        self.assertIn("tax_estimate_meta.get('is_limited_estimate')", body)
        self.assertRegex(
            body,
            re.compile(r"입력 부족으로 제한된 추정", re.MULTILINE),
        )


if __name__ == "__main__":
    unittest.main()

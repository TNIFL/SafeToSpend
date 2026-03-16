from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InputRecoveryBannerPriorityTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_overview_has_top_priority_recovery_banner_for_guarded_users(self) -> None:
        body = self._read("templates/overview.html")
        self.assertIn("overview-recovery-banner", body)
        self.assertIn("{% if has_recovery_cta %}", body)
        self.assertIn("{% if core_numbers_blocked %}", body)
        self.assertIn("핵심 숫자 노출 제한 중", body)
        self.assertIn("정확도 보완 필요", body)

    def test_tax_buffer_recovery_block_appears_before_key_number_copy(self) -> None:
        body = self._read("templates/calendar/tax_buffer.html")
        self.assertIn("tax_recovery_cta and tax_recovery_cta.show", body)
        self.assertIn("{{ tax_recovery_cta.title }}", body)
        self.assertIn("가장 먼저 소득 유형 1문항만 저장하면 계산을 시작할 수 있어요", body)
        self.assertIn("가입유형 먼저 저장", body)
        self.assertLess(
            body.index("tax_recovery_cta and tax_recovery_cta.show"),
            body.index("선택한 달 추가 납부 예상세액(추정)"),
        )

    def test_nhis_template_keeps_recovery_block_and_blocked_hide_message(self) -> None:
        body = self._read("templates/nhis.html")
        self.assertIn("nhis_recovery_cta_ctx and nhis_recovery_cta_ctx.show", body)
        self.assertIn("가입유형 먼저 저장", body)
        self.assertIn("입력이 부족해 핵심 숫자를 숨겼어요.", body)
        self.assertIn("nhis_membership_type_quick_save", body)


if __name__ == "__main__":
    unittest.main()

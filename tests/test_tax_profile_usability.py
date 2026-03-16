from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaxProfileUsabilityTest(unittest.TestCase):
    def test_required_section_is_prioritized_before_optional_section(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        required_idx = body.find("필수 입력 먼저")
        optional_idx = body.find("선택 입력(필요한 경우만)")
        self.assertGreaterEqual(required_idx, 0)
        self.assertGreaterEqual(optional_idx, 0)
        self.assertLess(required_idx, optional_idx)

    def test_quick_zero_buttons_exist_for_required_tax_fields(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn('data-fill-zero-target="withheld_tax_annual_krw"', body)
        self.assertIn('data-fill-zero-target="prepaid_tax_annual_krw"', body)
        self.assertIn('data-fill-stepwise-zero="1"', body)

    def test_focus_and_inline_saved_script_hooks_exist(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn('const focusField = String(qs.get("focus") || "").trim();', body)
        self.assertIn('const inlineSaved = String(qs.get("inline_saved") || "").trim();', body)
        self.assertIn('document.getElementById("tax-stepwise-card")', body)


if __name__ == "__main__":
    unittest.main()

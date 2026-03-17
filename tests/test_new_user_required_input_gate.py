from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NewUserRequiredInputGateTest(unittest.TestCase):
    def test_onboarding_redirects_to_overview_result_first(self) -> None:
        body = (ROOT / "routes/web/auth.py").read_text(encoding="utf-8")
        self.assertIn('return redirect(url_for("web_overview.overview"))', body)
        self.assertIn("지금 상태 기준 결과를 먼저 보여드릴게요", body)

    def test_onboarding_template_mentions_result_first_phase(self) -> None:
        body = (ROOT / "templates/onboarding.html").read_text(encoding="utf-8")
        self.assertIn("결과부터 보여드릴게요", body)
        self.assertIn("저장하고 결과 보기", body)

    def test_nhis_profile_service_blocks_missing_required_inputs(self) -> None:
        body = (ROOT / "services/nhis_profile.py").read_text(encoding="utf-8")
        self.assertIn("직장가입자 99% 필수 입력이 부족해요", body)
        self.assertIn("지역가입자 99% 필수 입력이 부족해요", body)


if __name__ == "__main__":
    unittest.main()

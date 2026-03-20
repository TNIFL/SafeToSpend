from __future__ import annotations

import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import SafeToSpendSettings, User


class NhisRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"nhis-user-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

    def tearDown(self) -> None:
        with self.app.app_context():
            SafeToSpendSettings.query.filter_by(user_pk=self.user_pk).delete(synchronize_session=False)
            User.query.filter(User.id == self.user_pk).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def test_nhis_page_requires_login(self) -> None:
        response = self.client.get("/dashboard/nhis", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/dashboard/nhis", response.headers["Location"])

    def test_nhis_page_renders_guide_only_ctas(self) -> None:
        self._login()

        response = self.client.get("/dashboard/nhis")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("건보료 안내", body)
        self.assertIn("현재는 안내 중심 화면입니다.", body)
        self.assertIn("공식자료 업로드", body)
        self.assertIn("참고자료 업로드", body)
        self.assertIn("정리하기", body)
        self.assertIn("세무사 패키지", body)
        self.assertIn("세금 보관함", body)

    def test_nhis_page_uses_onboarding_health_insurance_copy(self) -> None:
        with self.app.app_context():
            db.session.add(
                SafeToSpendSettings(
                    user_pk=self.user_pk,
                    default_tax_rate=0.15,
                    custom_rates={"_meta": {"insurance_type": "local", "employment_type": "freelancer"}},
                )
            )
            db.session.commit()

        self._login()
        body = self.client.get("/dashboard/nhis").get_data(as_text=True)

        self.assertIn("입력하신 정보 기준으로는 지역가입자 쪽 자료를 먼저 챙기는 편이 좋습니다.", body)
        self.assertIn("건강보험 납부확인서와 자격 관련 문서를 공식자료 채널에 먼저 모아 두세요.", body)
        self.assertIn("프리랜서/사업자 흐름이 있으면 홈택스 납부내역과 같이 준비하는 편이 실용적입니다.", body)

    def test_nhis_page_does_not_claim_unavailable_capabilities(self) -> None:
        self._login()

        body = self.client.get("/dashboard/nhis").get_data(as_text=True)

        self.assertNotIn("정확히 계산", body)
        self.assertNotIn("자동 확정", body)
        self.assertNotIn("공식 확인 완료", body)
        self.assertNotIn("대사 리포트", body)
        self.assertNotIn("세금 설정", body)


if __name__ == "__main__":
    unittest.main()

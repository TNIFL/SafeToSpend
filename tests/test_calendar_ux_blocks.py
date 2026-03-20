from __future__ import annotations

import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import SafeToSpendSettings, TaxBufferLedger, User


class CalendarUxBlocksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"calendar-ux-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

    def tearDown(self) -> None:
        with self.app.app_context():
            TaxBufferLedger.query.filter(TaxBufferLedger.user_pk == self.user_pk).delete(synchronize_session=False)
            SafeToSpendSettings.query.filter(SafeToSpendSettings.user_pk == self.user_pk).delete(synchronize_session=False)
            User.query.filter(User.id == self.user_pk).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def test_review_page_renders_guidance_blocks_and_safe_ctas(self) -> None:
        self._login()

        response = self.client.get("/dashboard/review?month=2026-03")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("이번 달 정리 순서", body)
        self.assertIn("자료 보강 경로", body)
        self.assertIn("공식자료 업로드", body)
        self.assertIn("참고자료 업로드", body)
        self.assertIn("건보료 안내", body)
        self.assertIn("세무사 패키지", body)
        self.assertIn("대사 리포트", body)
        self.assertNotIn("세금 설정", body)

    def test_review_page_uses_onboarding_guidance_copy(self) -> None:
        with self.app.app_context():
            db.session.add(
                SafeToSpendSettings(
                    user_pk=self.user_pk,
                    default_tax_rate=0.15,
                    custom_rates={
                        "_meta": {
                            "onboarding_user_type": "freelancer_33",
                            "onboarding_vat_status": "vat",
                            "onboarding_health_insurance": "local",
                        }
                    },
                )
            )
            db.session.commit()

        self._login()
        body = self.client.get("/dashboard/review?month=2026-03").get_data(as_text=True)

        self.assertIn("입력하신 정보 기준으로는 프리랜서 자료를 먼저 맞추는 편이 좋습니다.", body)
        self.assertIn("원천징수 관련 문서와 홈택스 납부내역을 먼저 챙기면 정리 결과를 세무 자료와 연결하기 쉽습니다.", body)
        self.assertIn("과세사업자/부가세 대상이면 이번 달 지출 정리 후 공식자료에서 납부내역을 먼저 확인해 두는 편이 안전합니다.", body)
        self.assertIn("지역가입자 기준이라면 건보 납부확인서와 자격 관련 문서를 같이 준비해 두세요.", body)

    def test_tax_buffer_page_renders_guidance_blocks_and_safe_ctas(self) -> None:
        self._login()

        response = self.client.get("/dashboard/tax-buffer?month=2026-03")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("이 숫자를 이렇게 보세요", body)
        self.assertIn("수치를 보강하는 방법", body)
        self.assertIn("공식자료 업로드", body)
        self.assertIn("참고자료 업로드", body)
        self.assertIn("건보료 안내", body)
        self.assertIn("세무사 패키지", body)
        self.assertIn("대사 리포트", body)
        self.assertNotIn("세금 설정", body)
        self.assertNotIn("정밀 계산", body)

    def test_tax_buffer_page_uses_onboarding_guidance_copy(self) -> None:
        with self.app.app_context():
            db.session.add(
                SafeToSpendSettings(
                    user_pk=self.user_pk,
                    default_tax_rate=0.15,
                    custom_rates={
                        "_meta": {
                            "onboarding_user_type": "employee_sidejob",
                            "onboarding_health_insurance": "employee",
                            "onboarding_vat_status": "non_vat",
                        }
                    },
                )
            )
            db.session.commit()

        self._login()
        body = self.client.get("/dashboard/tax-buffer?month=2026-03").get_data(as_text=True)

        self.assertIn("입력하신 정보 기준으로는 본업과 부업을 섞지 않고 보는 편이 좋습니다.", body)
        self.assertIn("직장인 + 부업이면 세금 보관함 숫자를 확정값처럼 보기보다, 본업/부업 자료를 나눠 확인해 주세요.", body)
        self.assertIn("직장가입자 기준이라면 건보 자료는 예외 상황 확인용으로만 보수적으로 참고해 주세요.", body)


if __name__ == "__main__":
    unittest.main()

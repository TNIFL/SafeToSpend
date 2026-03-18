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
        self.assertNotIn("세금 설정", body)
        self.assertNotIn("대사 리포트", body)

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
        self.assertNotIn("세금 설정", body)
        self.assertNotIn("정밀 계산", body)


if __name__ == "__main__":
    unittest.main()

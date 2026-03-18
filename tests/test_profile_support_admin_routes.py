from __future__ import annotations

import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import User, UserDashboardState


class ProfileSupportAdminRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"profile-user-{uuid4().hex}@example.com")
            user.set_password("test-password")
            admin = User(email=f"admin-user-{uuid4().hex}@example.com")
            admin.set_password("test-password")
            db.session.add(user)
            db.session.add(admin)
            db.session.commit()
            self.user_pk = int(user.id)
            self.user_email = user.email
            self.admin_pk = int(admin.id)
            self.admin_email = admin.email

    def tearDown(self) -> None:
        with self.app.app_context():
            UserDashboardState.query.filter(
                UserDashboardState.user_pk.in_([self.user_pk, self.admin_pk])
            ).delete(synchronize_session=False)
            User.query.filter(User.id.in_([self.user_pk, self.admin_pk])).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self, user_pk: int) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = user_pk

    def test_mypage_requires_login(self) -> None:
        response = self.client.get("/mypage", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/mypage", response.headers["Location"])

    def test_mypage_renders_basic_account_summary(self) -> None:
        self._login(self.user_pk)

        response = self.client.get("/mypage")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("내 계정", body)
        self.assertIn(self.user_email, body)
        self.assertIn("공식자료", body)
        self.assertIn("참고자료", body)
        self.assertIn("구독 준비 중", body)

    def test_support_page_renders_and_accepts_non_persistent_submission(self) -> None:
        self._login(self.user_pk)

        response = self.client.post(
            "/support",
            data={
                "subject": "문의 테스트",
                "message": "현재 저장되지 않는 안내용 문의입니다.",
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("문의 저장 기능은 아직 연결되지 않았습니다.", body)
        self.assertIn("입력한 문의 초안", body)
        self.assertIn("문의 테스트", body)

    def test_admin_requires_explicit_admin_email_allowlist(self) -> None:
        self._login(self.user_pk)

        response = self.client.get("/admin")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 403)
        self.assertIn("관리자 이메일로 등록된 계정만 접근할 수 있어요.", body)

    def test_admin_page_renders_for_configured_admin(self) -> None:
        self.app.config["ADMIN_EMAILS"] = self.admin_email
        self._login(self.admin_pk)

        response = self.client.get("/admin")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("관리자 기본 화면", body)
        self.assertIn("가입 사용자", body)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import SafeToSpendSettings, User, UserDashboardState


class NavigationUxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"nav-user-{uuid4().hex}@example.com")
            user.set_password("test-password")
            admin = User(email=f"nav-admin-{uuid4().hex}@example.com")
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
            SafeToSpendSettings.query.filter(
                SafeToSpendSettings.user_pk.in_([self.user_pk, self.admin_pk])
            ).delete(synchronize_session=False)
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

    def test_landing_shows_current_product_ctas_without_missing_menu_labels(self) -> None:
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("플랜 안내", body)
        self.assertIn("지금 바로 찾을 수 있는 기능", body)
        self.assertNotIn("알림", body)
        self.assertNotIn("대사 리포트", body)
        self.assertNotIn("세금 설정", body)

    def test_logged_in_nav_shows_existing_routes_only(self) -> None:
        self._login(self.user_pk)

        response = self.client.get("/mypage")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("요약", body)
        self.assertIn("정리하기", body)
        self.assertIn("처리함", body)
        self.assertIn("패키지", body)
        self.assertIn("공식자료 업로드", body)
        self.assertIn("참고자료", body)
        self.assertIn("증빙 자료 보관함", body)
        self.assertIn("구독 안내", body)
        self.assertIn("내 계정", body)
        self.assertIn("문의", body)
        self.assertNotIn("알림", body)
        self.assertNotIn("대사 리포트", body)
        self.assertNotIn("세금 설정", body)
        self.assertNotIn("관리자</a>", body)

    def test_overview_and_dashboard_surface_recovered_discovery_ctas(self) -> None:
        self._login(self.user_pk)

        overview_response = self.client.get("/overview")
        overview_body = overview_response.get_data(as_text=True)
        dashboard_response = self.client.get("/dashboard/")
        dashboard_body = dashboard_response.get_data(as_text=True)

        self.assertEqual(overview_response.status_code, 200)
        self.assertIn("빠른 이동", overview_body)
        self.assertIn("세무사 패키지 보기", overview_body)
        self.assertIn("공식자료 업로드", overview_body)
        self.assertIn("참고자료 업로드", overview_body)
        self.assertIn("문의 안내", overview_body)

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn("지금 바로 열 수 있는 기능", dashboard_body)
        self.assertIn("공식자료", dashboard_body)
        self.assertIn("참고자료", dashboard_body)
        self.assertIn("내 계정", dashboard_body)

    def test_package_page_surfaces_account_and_support_links(self) -> None:
        self._login(self.user_pk)

        response = self.client.get("/dashboard/package")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("함께 쓰는 화면", body)
        self.assertIn("구독 안내", body)
        self.assertIn("내 계정", body)
        self.assertIn("문의 안내", body)

    def test_admin_nav_link_is_only_visible_for_allowed_admin(self) -> None:
        self._login(self.user_pk)
        normal_body = self.client.get("/mypage").get_data(as_text=True)
        self.assertNotIn("관리자</a>", normal_body)

        self.app.config["ADMIN_EMAILS"] = self.admin_email
        self._login(self.admin_pk)
        admin_body = self.client.get("/mypage").get_data(as_text=True)
        self.assertIn("관리자", admin_body)


if __name__ == "__main__":
    unittest.main()

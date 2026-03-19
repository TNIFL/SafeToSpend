from __future__ import annotations

import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import SafeToSpendSettings, User


class AuthRegisterRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.created_user_ids: list[int] = []

    def tearDown(self) -> None:
        with self.app.app_context():
            if self.created_user_ids:
                SafeToSpendSettings.query.filter(
                    SafeToSpendSettings.user_pk.in_(self.created_user_ids)
                ).delete(synchronize_session=False)
                User.query.filter(User.id.in_(self.created_user_ids)).delete(synchronize_session=False)
                db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _register(self, **overrides):
        email = overrides.pop("email", f"register-{uuid4().hex}@example.com")
        data = {
            "email": email,
            "password": "test-password",
            "password2": "test-password",
            **overrides,
        }
        response = self.client.post("/register", data=data, follow_redirects=True)
        return email, response

    def test_register_page_renders_required_consents_and_links(self) -> None:
        response = self.client.get("/register")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("[필수] 이용약관에 동의합니다", body)
        self.assertIn("[필수] 개인정보처리방침에 동의합니다", body)
        self.assertIn("/legal/terms", body)
        self.assertIn("/legal/privacy", body)

    def test_legal_document_routes_render_draft_pages(self) -> None:
        terms_response = self.client.get("/legal/terms")
        privacy_response = self.client.get("/legal/privacy")

        self.assertEqual(terms_response.status_code, 200)
        self.assertIn("현재 기준 문서", terms_response.get_data(as_text=True))
        self.assertIn("이용약관 초안", terms_response.get_data(as_text=True))

        self.assertEqual(privacy_response.status_code, 200)
        self.assertIn("현재 기준 문서", privacy_response.get_data(as_text=True))
        self.assertIn("개인정보처리방침 초안", privacy_response.get_data(as_text=True))

    def test_register_rejects_submission_without_required_consents(self) -> None:
        email, response = self._register()
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("이용약관과 개인정보처리방침에 동의해야 가입할 수 있습니다.", body)

        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(email=email).first())

    def test_register_succeeds_when_both_required_consents_are_checked(self) -> None:
        email, response = self._register(agree_terms="on", agree_privacy="on")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("가입이 완료되었습니다. 로그인해 주세요.", body)

        with self.app.app_context():
            user = User.query.filter_by(email=email).first()
            self.assertIsNotNone(user)
            self.created_user_ids.append(int(user.id))
            settings = SafeToSpendSettings.query.filter_by(user_pk=int(user.id)).first()
            self.assertIsNotNone(settings)


if __name__ == "__main__":
    unittest.main()

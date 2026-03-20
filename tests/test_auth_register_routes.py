from __future__ import annotations

import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import SafeToSpendSettings, User, UserConsentAgreement
from services.auth import register_user
from services.legal_documents import PRIVACY_POLICY, PRIVACY_VERSION, TERMS_OF_SERVICE, TERMS_VERSION


class AuthRegisterRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.created_user_ids: list[int] = []

    def tearDown(self) -> None:
        with self.app.app_context():
            if self.created_user_ids:
                UserConsentAgreement.query.filter(
                    UserConsentAgreement.user_pk.in_(self.created_user_ids)
                ).delete(synchronize_session=False)
                SafeToSpendSettings.query.filter(
                    SafeToSpendSettings.user_pk.in_(self.created_user_ids)
                ).delete(synchronize_session=False)
                User.query.filter(User.id.in_(self.created_user_ids)).delete(synchronize_session=False)
                db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _register(self, **overrides):
        email = overrides.pop("email", f"register-{uuid4().hex}@example.com")
        follow_redirects = overrides.pop("follow_redirects", True)
        data = {
            "email": email,
            "password": "test-password",
            "password2": "test-password",
            **overrides,
        }
        response = self.client.post("/register", data=data, follow_redirects=follow_redirects)
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
            self.assertEqual(UserConsentAgreement.query.count(), 0)

    def test_register_succeeds_and_redirects_new_user_to_onboarding(self) -> None:
        email, response = self._register(agree_terms="on", agree_privacy="on")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("가입이 완료되었습니다. 시작 전에 추천 설정만 가볍게 맞춰볼게요.", body)
        self.assertIn("딱 세 가지만 알려주시면", body)

        with self.app.app_context():
            user = User.query.filter_by(email=email).first()
            self.assertIsNotNone(user)
            self.created_user_ids.append(int(user.id))
            settings = SafeToSpendSettings.query.filter_by(user_pk=int(user.id)).first()
            self.assertIsNotNone(settings)
            agreements = (
                UserConsentAgreement.query.filter_by(user_pk=int(user.id))
                .order_by(UserConsentAgreement.document_type.asc())
                .all()
            )
            self.assertEqual(len(agreements), 2)
            self.assertEqual(
                {(row.document_type, row.document_version) for row in agreements},
                {
                    (TERMS_OF_SERVICE, TERMS_VERSION),
                    (PRIVACY_POLICY, PRIVACY_VERSION),
                },
            )
            self.assertTrue(all(row.agreed_at is not None for row in agreements))

        with self.client.session_transaction() as sess:
            self.assertIsNotNone(sess.get("user_id"))

    def test_onboarding_save_persists_profile_meta_and_redirects_to_overview(self) -> None:
        email, response = self._register(agree_terms="on", agree_privacy="on", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/getting-started", response.headers["Location"])

        with self.app.app_context():
            user = User.query.filter_by(email=email).first()
            self.assertIsNotNone(user)
            self.created_user_ids.append(int(user.id))

        save_response = self.client.post(
            "/getting-started",
            data={
                "action": "save",
                "user_type": "freelancer_33",
                "health_insurance": "local",
                "vat_status": "vat",
            },
            follow_redirects=False,
        )

        self.assertEqual(save_response.status_code, 302)
        self.assertTrue(save_response.headers["Location"].endswith("/overview"))

        with self.app.app_context():
            user = User.query.filter_by(email=email).first()
            settings = SafeToSpendSettings.query.filter_by(user_pk=int(user.id)).first()
            self.assertIsNotNone(settings)
            meta = settings.custom_rates.get("_meta", {})
            self.assertEqual(meta.get("onboarding_user_type"), "freelancer_33")
            self.assertEqual(meta.get("employment_type"), "freelancer")
            self.assertEqual(meta.get("onboarding_health_insurance"), "local")
            self.assertEqual(meta.get("insurance_type"), "local")
            self.assertEqual(meta.get("onboarding_vat_status"), "vat")
            self.assertTrue(meta.get("vat_registered"))
            self.assertIsNotNone(meta.get("onboarding_completed_at"))

    def test_onboarding_can_be_skipped(self) -> None:
        email, _ = self._register(agree_terms="on", agree_privacy="on")

        with self.app.app_context():
            user = User.query.filter_by(email=email).first()
            self.assertIsNotNone(user)
            self.created_user_ids.append(int(user.id))

        response = self.client.post(
            "/getting-started",
            data={"action": "skip"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/overview"))

        with self.app.app_context():
            user = User.query.filter_by(email=email).first()
            settings = SafeToSpendSettings.query.filter_by(user_pk=int(user.id)).first()
            self.assertIsNotNone(settings)
            meta = settings.custom_rates.get("_meta", {})
            self.assertIsNotNone(meta.get("onboarding_skipped_at"))
            self.assertIsNone(meta.get("onboarding_completed_at"))

    def test_login_flow_still_redirects_existing_user_to_overview(self) -> None:
        email = f"login-{uuid4().hex}@example.com"
        with self.app.app_context():
            ok, _, user_id = register_user(email, "test-password")
            self.assertTrue(ok)
            self.assertIsNotNone(user_id)
            self.created_user_ids.append(int(user_id))

        response = self.client.post(
            "/login",
            data={"identifier": email, "password": "test-password"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/overview"))


if __name__ == "__main__":
    unittest.main()

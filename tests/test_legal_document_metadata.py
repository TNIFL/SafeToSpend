from __future__ import annotations

import unittest

from sqlalchemy.exc import IntegrityError

from app import create_app
from core.extensions import db
from domain.models import LegalDocumentMetadata
from services.legal_documents import (
    PRIVACY_POLICY,
    PRIVACY_VERSION,
    TERMS_OF_SERVICE,
    TERMS_VERSION,
    get_active_legal_document,
    required_signup_consents,
)


class LegalDocumentMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.created_metadata_ids: list[int] = []

    def tearDown(self) -> None:
        with self.app.app_context():
            if self.created_metadata_ids:
                LegalDocumentMetadata.query.filter(
                    LegalDocumentMetadata.id.in_(self.created_metadata_ids)
                ).delete(synchronize_session=False)
                db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def test_active_legal_documents_exist_for_required_signup_consents(self) -> None:
        with self.app.app_context():
            terms = get_active_legal_document(TERMS_OF_SERVICE)
            privacy = get_active_legal_document(PRIVACY_POLICY)

            self.assertIsNotNone(terms)
            self.assertEqual(terms.version, TERMS_VERSION)
            self.assertEqual(terms.status, "active")
            self.assertFalse(terms.requires_reconsent)

            self.assertIsNotNone(privacy)
            self.assertEqual(privacy.version, PRIVACY_VERSION)
            self.assertEqual(privacy.status, "active")
            self.assertFalse(privacy.requires_reconsent)

    def test_required_signup_consents_follow_active_document_versions(self) -> None:
        with self.app.app_context():
            self.assertEqual(
                {(item.document_type, item.document_version) for item in required_signup_consents()},
                {
                    (TERMS_OF_SERVICE, TERMS_VERSION),
                    (PRIVACY_POLICY, PRIVACY_VERSION),
                },
            )

    def test_only_one_active_document_is_allowed_per_document_type(self) -> None:
        with self.app.app_context():
            db.session.add(
                LegalDocumentMetadata(
                    document_type=TERMS_OF_SERVICE,
                    version="2026-04-v1",
                    display_name="이용약관 개정본",
                    status="active",
                    effective_at=db.func.now(),
                    requires_reconsent=True,
                    summary="중대한 변경 예시",
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_archived_document_can_coexist_without_changing_active_lookup(self) -> None:
        with self.app.app_context():
            row = LegalDocumentMetadata(
                document_type=PRIVACY_POLICY,
                version="2026-02-archive-1",
                display_name="개정 전 개인정보처리방침",
                status="archived",
                effective_at=db.func.now(),
                requires_reconsent=False,
                summary="이전 버전 보관",
            )
            db.session.add(row)
            db.session.commit()
            self.created_metadata_ids.append(int(row.id))

            privacy = get_active_legal_document(PRIVACY_POLICY)
            self.assertIsNotNone(privacy)
            self.assertEqual(privacy.version, PRIVACY_VERSION)


if __name__ == "__main__":
    unittest.main()

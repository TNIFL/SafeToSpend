from __future__ import annotations

import unittest
from datetime import datetime
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import (
    EvidenceItem,
    ExpenseLabel,
    OfficialDataDocument,
    ReferenceMaterialItem,
    Transaction,
    User,
)


class ReconcileRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"reconcile-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

            tx_in = Transaction(
                user_pk=self.user_pk,
                occurred_at=datetime(2026, 3, 10, 9, 0, 0),
                direction="in",
                amount_krw=1500000,
                counterparty="프로젝트A",
                memo="입금",
                source="csv",
                external_hash=f"reconcile-in-{uuid4().hex}",
            )
            tx_out_missing = Transaction(
                user_pk=self.user_pk,
                occurred_at=datetime(2026, 3, 11, 13, 0, 0),
                direction="out",
                amount_krw=250000,
                counterparty="공급사B",
                memo="외주비",
                source="csv",
                external_hash=f"reconcile-out-missing-{uuid4().hex}",
            )
            tx_out_attached = Transaction(
                user_pk=self.user_pk,
                occurred_at=datetime(2026, 3, 12, 14, 0, 0),
                direction="out",
                amount_krw=180000,
                counterparty="도구구독",
                memo="서비스 이용료",
                source="csv",
                external_hash=f"reconcile-out-attached-{uuid4().hex}",
            )
            db.session.add_all([tx_in, tx_out_missing, tx_out_attached])
            db.session.commit()

            db.session.add(
                ExpenseLabel(
                    user_pk=self.user_pk,
                    transaction_id=tx_out_missing.id,
                    status="business",
                    confidence=100,
                    labeled_by="user",
                )
            )
            db.session.add(
                ExpenseLabel(
                    user_pk=self.user_pk,
                    transaction_id=tx_out_attached.id,
                    status="business",
                    confidence=100,
                    labeled_by="user",
                )
            )
            db.session.add(
                EvidenceItem(
                    user_pk=self.user_pk,
                    transaction_id=tx_out_attached.id,
                    requirement="required",
                    status="attached",
                    original_filename="receipt.pdf",
                    file_key="evidence/receipt.pdf",
                    mime_type="application/pdf",
                    size_bytes=128,
                    sha256="a" * 64,
                )
            )
            db.session.add(
                OfficialDataDocument(
                    user_pk=self.user_pk,
                    document_type="nhis_payment_confirmation",
                    source_authority="건보공단",
                    raw_file_key="official/nhis.pdf",
                    original_filename="nhis.pdf",
                    mime_type="application/pdf",
                    size_bytes=256,
                    sha256="b" * 64,
                    parse_status="parsed",
                )
            )
            db.session.add(
                ReferenceMaterialItem(
                    user_pk=self.user_pk,
                    material_kind="reference",
                    raw_file_key="reference/note.txt",
                    original_filename="note.txt",
                    mime_type="text/plain",
                    size_bytes=64,
                    sha256="c" * 64,
                    title="보충 메모",
                )
            )
            db.session.commit()

    def tearDown(self) -> None:
        with self.app.app_context():
            EvidenceItem.query.filter(EvidenceItem.user_pk == self.user_pk).delete(synchronize_session=False)
            ExpenseLabel.query.filter(ExpenseLabel.user_pk == self.user_pk).delete(synchronize_session=False)
            OfficialDataDocument.query.filter(OfficialDataDocument.user_pk == self.user_pk).delete(
                synchronize_session=False
            )
            ReferenceMaterialItem.query.filter(ReferenceMaterialItem.user_pk == self.user_pk).delete(
                synchronize_session=False
            )
            Transaction.query.filter(Transaction.user_pk == self.user_pk).delete(synchronize_session=False)
            User.query.filter(User.id == self.user_pk).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def test_reconcile_page_requires_login(self) -> None:
        response = self.client.get("/dashboard/reconcile?month=2026-03", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/dashboard/reconcile?month%3D2026-03", response.headers["Location"])

    def test_reconcile_page_renders_summary_blocks_and_safe_ctas(self) -> None:
        self._login()

        response = self.client.get("/dashboard/reconcile?month=2026-03")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("대사 리포트", body)
        self.assertIn("이번 달 거래", body)
        self.assertIn("지금 해야 할 일", body)
        self.assertIn("자료 채널 준비 상태", body)
        self.assertIn("정리하기", body)
        self.assertIn("세금 보관함", body)
        self.assertIn("공식자료 업로드", body)
        self.assertIn("참고자료 업로드", body)
        self.assertIn("세무사 패키지", body)
        self.assertIn("건보료 안내", body)

    def test_reconcile_page_does_not_claim_unavailable_capabilities(self) -> None:
        self._login()

        body = self.client.get("/dashboard/reconcile?month=2026-03").get_data(as_text=True)

        self.assertNotIn("대사 완료", body)
        self.assertNotIn("자동 검증 완료", body)
        self.assertNotIn("모두 확인됨", body)
        self.assertNotIn("정확히 맞음", body)
        self.assertNotIn("세금 설정", body)


if __name__ == "__main__":
    unittest.main()

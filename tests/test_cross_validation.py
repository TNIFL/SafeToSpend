from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import OfficialDataDocument, ReferenceMaterialItem, Transaction, User
from services.cross_validation import (
    build_cross_validation_context,
    build_official_document_cross_validation,
    normalize_validation_amount,
    normalize_validation_date,
    normalize_validation_text,
)


class CrossValidationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            OFFICIAL_DATA_UPLOAD_DIR=self.tmpdir.name,
            REFERENCE_MATERIAL_UPLOAD_DIR=self.tmpdir.name,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"cross-validation-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

    def tearDown(self) -> None:
        with self.app.app_context():
            ReferenceMaterialItem.query.filter_by(user_pk=self.user_pk).delete()
            OfficialDataDocument.query.filter_by(user_pk=self.user_pk).delete()
            Transaction.query.filter_by(user_pk=self.user_pk).delete()
            user = User.query.filter_by(id=self.user_pk).first()
            if user:
                db.session.delete(user)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def _official_doc(
        self,
        *,
        document_type: str,
        source_authority: str,
        reference_date: date,
        parse_status: str = "parsed",
        summary: dict | None = None,
    ) -> OfficialDataDocument:
        doc = OfficialDataDocument(
            user_pk=self.user_pk,
            document_type=document_type,
            source_authority=source_authority,
            raw_file_key=f"u{self.user_pk}/doc-{uuid4().hex}.csv",
            original_filename="sample.csv",
            mime_type="text/csv",
            size_bytes=10,
            sha256="a" * 64,
            reference_date=reference_date,
            parse_status=parse_status,
            verification_status="not_verified",
            structure_validation_status="passed" if parse_status == "parsed" else "needs_review",
            trust_grade="B" if parse_status == "parsed" else "C",
            extracted_key_summary_json=summary or {"display_summary": []},
            parser_version="official-data-v1",
        )
        db.session.add(doc)
        db.session.commit()
        return doc

    def _tx(self, *, occurred_at: datetime, amount_krw: int, counterparty: str, memo: str = "") -> Transaction:
        tx = Transaction(
            user_pk=self.user_pk,
            import_job_id=None,
            occurred_at=occurred_at,
            direction="out",
            amount_krw=amount_krw,
            counterparty=counterparty,
            memo=memo,
            source="manual",
            external_hash=uuid4().hex,
        )
        db.session.add(tx)
        db.session.commit()
        return tx

    def _reference(self, *, kind: str = "reference", title: str, note: str = "", filename: str = "note.txt") -> ReferenceMaterialItem:
        item = ReferenceMaterialItem(
            user_pk=self.user_pk,
            material_kind=kind,
            raw_file_key=f"u{self.user_pk}/ref-{uuid4().hex}.txt",
            original_filename=filename,
            mime_type="text/plain",
            size_bytes=20,
            sha256="b" * 64,
            title=title,
            note=note,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def test_normalization_rules(self) -> None:
        self.assertEqual(normalize_validation_text(" 국세청(홈택스) "), "국세청홈택스")
        self.assertEqual(normalize_validation_amount("150,000원"), 150000)
        self.assertEqual(normalize_validation_date("2026년 3월 10일").isoformat(), "2026-03-10")

    def test_cross_validation_marks_match_when_amount_date_and_authority_match_transaction(self) -> None:
        with self.app.app_context():
            doc = self._official_doc(
                document_type="hometax_tax_payment_history",
                source_authority="국세청(홈택스)",
                reference_date=date(2026, 3, 10),
                summary={
                    "paid_tax_total_krw": 150000,
                    "latest_payment_date": "2026-03-10",
                    "display_summary": [],
                },
            )
            self._tx(
                occurred_at=datetime(2026, 3, 10, 9, 0, 0),
                amount_krw=150000,
                counterparty="국세청 홈택스",
            )

            result = build_official_document_cross_validation(
                document=doc,
                context=build_cross_validation_context(user_pk=self.user_pk),
            )

            self.assertEqual(result["status"], "match")
            self.assertEqual(result["status_label"], "일치")

    def test_cross_validation_marks_partial_match_when_reference_material_supports_same_amount_and_date(self) -> None:
        with self.app.app_context():
            doc = self._official_doc(
                document_type="hometax_tax_payment_history",
                source_authority="국세청(홈택스)",
                reference_date=date(2026, 3, 10),
                summary={
                    "paid_tax_total_krw": 150000,
                    "latest_payment_date": "2026-03-10",
                    "display_summary": [],
                },
            )
            self._reference(title="홈택스 납부내역 2026-03-10 150,000원")

            result = build_official_document_cross_validation(
                document=doc,
                context=build_cross_validation_context(user_pk=self.user_pk),
            )

            self.assertEqual(result["status"], "partial_match")
            self.assertEqual(result["status_label"], "부분일치")

    def test_cross_validation_marks_review_needed_when_comparable_doc_has_no_candidates(self) -> None:
        with self.app.app_context():
            doc = self._official_doc(
                document_type="nhis_payment_confirmation",
                source_authority="국민건강보험공단",
                reference_date=date(2026, 3, 10),
                summary={
                    "latest_paid_amount_krw": 123000,
                    "display_summary": [],
                },
            )

            result = build_official_document_cross_validation(
                document=doc,
                context=build_cross_validation_context(user_pk=self.user_pk),
            )

            self.assertEqual(result["status"], "review_needed")
            self.assertEqual(result["status_label"], "재확인필요")

    def test_cross_validation_marks_mismatch_for_same_authority_transaction_with_different_amount(self) -> None:
        with self.app.app_context():
            doc = self._official_doc(
                document_type="nhis_payment_confirmation",
                source_authority="국민건강보험공단",
                reference_date=date(2026, 3, 10),
                summary={
                    "latest_paid_amount_krw": 123000,
                    "display_summary": [],
                },
            )
            self._tx(
                occurred_at=datetime(2026, 3, 10, 11, 30, 0),
                amount_krw=98000,
                counterparty="국민건강보험공단",
            )

            result = build_official_document_cross_validation(
                document=doc,
                context=build_cross_validation_context(user_pk=self.user_pk),
            )

            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["status_label"], "불일치")

    def test_cross_validation_marks_reference_only_for_non_comparable_document(self) -> None:
        with self.app.app_context():
            doc = self._official_doc(
                document_type="nhis_eligibility_status",
                source_authority="국민건강보험공단",
                reference_date=date(2026, 3, 1),
                summary={"subscriber_type": "직장가입자", "display_summary": []},
            )

            result = build_official_document_cross_validation(
                document=doc,
                context=build_cross_validation_context(user_pk=self.user_pk),
            )

            self.assertEqual(result["status"], "reference_only")
            self.assertEqual(result["status_label"], "참고용")

    def test_official_data_detail_shows_cross_validation_result(self) -> None:
        with self.app.app_context():
            doc = self._official_doc(
                document_type="hometax_tax_payment_history",
                source_authority="국세청(홈택스)",
                reference_date=date(2026, 3, 10),
                summary={
                    "paid_tax_total_krw": 150000,
                    "latest_payment_date": "2026-03-10",
                    "display_summary": [],
                },
            )
            self._tx(
                occurred_at=datetime(2026, 3, 10, 9, 0, 0),
                amount_krw=150000,
                counterparty="국세청 홈택스",
            )
            doc_id = int(doc.id)

        self._login()
        response = self.client.get(f"/dashboard/official-data/{doc_id}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("교차검증 결과", body)
        self.assertIn("일치", body)


if __name__ == "__main__":
    unittest.main()

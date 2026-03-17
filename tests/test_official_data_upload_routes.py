from __future__ import annotations

import io
import tempfile
import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import OfficialDataDocument, User
from services.official_data_store import delete_official_data_file


class OfficialDataUploadRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            OFFICIAL_DATA_UPLOAD_DIR=self.tmpdir.name,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"official-data-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

    def tearDown(self) -> None:
        with self.app.app_context():
            docs = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).all()
            for doc in docs:
                delete_official_data_file(doc.raw_file_key)
                db.session.delete(doc)
            user = User.query.filter_by(id=self.user_pk).first()
            if user:
                db.session.delete(user)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def _upload(self, *, filename: str, content: bytes, follow_redirects: bool = True):
        self._login()
        return self.client.post(
            "/dashboard/official-data/upload",
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
            follow_redirects=follow_redirects,
        )

    def test_upload_supported_hometax_payment_history_marks_parsed(self) -> None:
        payload = (
            "국세청 홈택스,,\n"
            "납부내역 조회 결과,,\n"
            "조회일,2026-03-10,\n"
            "최근 납부일,납부금액 합계,세목명\n"
            "2026.03.09,150000,종합소득세\n"
        ).encode("utf-8")

        response = self._upload(filename="hometax_payment.csv", content=payload)
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("공식자료 처리 결과", body)
        self.assertIn("반영 가능", body)
        self.assertIn("홈택스 납부내역", body)

        with self.app.app_context():
            docs = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).all()
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].parse_status, "parsed")
            self.assertEqual(docs[0].document_type, "hometax_tax_payment_history")
            self.assertEqual(docs[0].trust_grade, "B")

    def test_upload_known_document_with_incomplete_structure_marks_review(self) -> None:
        payload = (
            "국세청 홈택스\n"
            "납부내역서\n"
            "조회번호,사용자\n"
        ).encode("utf-8")

        response = self._upload(filename="hometax_review.csv", content=payload)
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("검토 필요", body)

        with self.app.app_context():
            doc = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(doc.parse_status, "needs_review")
            self.assertEqual(doc.trust_grade, "C")

    def test_upload_supported_extension_but_unsupported_document_marks_unsupported(self) -> None:
        payload = (
            "개인 정리 파일\n"
            "항목,값\n"
            "메모,직접 정리\n"
        ).encode("utf-8")

        response = self._upload(filename="notes.csv", content=payload)
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("미지원 형식", body)

        with self.app.app_context():
            doc = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(doc.parse_status, "unsupported")
            self.assertEqual(doc.trust_grade, "D")

    def test_upload_bad_xlsx_marks_failed(self) -> None:
        response = self._upload(filename="broken.xlsx", content=b"not-a-real-xlsx")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("읽기 실패", body)

        with self.app.app_context():
            doc = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(doc.parse_status, "failed")
            self.assertEqual(doc.structure_validation_status, "failed")

    def test_upload_rejects_unsupported_extension_without_creating_document(self) -> None:
        response = self._upload(filename="memo.txt", content=b"plain text")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("허용되지 않는 파일 형식입니다.", body)

        with self.app.app_context():
            count = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).count()
            self.assertEqual(count, 0)

    def test_index_lists_uploaded_document_metadata(self) -> None:
        payload = (
            "국세청 홈택스,,\n"
            "원천징수영수증,,\n"
            "지급일,원천징수 세액,소득 구분\n"
            "2026-03-05,33000,사업소득\n"
        ).encode("utf-8")
        self._upload(filename="withholding.csv", content=payload)

        self._login()
        response = self.client.get("/dashboard/official-data")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("최근 업로드", body)
        self.assertIn("홈택스 원천징수 관련 문서", body)
        self.assertIn("withholding.csv", body)


if __name__ == "__main__":
    unittest.main()

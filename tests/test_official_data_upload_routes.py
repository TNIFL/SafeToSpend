from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import OfficialDataDocument, SafeToSpendSettings, User
from services.official_data_store import delete_official_data_file
from services.plan import RuntimePlanState


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "official_data"


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
            settings = SafeToSpendSettings.query.filter_by(user_pk=self.user_pk).first()
            if settings:
                db.session.delete(settings)
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

    def test_upload_shifted_withholding_variant_marks_parsed(self) -> None:
        response = self._upload(
            filename="withholding_shifted.csv",
            content=(FIXTURES / "hometax_withholding_statement_shifted_headers.csv").read_bytes(),
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("반영 가능", body)
        self.assertIn("홈택스 원천징수 관련 문서", body)

        with self.app.app_context():
            doc = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(doc.parse_status, "parsed")
            self.assertEqual(doc.document_type, "hometax_withholding_statement")
            self.assertEqual(doc.extracted_key_summary_json["withheld_tax_total_krw"], 1820000)
            self.assertEqual(doc.trust_grade, "B")

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

    def test_upload_known_source_but_unrecognized_document_stays_review(self) -> None:
        response = self._upload(
            filename="hometax_known_source_unrecognized.csv",
            content=(FIXTURES / "hometax_known_source_unrecognized.csv").read_bytes(),
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("검토 필요", body)

        with self.app.app_context():
            doc = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(doc.parse_status, "needs_review")
            self.assertIsNone(doc.document_type)
            self.assertEqual(doc.trust_grade, "C")

    def test_upload_nhis_variant_pdf_marks_parsed(self) -> None:
        response = self._upload(
            filename="nhis_payment_variant.pdf",
            content=(FIXTURES / "nhis_payment_confirmation_variant.pdf").read_bytes(),
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("반영 가능", body)
        self.assertIn("건강보험 납부확인서", body)

        with self.app.app_context():
            doc = OfficialDataDocument.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(doc.parse_status, "parsed")
            self.assertEqual(doc.document_type, "nhis_payment_confirmation")
            self.assertEqual(doc.extracted_key_summary_json["latest_paid_amount_krw"], 352000)

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

    def test_index_renders_guidance_blocks_and_baseline_documents(self) -> None:
        self._login()

        response = self.client.get("/dashboard/official-data")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("이 채널에 올리면 좋은 자료", body)
        self.assertIn("당신이 입력한 정보 기준 추천 자료", body)
        self.assertIn("잘 모르겠다면 먼저 올릴 기본 자료", body)
        self.assertIn("처리 방식", body)
        self.assertIn("보관 방식", body)
        self.assertIn("삭제 방식", body)
        self.assertIn("홈택스 납부내역", body)
        self.assertIn("건강보험 납부확인서", body)
        self.assertIn("아직 저장된 정보가 없어 기본 자료 안내를 먼저 보여드리고 있습니다.", body)
        self.assertIn("자동 구조화 대상", body)

    def test_index_uses_settings_meta_for_recommendation_copy(self) -> None:
        with self.app.app_context():
            settings = SafeToSpendSettings(
                user_pk=self.user_pk,
                default_tax_rate=0.15,
                custom_rates={
                    "_meta": {
                        "health_insurance_type": "지역가입자",
                        "work_type": "프리랜서",
                        "vat_registered": True,
                    }
                },
            )
            db.session.add(settings)
            db.session.commit()

        self._login()
        response = self.client.get("/dashboard/official-data")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("지역가입자 기준으로 건강보험 관련 공식자료를 먼저 보는 편이 좋습니다.", body)
        self.assertIn("프리랜서 기준으로는 원천징수와 납부내역을 함께 올리는 편이 가장 실용적입니다.", body)
        self.assertIn("과세사업자/부가세 대상이면 지원 범위 안의 홈택스 납부 자료부터 먼저 맞추는 편이 안전합니다.", body)
        self.assertIn("건강보험 자격 관련 문서", body)
        self.assertIn("입력하신 정보 기준으로는 프리랜서 자료를 먼저 올리는 편이 좋습니다.", body)
        self.assertIn("과세사업자/부가세 대상이면 세금계산서·현금영수증·사업용카드 자료도 준비 후보로 같이 챙겨 두는 편이 좋습니다.", body)
        self.assertIn("건강보험 납부확인서와 자격 관련 문서를 함께 올리면 건보 안내와 공식자료 흐름이 덜 끊깁니다.", body)

    def test_index_shows_pro_notice_for_non_pro_plan(self) -> None:
        self._login()

        with patch(
            "routes.web.official_data.build_runtime_plan_state",
            return_value=RuntimePlanState(
                current_plan_code="basic",
                current_plan_label="베이직",
                subscription_ready=False,
                runtime_mode="display_only",
                status_label="구독 준비 중",
                note="display only",
            ),
        ):
            response = self.client.get("/dashboard/official-data")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("프로 안내", body)
        self.assertIn("자동 수집 가능한 공식자료를 행정 일정에 맞춰 자동으로 불러오는 기능을 지원할 예정입니다.", body)

    def test_index_hides_pro_notice_for_pro_plan(self) -> None:
        self._login()

        with patch(
            "routes.web.official_data.build_runtime_plan_state",
            return_value=RuntimePlanState(
                current_plan_code="pro",
                current_plan_label="프로",
                subscription_ready=False,
                runtime_mode="display_only",
                status_label="구독 준비 중",
                note="display only",
            ),
        ):
            response = self.client.get("/dashboard/official-data")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("프로 안내", body)


if __name__ == "__main__":
    unittest.main()

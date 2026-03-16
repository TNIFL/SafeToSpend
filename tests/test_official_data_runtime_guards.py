from __future__ import annotations

import unittest

from domain.models import OfficialDataDocument
from services.official_data_guards import (
    TRUST_GRADE_A,
    TRUST_GRADE_B,
    TRUST_GRADE_C,
    TRUST_GRADE_D,
    classify_sensitive_tokens,
    compute_official_data_trust_grade,
    resolve_trust_fields_for_document,
    sanitize_official_data_payload_for_storage,
    sanitize_official_data_summary_for_render,
)
from services.official_data_upload import build_official_data_result_context


class OfficialDataRuntimeGuardsTest(unittest.TestCase):
    def test_sensitive_token_classifier_detects_rrn_and_health_detail(self) -> None:
        flags = classify_sensitive_tokens("900101-1234567", "상병 내용이 포함된 상세 메모")
        self.assertTrue(flags["resident_registration_number"])
        self.assertTrue(flags["health_detail_text"])

    def test_storage_guard_removes_preview_and_masks_identifier(self) -> None:
        decision = sanitize_official_data_payload_for_storage(
            {
                "issuer_name": "국세청 홈택스",
                "document_name": "원천징수 이행상황 신고서",
                "verified_reference_date": "2026-03-01",
                "total_withheld_tax_krw": 120000,
                "payor_key": "HTX-DEMO-9999",
                "preview_text": "header1 | header2\nvalue1 | value2\n" * 20,
            },
            summary={
                "issuer": "국세청 홈택스",
                "document_name": "원천징수 이행상황 신고서",
                "verified_reference_date": "2026-03-01",
                "total_amount_krw": 120000,
                "primary_key_label": "지급처 식별키",
                "primary_key_value": "HTX-DEMO-9999",
            },
            source_system="hometax",
            document_type="hometax_withholding_statement",
            parse_status="parsed",
        )
        self.assertNotIn("preview_text", decision.payload)
        self.assertNotIn("payor_key", decision.payload)
        self.assertIn("payor_key_hash", decision.payload)
        self.assertIn("payor_key_masked", decision.payload)
        self.assertEqual(decision.summary.get("primary_key_value"), "***9999")

    def test_storage_guard_downgrades_when_rrn_or_health_text_is_detected(self) -> None:
        decision = sanitize_official_data_payload_for_storage(
            {
                "issuer_name": "국민건강보험공단",
                "document_name": "보험료 납부확인서",
                "verified_reference_date": "2026-03-02",
                "insured_key": "900101-1234567",
                "member_type": "상병 관련 상세 메모",
                "total_paid_amount_krw": 333000,
            },
            summary={
                "issuer": "국민건강보험공단",
                "document_name": "보험료 납부확인서",
                "verified_reference_date": "2026-03-02",
                "total_amount_krw": 333000,
                "primary_key_label": "가입자 식별키",
                "primary_key_value": "900101-1234567",
            },
            source_system="nhis",
            document_type="nhis_payment_confirmation",
            parse_status="parsed",
        )
        self.assertTrue(decision.downgraded_to_needs_review)
        self.assertTrue(decision.sensitive_data_redacted)
        self.assertNotIn("insured_key", decision.payload)
        self.assertNotIn("member_type", decision.payload)
        self.assertNotIn("primary_key_value", decision.summary)

    def test_summary_render_sanitizer_keeps_only_safe_rows(self) -> None:
        render_summary = sanitize_official_data_summary_for_render(
            {
                "issuer": "국민건강보험공단",
                "document_name": "보험료 납부확인서",
                "verified_reference_date": "2026-03-02",
                "document_period_start": "2026-01-01",
                "document_period_end": "2026-01-31",
                "total_amount_krw": 333000,
                "primary_key_label": "가입자 식별 참조",
                "primary_key_value": "***-100",
            }
        )
        labels = [row["label"] for row in render_summary.rows]
        self.assertIn("발급기관", labels)
        self.assertIn("식별 참조", " ".join(labels))

    def test_trust_grade_requires_official_verification_for_a(self) -> None:
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source=None,
                verification_status=None,
                parser_parse_status="parsed",
                structure_validation_status="passed",
            ).trust_grade,
            TRUST_GRADE_B,
        )
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source="government24_download_verify",
                verification_status="succeeded",
                parser_parse_status="parsed",
                structure_validation_status="passed",
            ).trust_grade,
            TRUST_GRADE_A,
        )
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source=None,
                verification_status=None,
                parser_parse_status="unsupported",
                structure_validation_status="not_applicable",
            ).trust_grade,
            TRUST_GRADE_C,
        )
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source=None,
                verification_status=None,
                parser_parse_status="parsed",
                structure_validation_status="passed",
                user_modified_flag=True,
            ).trust_grade,
            TRUST_GRADE_D,
        )

    def test_fallback_reads_summary_when_dedicated_fields_are_missing(self) -> None:
        trust = resolve_trust_fields_for_document(
            trust_grade=None,
            trust_grade_label=None,
            trust_scope_label=None,
            verification_source=None,
            verification_status="none",
            parse_status="parsed",
            structure_validation_status="passed",
            user_modified_flag=False,
            summary_fallback={
                "trust_grade": "B",
                "trust_grade_label": "공식 양식 구조와 일치",
                "trust_scope_label": "기관 확인 전 구조 검증 자료",
            },
        )
        self.assertEqual(trust.trust_grade, TRUST_GRADE_B)
        self.assertEqual(trust.trust_grade_label, "공식 양식 구조와 일치")

    def test_build_result_context_uses_dedicated_fields_first(self) -> None:
        document = OfficialDataDocument(
            user_pk=1,
            source_system="nhis",
            document_type="nhis_payment_confirmation",
            display_name="건보료 납부확인서",
            file_name_original="nhis.pdf",
            file_mime_type="application/pdf",
            file_size_bytes=10,
            file_hash="a" * 64,
            parse_status="parsed",
            extracted_payload_json={},
            extracted_key_summary_json={
                "issuer": "국민건강보험공단",
                "document_name": "보험료 납부확인서",
                "verified_reference_date": "2026-03-03",
                "trust_grade": "C",
                "trust_grade_label": "업로드한 자료 기준",
                "trust_scope_label": "기관 확인 전 사용자 업로드 자료 범위",
            },
            trust_grade="B",
            trust_grade_label="공식 양식 구조와 일치",
            trust_scope_label="기관 확인 전 구조 검증 자료",
            structure_validation_status="passed",
            verification_status="none",
            sensitive_data_redacted=True,
        )
        context = build_official_data_result_context(document)
        self.assertEqual(context["trust_grade"], "B")
        self.assertEqual(context["trust_grade_label"], "공식 양식 구조와 일치")


if __name__ == "__main__":
    unittest.main()

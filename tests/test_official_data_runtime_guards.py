from __future__ import annotations

import unittest
from pathlib import Path

from services.official_data_guards import (
    TRUST_GRADE_A,
    TRUST_GRADE_B,
    TRUST_GRADE_C,
    TRUST_GRADE_D,
    classify_sensitive_tokens,
    compute_official_data_trust_grade,
    sanitize_official_data_payload_for_storage,
    sanitize_official_data_summary_for_render,
)


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataRuntimeGuardsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

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
        self.assertEqual(decision.trust_grade, TRUST_GRADE_B)
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
        self.assertEqual(decision.trust_grade, TRUST_GRADE_D)
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
                "trust_grade": "B",
                "trust_grade_label": "공식 양식 구조와 일치",
                "trust_scope_label": "기관 확인 전 구조 검증 자료",
            }
        )
        labels = [row["label"] for row in render_summary.rows]
        self.assertIn("발급기관", labels)
        self.assertIn("식별 참조", " ".join(labels))
        self.assertEqual(render_summary.trust_grade, TRUST_GRADE_B)

    def test_trust_grade_requires_official_verification_for_a(self) -> None:
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source=None,
                verification_status=None,
                parser_parse_status="parsed",
                structure_validation_result="supported_document_type",
            )[0],
            TRUST_GRADE_B,
        )
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source="government24_download_verify",
                verification_status="verified",
                parser_parse_status="parsed",
                structure_validation_result="supported_document_type",
            )[0],
            TRUST_GRADE_A,
        )
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source=None,
                verification_status=None,
                parser_parse_status="unsupported",
                structure_validation_result="unsupported",
            )[0],
            TRUST_GRADE_C,
        )
        self.assertEqual(
            compute_official_data_trust_grade(
                verification_source=None,
                verification_status=None,
                parser_parse_status="parsed",
                structure_validation_result="supported_document_type",
                user_modified_flag=True,
            )[0],
            TRUST_GRADE_D,
        )

    def test_result_templates_and_route_copy_avoid_banned_claims(self) -> None:
        for rel_path in (
            "routes/web/official_data.py",
            "templates/official_data/upload.html",
            "templates/official_data/result.html",
            "templates/partials/official_data_effect_notice.html",
        ):
            body = self._read(rel_path)
            self.assertNotIn("진본", body)
            self.assertNotIn("법적으로 보장", body)
            self.assertNotIn("100% 정확", body)
            self.assertNotIn("원본임을 보증", body)
        result_body = self._read("templates/official_data/result.html")
        self.assertIn("검증 수준", result_body)
        self.assertIn("등급", result_body)


if __name__ == "__main__":
    unittest.main()

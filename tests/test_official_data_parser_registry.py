from __future__ import annotations

import unittest

from services.official_data_parser_registry import identify_official_data_document


class OfficialDataParserRegistryTest(unittest.TestCase):
    def test_identifies_hometax_tax_payment_history_with_variant_title_and_headers(self) -> None:
        rows = [
            ["국세청 홈택스", "", ""],
            ["납부 내역 조회 결과", "", ""],
            ["조회일", "2026-03-10", ""],
            ["최근 납부일", "납부금액 합계", "세목명"],
            ["2026.03.09", "150,000원", "종합소득세"],
        ]

        decision = identify_official_data_document(extension=".csv", rows=rows)

        self.assertEqual(decision.registry_status, "identified")
        self.assertEqual(decision.document_type, "hometax_tax_payment_history")
        self.assertEqual(decision.source_authority, "국세청(홈택스)")

    def test_marks_hometax_document_as_review_when_core_headers_are_missing(self) -> None:
        rows = [
            ["국세청 홈택스"],
            ["납부내역서"],
            ["안내문"],
            ["조회번호", "사용자"],
        ]

        decision = identify_official_data_document(extension=".xlsx", rows=rows)

        self.assertEqual(decision.registry_status, "needs_review")
        self.assertIsNone(decision.document_type)
        self.assertEqual(decision.source_authority, "국세청(홈택스)")

    def test_identifies_nhis_eligibility_pdf_text(self) -> None:
        text = (
            "국민건강보험공단 자격득실확인서 기준일 2026년 3월 1일 "
            "가입자구분 직장가입자 자격상태 정상 취득일 2024-01-01"
        )

        decision = identify_official_data_document(extension=".pdf", extracted_text=text)

        self.assertEqual(decision.registry_status, "identified")
        self.assertEqual(decision.document_type, "nhis_eligibility_status")
        self.assertEqual(decision.source_authority, "국민건강보험공단")

    def test_rejects_non_official_csv(self) -> None:
        rows = [
            ["사용자 정리 파일"],
            ["메모", "금액"],
            ["개인 기록", "10000"],
        ]

        decision = identify_official_data_document(extension=".csv", rows=rows)

        self.assertEqual(decision.registry_status, "unsupported")
        self.assertIsNone(decision.document_type)
        self.assertIsNone(decision.source_authority)


if __name__ == "__main__":
    unittest.main()

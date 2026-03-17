from __future__ import annotations

import csv
import unittest
from pathlib import Path

from services.official_data_parser_registry import identify_official_data_document


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "official_data"


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8") as handle:
        return [[cell.strip() for cell in row] for row in csv.reader(handle)]


def _read_pdf_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="ignore")


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

    def test_identifies_shifted_header_withholding_summary_fixture(self) -> None:
        rows = _read_csv_rows(FIXTURES / "hometax_withholding_statement_shifted_headers.csv")

        decision = identify_official_data_document(extension=".csv", rows=rows)

        self.assertEqual(decision.registry_status, "identified")
        self.assertEqual(decision.document_type, "hometax_withholding_statement")
        self.assertEqual(decision.source_authority, "국세청(홈택스)")

    def test_marks_known_hometax_source_without_supported_document_shape_as_review(self) -> None:
        rows = _read_csv_rows(FIXTURES / "hometax_known_source_unrecognized.csv")

        decision = identify_official_data_document(extension=".csv", rows=rows)

        self.assertEqual(decision.registry_status, "needs_review")
        self.assertIsNone(decision.document_type)
        self.assertEqual(decision.source_authority, "국세청(홈택스)")

    def test_identifies_nhis_eligibility_pdf_text_with_label_variants(self) -> None:
        text = _read_pdf_text(FIXTURES / "nhis_eligibility_status_variant.pdf")

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

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from services.official_data_parsers import (
    parse_hometax_tax_payment_history,
    parse_hometax_withholding_statement,
    parse_nhis_eligibility_status,
    parse_nhis_payment_confirmation,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "official_data"


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8") as handle:
        return [[cell.strip() for cell in row] for row in csv.reader(handle)]


def _read_pdf_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="ignore")


class OfficialDataParsersTest(unittest.TestCase):
    def test_parse_hometax_withholding_statement_success(self) -> None:
        rows = [
            ["국세청 홈택스"],
            ["원천징수영수증"],
            ["지급일", "원천징수 세액", "총지급액", "소득 구분"],
            ["2026-03-05", "33,000원", "330,000원", "사업소득"],
            ["2026-03-15", "12,000원", "120,000원", "사업소득"],
        ]

        parsed = parse_hometax_withholding_statement(rows)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-03-15")
        self.assertEqual(parsed["summary"]["withheld_tax_total_krw"], 45000)

    def test_parse_hometax_withholding_statement_shifted_header_summary_success(self) -> None:
        rows = _read_csv_rows(FIXTURES / "hometax_withholding_statement_shifted_headers.csv")

        parsed = parse_hometax_withholding_statement(rows)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-02-10")
        self.assertEqual(parsed["summary"]["withheld_tax_total_krw"], 1820000)
        self.assertEqual(parsed["summary"]["payer_reference"], "HTX-PAYER-0099")

    def test_parse_hometax_tax_payment_history_summary_variant_success(self) -> None:
        rows = _read_csv_rows(FIXTURES / "hometax_tax_payment_history_variant.csv")

        parsed = parse_hometax_tax_payment_history(rows)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-03-12")
        self.assertEqual(parsed["summary"]["paid_tax_total_krw"], 640000)
        self.assertEqual(parsed["summary"]["latest_payment_date"], "2026-03-10")
        self.assertEqual(parsed["summary"]["payment_entry_count"], 2)

    def test_parse_hometax_tax_payment_history_marks_review_when_tax_type_is_missing(self) -> None:
        rows = [
            ["국세청 홈택스"],
            ["조회일", "2026-03-10"],
            ["납부일", "납부세액 합계"],
            ["2026.03.09", "150,000원"],
        ]

        parsed = parse_hometax_tax_payment_history(rows)

        self.assertEqual(parsed["parse_status"], "needs_review")
        self.assertEqual(parsed["structure_validation_status"], "needs_review")

    def test_parse_nhis_payment_confirmation_variant_success(self) -> None:
        text = _read_pdf_text(FIXTURES / "nhis_payment_confirmation_variant.pdf")

        parsed = parse_nhis_payment_confirmation(text)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-03-03")
        self.assertEqual(parsed["summary"]["latest_paid_amount_krw"], 352000)
        self.assertEqual(parsed["summary"]["insured_reference"], "NHIS-DEMO-100")
        self.assertEqual(parsed["summary"]["period_start"], "2026-01-01")
        self.assertEqual(parsed["summary"]["period_end"], "2026-02-28")

    def test_parse_nhis_eligibility_status_variant_success(self) -> None:
        text = _read_pdf_text(FIXTURES / "nhis_eligibility_status_variant.pdf")

        parsed = parse_nhis_eligibility_status(text)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-03-11")
        self.assertEqual(parsed["summary"]["subscriber_type"], "지역가입자")
        self.assertEqual(parsed["summary"]["eligibility_status"], "유지")
        self.assertEqual(parsed["summary"]["eligibility_start_date"], "2025-07-01")
        self.assertEqual(parsed["summary"]["latest_status_change_date"], "2026-02-01")


if __name__ == "__main__":
    unittest.main()

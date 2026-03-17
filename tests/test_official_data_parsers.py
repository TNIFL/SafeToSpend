from __future__ import annotations

import unittest

from services.official_data_parsers import (
    parse_hometax_tax_payment_history,
    parse_hometax_withholding_statement,
    parse_nhis_eligibility_status,
    parse_nhis_payment_confirmation,
)


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

    def test_parse_nhis_payment_confirmation_success(self) -> None:
        text = (
            "국민건강보험공단 납부확인서 기준일: 2026년 3월 10일 "
            "가입자구분: 지역가입자 납부금액: 123,000원"
        )

        parsed = parse_nhis_payment_confirmation(text)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-03-10")
        self.assertEqual(parsed["summary"]["latest_paid_amount_krw"], 123000)

    def test_parse_nhis_eligibility_status_success(self) -> None:
        text = (
            "국민건강보험공단 자격득실확인서 기준일 2026-03-01 "
            "가입자구분 직장가입자 자격상태 정상 취득일 2024-01-01 "
            "상실일 2026-02-28"
        )

        parsed = parse_nhis_eligibility_status(text)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["structure_validation_status"], "passed")
        self.assertEqual(parsed["reference_date"].isoformat(), "2026-03-01")
        self.assertEqual(parsed["summary"]["subscriber_type"], "직장가입자")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.official_data_extractors import build_envelope_from_path
from services.official_data_parser_registry import resolve_fixture_document
from services.official_data_parsers import (
    PARSE_STATUS_NEEDS_REVIEW,
    PARSE_STATUS_PARSED,
    PARSE_STATUS_UNSUPPORTED,
    parse_fixture_for_registry,
    parse_hometax_business_card_usage,
    parse_hometax_tax_payment_history,
    parse_hometax_withholding_statement,
    parse_nhis_eligibility_status,
    parse_nhis_payment_confirmation,
    write_parser_smoke_report,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'official_data'


class OfficialDataParsersTest(unittest.TestCase):
    def test_supported_parsers_extract_expected_summary(self) -> None:
        withholding = parse_hometax_withholding_statement(build_envelope_from_path(FIXTURES / 'hometax_withholding_statement.csv'))
        self.assertEqual(withholding.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(withholding.extracted_key_summary['total_amount_krw'], 1820000)

        withholding_variant = parse_hometax_withholding_statement(build_envelope_from_path(FIXTURES / 'hometax_withholding_statement_shifted_headers.csv'))
        self.assertEqual(withholding_variant.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(withholding_variant.extracted_payload['total_withheld_tax_krw'], 1820000)

        card_usage = parse_hometax_business_card_usage(build_envelope_from_path(FIXTURES / 'hometax_business_card_usage.xlsx'))
        self.assertEqual(card_usage.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(card_usage.extracted_key_summary['total_amount_krw'], 485000)

        tax_payment = parse_hometax_tax_payment_history(build_envelope_from_path(FIXTURES / 'hometax_tax_payment_history.csv'))
        self.assertEqual(tax_payment.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(tax_payment.extracted_payload['paid_tax_total_krw'], 640000)
        self.assertEqual(tax_payment.extracted_key_summary['primary_key_value'], '***합소득세')

        tax_payment_variant = parse_hometax_tax_payment_history(build_envelope_from_path(FIXTURES / 'hometax_tax_payment_history_variant.csv'))
        self.assertEqual(tax_payment_variant.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(tax_payment_variant.extracted_payload['paid_tax_total_krw'], 640000)
        self.assertEqual(tax_payment_variant.extracted_payload['latest_payment_date'], '2026-03-10')

        nhis = parse_nhis_payment_confirmation(build_envelope_from_path(FIXTURES / 'nhis_payment_confirmation.pdf'))
        self.assertEqual(nhis.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(nhis.extracted_key_summary['primary_key_value'], '***-100')

        nhis_variant = parse_nhis_payment_confirmation(build_envelope_from_path(FIXTURES / 'nhis_payment_confirmation_variant.pdf'))
        self.assertEqual(nhis_variant.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(nhis_variant.extracted_payload['total_paid_amount_krw'], 352000)

        eligibility = parse_nhis_eligibility_status(build_envelope_from_path(FIXTURES / 'nhis_eligibility_status.pdf'))
        self.assertEqual(eligibility.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(eligibility.extracted_payload['eligibility_status'], '유지')

        eligibility_variant = parse_nhis_eligibility_status(build_envelope_from_path(FIXTURES / 'nhis_eligibility_status_variant.pdf'))
        self.assertEqual(eligibility_variant.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(eligibility_variant.extracted_payload['eligibility_start_date'], '2025-07-01')

    def test_parser_returns_needs_review_when_required_fields_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'missing_fields.csv'
            path.write_text('문서명,발급기관,기준일\n원천징수 이행상황 신고서,국세청 홈택스,2026-02-10\n', encoding='utf-8')
            result = parse_hometax_withholding_statement(build_envelope_from_path(path))
        self.assertEqual(result.parse_status, PARSE_STATUS_NEEDS_REVIEW)
        self.assertEqual(result.parse_error_code, 'missing_required_fields')
        self.assertNotIn('record', result.extracted_payload)

        payment_result = parse_hometax_tax_payment_history(build_envelope_from_path(FIXTURES / 'hometax_tax_payment_history_partial.csv'))
        self.assertEqual(payment_result.parse_status, PARSE_STATUS_NEEDS_REVIEW)
        self.assertEqual(payment_result.parse_error_code, 'missing_required_fields')

        eligibility_result = parse_nhis_eligibility_status(build_envelope_from_path(FIXTURES / 'nhis_eligibility_partial.pdf'))
        self.assertEqual(eligibility_result.parse_status, PARSE_STATUS_NEEDS_REVIEW)
        self.assertEqual(eligibility_result.parse_error_code, 'document_header_mismatch')

    def test_unregistered_parser_falls_back_to_unsupported(self) -> None:
        result = parse_fixture_for_registry('missing_document_type', build_envelope_from_path(FIXTURES / 'hometax_withholding_statement.csv'))
        self.assertEqual(result.parse_status, PARSE_STATUS_UNSUPPORTED)
        self.assertEqual(result.parse_error_code, 'parser_not_registered')

    def test_new_tax_payment_parser_keeps_payload_free_of_raw_sensitive_preview(self) -> None:
        result = parse_hometax_tax_payment_history(build_envelope_from_path(FIXTURES / 'hometax_tax_payment_history.csv'))
        self.assertEqual(result.parse_status, PARSE_STATUS_PARSED)
        self.assertNotIn('preview_text', result.extracted_payload)
        self.assertNotIn('raw_text', result.extracted_payload)
        self.assertNotIn('fixture_source', result.extracted_payload)

    def test_variant_pdf_and_shifted_tabular_structures_do_not_force_missing_defaults(self) -> None:
        tax_payment = parse_hometax_tax_payment_history(build_envelope_from_path(FIXTURES / 'hometax_tax_payment_history_variant.csv'))
        self.assertEqual(tax_payment.parse_status, PARSE_STATUS_PARSED)
        self.assertEqual(tax_payment.extracted_payload['payment_entry_count'], 2)

        eligibility = parse_nhis_eligibility_status(build_envelope_from_path(FIXTURES / 'nhis_eligibility_status_variant.pdf'))
        self.assertEqual(eligibility.parse_status, PARSE_STATUS_PARSED)
        self.assertIsNone(eligibility.extracted_payload['eligibility_end_date'])

    def test_smoke_report_is_written_from_fixture_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / 'smoke.json'
            report = write_parser_smoke_report(
                fixture_paths=[
                    FIXTURES / 'hometax_withholding_statement.csv',
                    FIXTURES / 'hometax_withholding_statement_shifted_headers.csv',
                    FIXTURES / 'hometax_business_card_usage.xlsx',
                    FIXTURES / 'hometax_tax_payment_history.csv',
                    FIXTURES / 'hometax_tax_payment_history_variant.csv',
                    FIXTURES / 'nhis_payment_confirmation.pdf',
                    FIXTURES / 'nhis_payment_confirmation_variant.pdf',
                    FIXTURES / 'nhis_eligibility_status.pdf',
                    FIXTURES / 'nhis_eligibility_status_variant.pdf',
                    FIXTURES / 'unknown_headers.csv',
                ],
                resolver=resolve_fixture_document,
                output_path=output_path,
            )
            written = json.loads(output_path.read_text(encoding='utf-8'))
        self.assertEqual(report['row_count'], 10)
        self.assertEqual(written['row_count'], 10)
        self.assertEqual(written['rows'][0]['parse_status'], PARSE_STATUS_PARSED)


if __name__ == '__main__':
    unittest.main()

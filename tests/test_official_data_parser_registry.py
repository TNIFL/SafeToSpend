from __future__ import annotations

import unittest
from pathlib import Path

from services.official_data_extractors import build_envelope_from_path
from services.official_data_parser_registry import (
    REGISTRY_STATUS_NEEDS_REVIEW,
    REGISTRY_STATUS_SUPPORTED,
    REGISTRY_STATUS_UNSUPPORTED_DOCUMENT,
    REGISTRY_STATUS_UNSUPPORTED_FORMAT,
    get_parser_for_document_type,
    identify_official_data_document,
    list_supported_document_options,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'official_data'


class OfficialDataParserRegistryTest(unittest.TestCase):
    def test_supported_fixture_documents_are_identified(self) -> None:
        cases = {
            'hometax_withholding_statement.csv': 'hometax_withholding_statement',
            'hometax_business_card_usage.xlsx': 'hometax_business_card_usage',
            'hometax_tax_payment_history.csv': 'hometax_tax_payment_history',
            'nhis_payment_confirmation.pdf': 'nhis_payment_confirmation',
            'nhis_eligibility_status.pdf': 'nhis_eligibility_status',
        }
        for filename, document_type in cases.items():
            envelope = build_envelope_from_path(FIXTURES / filename)
            decision = identify_official_data_document(envelope)
            self.assertEqual(decision.registry_status, REGISTRY_STATUS_SUPPORTED)
            self.assertEqual(decision.supported_document_type, document_type)

    def test_unsupported_and_needs_review_cases_are_closed_safely(self) -> None:
        unsupported = identify_official_data_document(build_envelope_from_path(FIXTURES / 'unsupported_capture.jpg'))
        self.assertEqual(unsupported.registry_status, REGISTRY_STATUS_UNSUPPORTED_FORMAT)
        self.assertEqual(unsupported.parse_error_code, 'unsupported_extension')

        unknown = identify_official_data_document(build_envelope_from_path(FIXTURES / 'unknown_headers.csv'))
        self.assertEqual(unknown.registry_status, REGISTRY_STATUS_UNSUPPORTED_DOCUMENT)
        self.assertEqual(unknown.parse_error_code, 'unsupported_document_type')

        partial_tax = identify_official_data_document(build_envelope_from_path(FIXTURES / 'hometax_tax_payment_history_partial.csv'))
        self.assertEqual(partial_tax.registry_status, REGISTRY_STATUS_NEEDS_REVIEW)
        self.assertEqual(partial_tax.supported_document_type, 'hometax_tax_payment_history')
        self.assertEqual(partial_tax.parse_error_code, 'partial_structure_detected')

        partial_nhis = identify_official_data_document(build_envelope_from_path(FIXTURES / 'nhis_eligibility_partial.pdf'))
        self.assertEqual(partial_nhis.registry_status, REGISTRY_STATUS_NEEDS_REVIEW)
        self.assertEqual(partial_nhis.supported_document_type, 'nhis_eligibility_status')
        self.assertEqual(partial_nhis.parse_error_code, 'partial_structure_detected')

        encrypted = identify_official_data_document(build_envelope_from_path(FIXTURES / 'encrypted_notice.pdf'))
        self.assertEqual(encrypted.registry_status, REGISTRY_STATUS_UNSUPPORTED_FORMAT)
        self.assertEqual(encrypted.parse_error_code, 'encrypted_pdf_unsupported')

        scanned = identify_official_data_document(build_envelope_from_path(FIXTURES / 'scanned_image_notice.pdf'))
        self.assertEqual(scanned.registry_status, REGISTRY_STATUS_UNSUPPORTED_FORMAT)
        self.assertEqual(scanned.parse_error_code, 'scanned_pdf_unsupported')

    def test_hint_mismatch_becomes_needs_review(self) -> None:
        envelope = build_envelope_from_path(FIXTURES / 'nhis_payment_confirmation.pdf')
        decision = identify_official_data_document(envelope, document_type_hint='hometax_withholding_statement')
        self.assertEqual(decision.registry_status, REGISTRY_STATUS_NEEDS_REVIEW)
        self.assertEqual(decision.supported_document_type, 'nhis_payment_confirmation')
        self.assertEqual(decision.parse_error_code, 'document_type_mismatch')

    def test_supported_options_and_parser_lookup_exist(self) -> None:
        options = list_supported_document_options()
        document_types = {item['document_type'] for item in options}
        self.assertGreaterEqual(len(options), 5)
        self.assertIn('hometax_tax_payment_history', document_types)
        self.assertIn('nhis_eligibility_status', document_types)
        self.assertIsNotNone(get_parser_for_document_type('hometax_withholding_statement'))
        self.assertIsNone(get_parser_for_document_type('missing_document_type'))


if __name__ == '__main__':
    unittest.main()

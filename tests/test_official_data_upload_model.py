from __future__ import annotations

import unittest

from sqlalchemy import CheckConstraint

from domain.models import OfficialDataDocument


class OfficialDataUploadModelTest(unittest.TestCase):
    def test_model_has_expected_columns(self) -> None:
        columns = set(OfficialDataDocument.__table__.columns.keys())
        expected = {
            'user_pk',
            'source_system',
            'document_type',
            'display_name',
            'file_name_original',
            'file_mime_type',
            'file_size_bytes',
            'file_hash',
            'parser_version',
            'parse_status',
            'parse_error_code',
            'parse_error_detail',
            'extracted_payload_json',
            'extracted_key_summary_json',
            'document_issued_at',
            'document_period_start',
            'document_period_end',
            'verified_reference_date',
            'raw_file_storage_mode',
            'raw_file_key',
            'created_at',
            'updated_at',
            'parsed_at',
        }
        self.assertTrue(expected.issubset(columns))

    def test_model_defaults_and_constraints_are_declared(self) -> None:
        table = OfficialDataDocument.__table__
        self.assertEqual(table.c.parse_status.default.arg, 'uploaded')
        self.assertEqual(table.c.raw_file_storage_mode.default.arg, 'none')
        constraints = ' '.join(
            str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        )
        self.assertIn('source_system', constraints)
        self.assertIn('parse_status', constraints)
        self.assertIn('raw_file_storage_mode', constraints)


if __name__ == '__main__':
    unittest.main()

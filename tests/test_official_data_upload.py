from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from services.official_data_upload import process_official_data_upload


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "official_data"


def _file_storage(path: Path, content_type: str) -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(path.read_bytes()),
        filename=path.name,
        content_type=content_type,
    )


class OfficialDataUploadServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config["MAX_UPLOAD_BYTES"] = 10 * 1024 * 1024
        self.app.config["SECRET_KEY"] = "official-data-upload-service-test"
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self) -> None:
        self.ctx.pop()

    @patch("services.official_data_upload.db.session.commit")
    @patch("services.official_data_upload.db.session.add")
    def test_shifted_tabular_variant_can_still_parse(self, add_mock, commit_mock) -> None:
        outcome = process_official_data_upload(
            user_pk=7,
            uploaded_file=_file_storage(
                FIXTURES / "hometax_tax_payment_history_variant.csv",
                "text/csv",
            ),
            document_type_hint="hometax_tax_payment_history",
        )
        self.assertEqual(outcome.document.parse_status, "parsed")
        self.assertEqual(outcome.document.document_type, "hometax_tax_payment_history")
        self.assertEqual(outcome.status_title, "구조 검증 완료")
        self.assertEqual(outcome.document.extracted_payload_json["paid_tax_total_krw"], 640000)
        add_mock.assert_called_once()
        commit_mock.assert_called_once()

    @patch("services.official_data_upload.db.session.commit")
    @patch("services.official_data_upload.db.session.add")
    def test_known_source_but_unrecognized_fixture_stays_review_not_parsed(self, add_mock, commit_mock) -> None:
        outcome = process_official_data_upload(
            user_pk=7,
            uploaded_file=_file_storage(
                FIXTURES / "hometax_known_source_unrecognized.csv",
                "text/csv",
            ),
            document_type_hint="hometax_tax_payment_history",
        )
        self.assertEqual(outcome.document.parse_status, "needs_review")
        self.assertIn(
            outcome.document.parse_error_code,
            {"known_source_but_unrecognized", "partial_structure_detected"},
        )
        self.assertEqual(outcome.status_title, "검토 필요")
        add_mock.assert_called_once()
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

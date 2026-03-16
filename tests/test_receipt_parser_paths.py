from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.receipt_parser import parse_receipt_from_file


class ReceiptParserPathTest(unittest.TestCase):
    def _tmp_file(self, suffix: str, data: bytes) -> Path:
        fd, path = tempfile.mkstemp(suffix=suffix)
        p = Path(path)
        p.write_bytes(data)
        return p

    def test_pdf_uses_file_input_path(self) -> None:
        pdf_path = self._tmp_file(".pdf", b"%PDF-1.7\n" + (b"0" * 40))
        try:
            captured: dict = {}

            def fake_extract(**kwargs):
                captured.update(kwargs)
                return True, {"merchant": "ok"}, "", {}

            with patch("services.receipt_parser.extract_receipt_json", side_effect=fake_extract):
                draft = parse_receipt_from_file(abs_path=pdf_path, mime_type="application/pdf")

            self.assertTrue(draft.ok)
            self.assertTrue(bool(captured.get("receipt_file_base64")))
            self.assertEqual(captured.get("receipt_file_mime"), "application/pdf")
            self.assertEqual(captured.get("receipt_file_name"), pdf_path.name)
            self.assertIsNone(captured.get("receipt_image_data_url"))
        finally:
            pdf_path.unlink(missing_ok=True)

    def test_image_uses_image_data_url_path(self) -> None:
        png_path = self._tmp_file(".png", b"\x89PNG\r\n\x1a\n" + (b"1" * 40))
        try:
            captured: dict = {}

            def fake_extract(**kwargs):
                captured.update(kwargs)
                return True, {"merchant": "ok"}, "", {}

            with patch("services.receipt_parser.extract_receipt_json", side_effect=fake_extract):
                draft = parse_receipt_from_file(abs_path=png_path, mime_type="image/png")

            self.assertTrue(draft.ok)
            data_url = str(captured.get("receipt_image_data_url") or "")
            self.assertTrue(data_url.startswith("data:image/png;base64,"))
            self.assertIsNone(captured.get("receipt_file_base64"))
        finally:
            png_path.unlink(missing_ok=True)

    def test_invalid_pdf_header_fails_safely(self) -> None:
        bad_pdf = self._tmp_file(".pdf", b"NOT_A_PDF_FILE")
        try:
            with patch("services.receipt_parser.extract_receipt_json") as mocked:
                draft = parse_receipt_from_file(abs_path=bad_pdf, mime_type="application/pdf")
            self.assertFalse(draft.ok)
            self.assertIn("PDF 파싱 실패", str(draft.error or ""))
            mocked.assert_not_called()
        finally:
            bad_pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

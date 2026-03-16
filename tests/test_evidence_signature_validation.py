from __future__ import annotations

import io
import unittest

from werkzeug.datastructures import FileStorage

from services.evidence_vault import _validate_file


class EvidenceSignatureValidationTest(unittest.TestCase):
    def _file(self, name: str, data: bytes, mimetype: str) -> FileStorage:
        return FileStorage(stream=io.BytesIO(data), filename=name, content_type=mimetype)

    def test_rejects_spoofed_non_image(self) -> None:
        f = self._file("fake.jpg", b"<html>not-image</html>", "image/jpeg")
        with self.assertRaises(ValueError):
            _validate_file(f)

    def test_accepts_extensionless_png_by_signature(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + (b"0" * 64)
        f = self._file("receipt", png, "application/octet-stream")
        safe_name, mime = _validate_file(f)
        self.assertTrue(safe_name.endswith(".png"))
        self.assertEqual(mime, "image/png")

    def test_normalizes_wrong_extension_to_pdf(self) -> None:
        pdf = b"%PDF-1.7\n" + (b"1" * 64)
        f = self._file("receipt.txt", pdf, "text/plain")
        safe_name, mime = _validate_file(f)
        self.assertTrue(safe_name.endswith(".pdf"))
        self.assertEqual(mime, "application/pdf")

    def test_accepts_heic_signature(self) -> None:
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heic" + (b"0" * 32)
        f = self._file("ios_capture.bin", heic, "application/octet-stream")
        safe_name, mime = _validate_file(f)
        self.assertTrue(safe_name.endswith(".heic"))
        self.assertEqual(mime, "image/heic")

    def test_signature_check_reads_from_stream_start(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + (b"A" * 64)
        f = self._file("offset_test.dat", png, "application/octet-stream")
        # 스트림이 중간에 있어도 시그니처 검증은 파일 시작 바이트를 본다.
        _ = f.stream.read(10)
        safe_name, mime = _validate_file(f)
        self.assertTrue(safe_name.endswith(".png"))
        self.assertEqual(mime, "image/png")


if __name__ == "__main__":
    unittest.main()

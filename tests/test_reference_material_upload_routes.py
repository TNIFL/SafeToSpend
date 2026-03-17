from __future__ import annotations

import io
import tempfile
import unittest
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import ReferenceMaterialItem, User
from services.reference_material_upload import delete_reference_material_item_file


class ReferenceMaterialUploadRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            REFERENCE_MATERIAL_UPLOAD_DIR=self.tmpdir.name,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"reference-material-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

    def tearDown(self) -> None:
        with self.app.app_context():
            items = ReferenceMaterialItem.query.filter_by(user_pk=self.user_pk).all()
            for item in items:
                delete_reference_material_item_file(item=item)
                db.session.delete(item)
            user = User.query.filter_by(id=self.user_pk).first()
            if user:
                db.session.delete(user)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def _upload(
        self,
        *,
        material_kind: str,
        filename: str,
        content: bytes,
        title: str = "",
        note: str = "",
        follow_redirects: bool = True,
    ):
        self._login()
        return self.client.post(
            "/dashboard/reference-materials/upload",
            data={
                "material_kind": material_kind,
                "title": title,
                "note": note,
                "file": (io.BytesIO(content), filename),
            },
            content_type="multipart/form-data",
            follow_redirects=follow_redirects,
        )

    def test_reference_material_upload_succeeds(self) -> None:
        response = self._upload(
            material_kind="reference",
            filename="manual_note.pdf",
            content=b"%PDF-1.4\nreference material\n",
            title="직접 정리한 메모",
            note="세무사에게 같이 봐 달라고 올림",
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("참고자료를 참고용으로 보관했습니다.", body)
        self.assertIn("자동 반영 안 됨", body)

        with self.app.app_context():
            item = ReferenceMaterialItem.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(item.material_kind, "reference")
            self.assertEqual(item.title, "직접 정리한 메모")
            self.assertEqual(item.original_filename, "manual_note.pdf")

    def test_note_attachment_upload_succeeds(self) -> None:
        response = self._upload(
            material_kind="note_attachment",
            filename="memo.txt",
            content="추가 설명 파일".encode("utf-8"),
            title="추가 설명 메모",
            note="거래 배경 설명",
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("추가설명을 참고용으로 보관했습니다.", body)

        with self.app.app_context():
            item = ReferenceMaterialItem.query.filter_by(user_pk=self.user_pk).one()
            self.assertEqual(item.material_kind, "note_attachment")
            self.assertEqual(item.note, "거래 배경 설명")

    def test_unsupported_extension_is_rejected(self) -> None:
        response = self._upload(
            material_kind="reference",
            filename="archive.zip",
            content=b"zip-bytes",
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("허용되지 않는 파일 형식입니다.", body)

        with self.app.app_context():
            self.assertEqual(ReferenceMaterialItem.query.filter_by(user_pk=self.user_pk).count(), 0)

    def test_index_lists_items_with_reference_only_copy(self) -> None:
        self._upload(
            material_kind="reference",
            filename="working.csv",
            content="항목,값\n메모,직접 정리".encode("utf-8"),
            title="직접 정리 엑셀",
            note="계산 근거가 아니라 참고용",
        )

        self._login()
        response = self.client.get("/dashboard/reference-materials")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("참고자료 / 추가설명 업로드", body)
        self.assertIn("참고용", body)
        self.assertIn("자동 반영 안 됨", body)
        self.assertIn("세무사 참고용", body)
        self.assertIn("직접 정리 엑셀", body)
        self.assertIn("working.csv", body)

    def test_download_returns_uploaded_file(self) -> None:
        self._upload(
            material_kind="reference",
            filename="notes.txt",
            content="hello reference".encode("utf-8"),
            title="참고 텍스트",
        )

        with self.app.app_context():
            item = ReferenceMaterialItem.query.filter_by(user_pk=self.user_pk).one()
            item_id = int(item.id)

        self._login()
        response = self.client.get(f"/dashboard/reference-materials/{item_id}/download")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        response.close()


if __name__ == "__main__":
    unittest.main()

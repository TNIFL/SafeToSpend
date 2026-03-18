from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import create_app
from core.extensions import db
from domain.models import (
    BankAccountLink,
    EvidenceItem,
    ExpenseLabel,
    SafeToSpendSettings,
    Transaction,
    User,
    UserDashboardState,
)


class ReceiptModalRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            EVIDENCE_UPLOAD_DIR=self.tmpdir.name,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(email=f"receipt-modal-{uuid4().hex}@example.com")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
            self.user_pk = int(user.id)

            account = BankAccountLink(
                user_pk=self.user_pk,
                bank_code="0092",
                account_number="100200300400",
                alias="토스 주계좌",
                is_active=True,
            )
            db.session.add(account)
            db.session.commit()
            self.account_id = int(account.id)

    def tearDown(self) -> None:
        with self.app.app_context():
            EvidenceItem.query.filter(EvidenceItem.user_pk == self.user_pk).delete(synchronize_session=False)
            ExpenseLabel.query.filter(ExpenseLabel.user_pk == self.user_pk).delete(synchronize_session=False)
            Transaction.query.filter(Transaction.user_pk == self.user_pk).delete(synchronize_session=False)
            SafeToSpendSettings.query.filter(SafeToSpendSettings.user_pk == self.user_pk).delete(
                synchronize_session=False
            )
            UserDashboardState.query.filter(UserDashboardState.user_pk == self.user_pk).delete(
                synchronize_session=False
            )
            BankAccountLink.query.filter(BankAccountLink.user_pk == self.user_pk).delete(synchronize_session=False)
            User.query.filter(User.id == self.user_pk).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
            db.engine.dispose()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_pk

    def _wait_for_job(self, job_id: str, *, timeout: float = 2.0) -> dict:
        deadline = time.time() + timeout
        last_payload = None
        while time.time() < deadline:
            response = self.client.get(f"/dashboard/receipt-modal/jobs/{job_id}")
            self.assertEqual(response.status_code, 200)
            last_payload = response.get_json()
            if last_payload["job"]["is_complete"]:
                return last_payload
            time.sleep(0.02)
        self.fail(f"receipt modal job {job_id} did not complete in time; last_payload={last_payload}")

    @staticmethod
    def _fake_parsed_receipt(item) -> dict:
        filename = str(getattr(item, "filename", ""))
        if "스타벅스" in filename:
            return {
                "occurred_on": "2026-03-18",
                "occurred_time": "22:15" if "2215" in filename else "12:30",
                "amount_krw": 23500,
                "counterparty": "스타벅스",
                "payment_item": "아메리카노",
                "payment_method": "카드 ****4321",
                "memo": "영수증 기반 추정",
                "usage": "unknown",
                "warnings": [],
            }
        if "편의점" in filename:
            return {
                "occurred_on": "2026-03-18",
                "occurred_time": "09:10",
                "amount_krw": 11000,
                "counterparty": "편의점",
                "payment_item": None,
                "payment_method": None,
                "memo": None,
                "usage": "unknown",
                "warnings": ["일부 값은 직접 확인이 필요합니다."],
            }
        return {
            "occurred_on": None,
            "occurred_time": None,
            "amount_krw": None,
            "counterparty": None,
            "payment_item": None,
            "payment_method": None,
            "memo": None,
            "usage": "unknown",
            "warnings": ["영수증 값을 찾지 못했습니다."],
        }

    def test_floating_receipt_button_is_hidden_for_public_and_visible_after_login(self) -> None:
        public_body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("data-receipt-open", public_body)

        self._login()
        private_body = self.client.get("/overview").get_data(as_text=True)
        self.assertIn("data-receipt-open", private_body)
        self.assertIn("data-receipt-start", private_body)
        self.assertIn("1. 업로드", private_body)
        self.assertIn("2. 파싱 확인", private_body)
        self.assertIn("3. 결과 확인", private_body)

    def test_start_requires_login(self) -> None:
        response = self.client.post("/dashboard/receipt-modal/start", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/dashboard/receipt-modal/start", response.headers["Location"])

    def test_start_rejects_more_than_50_files(self) -> None:
        self._login()
        files = [(io.BytesIO(b"fake-image"), f"receipt-{index}.jpg") for index in range(51)]

        response = self.client.post(
            "/dashboard/receipt-modal/start",
            data={"files": files},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("최대 50개", payload["error"])

    def test_start_returns_json_when_request_body_is_too_large(self) -> None:
        self._login()
        self.app.config["MAX_CONTENT_LENGTH"] = 128
        self.app.config["RECEIPT_MODAL_MAX_BYTES"] = 128

        response = self.client.post(
            "/dashboard/receipt-modal/start",
            data={
                "files": [
                    (io.BytesIO(b"x" * 4096), "too-large.jpg"),
                ]
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 413)
        payload = response.get_json()
        self.assertIsNotNone(payload)
        self.assertFalse(payload["ok"])
        self.assertIn("업로드 전체 용량이 너무 큽니다", payload["error"])

    def test_start_returns_job_and_account_options(self) -> None:
        self._login()

        with patch("services.receipt_modal._parse_receipt_file_with_openai", side_effect=self._fake_parsed_receipt):
            response = self.client.post(
                "/dashboard/receipt-modal/start",
                data={
                    "files": [
                        (io.BytesIO(b"fake-image"), "20260318_23500원_스타벅스.jpg"),
                        (io.BytesIO(b"fake-image"), "receipt_plain.png"),
                    ]
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["max_files"], 50)
        self.assertEqual(payload["accounts"][0]["label"], "토스 주계좌")
        self.assertEqual(len(payload["job"]["items"]), 2)
        self.assertEqual(payload["job"]["items"][0]["filename"], "20260318_23500원_스타벅스.jpg")
        self.assertIn(payload["job"]["items"][0]["status"], {"queued", "processing", "ready"})

    def test_status_returns_parsed_fields_after_background_work(self) -> None:
        self._login()
        with patch("services.receipt_modal._parse_receipt_file_with_openai", side_effect=self._fake_parsed_receipt):
            start_response = self.client.post(
                "/dashboard/receipt-modal/start",
                data={
                    "files": [
                        (io.BytesIO(b"fake-image"), "20260318_2215_23500원_스타벅스_item커피_card4321.jpg"),
                    ]
                },
                content_type="multipart/form-data",
            )
            job_id = start_response.get_json()["job"]["job_id"]
            payload = self._wait_for_job(job_id)
        item = payload["job"]["items"][0]

        self.assertTrue(payload["job"]["is_complete"])
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["amount_krw"], 23500)
        self.assertEqual(item["counterparty"], "스타벅스")
        self.assertEqual(item["occurred_on"], "2026-03-18")
        self.assertEqual(item["occurred_time"], "22:15")
        self.assertEqual(item["payment_method"], "카드 ****4321")

    def test_start_accepts_heic_images(self) -> None:
        self._login()

        with patch("services.receipt_modal._parse_receipt_file_with_openai", side_effect=self._fake_parsed_receipt):
            response = self.client.post(
                "/dashboard/receipt-modal/start",
                data={
                    "files": [
                        (io.BytesIO(b"fake-heic-image"), "20260318_11000원_편의점.heic"),
                    ]
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["job"]["items"]), 1)
        self.assertEqual(payload["job"]["items"][0]["filename"], "20260318_11000원_편의점.heic")

    def test_create_transaction_and_attach_evidence_with_selected_account(self) -> None:
        self._login()
        with patch("services.receipt_modal._parse_receipt_file_with_openai", side_effect=self._fake_parsed_receipt):
            start_response = self.client.post(
                "/dashboard/receipt-modal/start",
                data={
                    "files": [(io.BytesIO(b"fake-image"), "20260318_1230_23500원_스타벅스.jpg")],
                },
                content_type="multipart/form-data",
            )
            job_id = start_response.get_json()["job"]["job_id"]
            payload = self._wait_for_job(job_id)
        item = payload["job"]["items"][0]

        response = self.client.post(
            f"/dashboard/receipt-modal/jobs/{job_id}/create",
            data={
                "bank_account_link_id": str(self.account_id),
                "items_json": json.dumps(
                    [
                        {
                            "item_id": item["item_id"],
                            "filename": item["filename"],
                            "occurred_on": "2026-03-18",
                            "occurred_time": "12:30",
                            "amount_krw": "23500",
                            "counterparty": "스타벅스",
                            "payment_item": "아메리카노",
                            "payment_method": "카드 ****4321",
                            "memo": "회의 전 커피",
                            "usage": "business",
                        }
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["selected_account_label"], "토스 주계좌")
        self.assertEqual(result["job"]["items"][0]["status"], "created")

        with self.app.app_context():
            tx = Transaction.query.filter_by(user_pk=self.user_pk).one()
            label = ExpenseLabel.query.filter_by(user_pk=self.user_pk, transaction_id=tx.id).one()
            evidence = EvidenceItem.query.filter_by(user_pk=self.user_pk, transaction_id=tx.id).one()

            self.assertEqual(tx.direction, "out")
            self.assertEqual(tx.amount_krw, 23500)
            self.assertEqual(tx.source, "receipt_modal")
            self.assertIn("[선택 계좌: 토스 주계좌]", tx.memo or "")
            self.assertIn("[결제 항목: 아메리카노]", tx.memo or "")
            self.assertIn("[결제 수단: 카드 ****4321]", tx.memo or "")
            self.assertEqual(label.status, "business")
            self.assertEqual(evidence.status, "attached")
            self.assertEqual(evidence.requirement, "required")
            self.assertEqual(evidence.original_filename, "20260318_1230_23500원_스타벅스.jpg")
            self.assertTrue((Path(self.tmpdir.name) / evidence.file_key).exists())

    def test_create_succeeds_without_account_selection(self) -> None:
        self._login()
        with patch("services.receipt_modal._parse_receipt_file_with_openai", side_effect=self._fake_parsed_receipt):
            start_response = self.client.post(
                "/dashboard/receipt-modal/start",
                data={
                    "files": [(io.BytesIO(b"fake-image"), "receipt_plain.png")],
                },
                content_type="multipart/form-data",
            )
            job_id = start_response.get_json()["job"]["job_id"]
            payload = self._wait_for_job(job_id)
        item = payload["job"]["items"][0]

        response = self.client.post(
            f"/dashboard/receipt-modal/jobs/{job_id}/create",
            data={
                "items_json": json.dumps(
                    [
                        {
                            "item_id": item["item_id"],
                            "filename": item["filename"],
                            "occurred_on": "2026-03-18",
                            "occurred_time": "09:10",
                            "amount_krw": "8900",
                            "counterparty": "편의점",
                            "payment_item": "알수없음",
                            "payment_method": "",
                            "memo": "",
                            "usage": "unknown",
                        }
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["created_count"], 1)
        self.assertIsNone(result["selected_account_label"])

        with self.app.app_context():
            tx = Transaction.query.filter_by(user_pk=self.user_pk).one()
            label = ExpenseLabel.query.filter_by(user_pk=self.user_pk, transaction_id=tx.id).one()
            evidence = EvidenceItem.query.filter_by(user_pk=self.user_pk, transaction_id=tx.id).one()

            self.assertNotIn("[선택 계좌:", tx.memo or "")
            self.assertEqual(label.status, "unknown")
            self.assertEqual(evidence.requirement, "maybe")
            self.assertEqual(evidence.status, "attached")


if __name__ == "__main__":
    unittest.main()

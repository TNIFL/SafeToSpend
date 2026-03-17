from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from routes.api.billing_webhook import api_billing_bp


class BillingWebhookApiTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(api_billing_bp)
        self.client = app.test_client()

    def test_webhook_received(self) -> None:
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_1", "paymentKey": "pay_1"}
        with patch(
            "routes.api.billing_webhook.ingest_payment_event",
            return_value={"ok": True, "duplicate": False, "status": "received", "payment_event_id": 1},
        ) as mocked:
            resp = self.client.post("/api/billing/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "received")
        mocked.assert_called_once()

    def test_webhook_duplicate(self) -> None:
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_1"}
        with patch(
            "routes.api.billing_webhook.ingest_payment_event",
            return_value={"ok": True, "duplicate": True, "status": "ignored_duplicate", "payment_event_id": 1},
        ):
            resp = self.client.post("/api/billing/webhook", json=payload)
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["status"], "ignored_duplicate")

    def test_webhook_triggers_reconcile_when_needed(self) -> None:
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_9", "paymentKey": "pay_9"}
        with (
            patch(
                "routes.api.billing_webhook.ingest_payment_event",
                return_value={
                    "ok": True,
                    "duplicate": False,
                    "status": "received",
                    "payment_event_id": 9,
                    "needs_reconcile": True,
                },
            ),
            patch(
                "routes.api.billing_webhook.reconcile_payment_from_event",
                return_value={"status_after": "reconciled", "reconciled": True},
            ) as reconcile_mock,
        ):
            resp = self.client.post("/api/billing/webhook", json=payload)
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["status"], "reconciled")
        self.assertTrue(body["reconciled"])
        reconcile_mock.assert_called_once()

    def test_webhook_rejects_non_json(self) -> None:
        resp = self.client.post("/api/billing/webhook", data="not-json", content_type="text/plain")
        self.assertEqual(resp.status_code, 400)

    def test_webhook_returns_503_on_store_failure(self) -> None:
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_2"}
        with patch("routes.api.billing_webhook.ingest_payment_event", side_effect=RuntimeError("boom")):
            resp = self.client.post("/api/billing/webhook", json=payload)
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()

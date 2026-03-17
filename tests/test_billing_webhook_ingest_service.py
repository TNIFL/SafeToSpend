from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from services.billing.service import ingest_payment_event


class BillingWebhookIngestServiceTest(unittest.TestCase):
    def test_duplicate_by_transmission_id(self) -> None:
        existing = SimpleNamespace(id=7)

        def _filter_by(**kwargs):
            q = MagicMock()
            if kwargs.get("transmission_id") == "tx_1":
                q.first.return_value = existing
            else:
                q.first.return_value = None
            return q

        with (
            patch("services.billing.service.PaymentEvent") as event_cls,
            patch("services.billing.service.db.session.add") as add_mock,
            patch("services.billing.service.db.session.commit") as commit_mock,
        ):
            event_cls.query.filter_by.side_effect = _filter_by
            result = ingest_payment_event(
                payload={"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_1"},
                transmission_id="tx_1",
            )

        self.assertTrue(result["duplicate"])
        self.assertEqual(result["status"], "ignored_duplicate")
        add_mock.assert_not_called()
        commit_mock.assert_not_called()

    def test_insert_new_event(self) -> None:
        def _filter_by(**_kwargs):
            q = MagicMock()
            q.first.return_value = None
            return q

        with (
            patch("services.billing.service.PaymentEvent") as event_cls,
            patch("services.billing.service.db.session.add") as add_mock,
            patch("services.billing.service.db.session.commit") as commit_mock,
        ):
            event_cls.query.filter_by.side_effect = _filter_by
            event_cls.return_value = SimpleNamespace(id=42)
            result = ingest_payment_event(
                payload={"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_2", "paymentKey": "pay_2"},
                transmission_id="tx_2",
            )

        self.assertFalse(result["duplicate"])
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["payment_event_id"], 42)
        add_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_integrity_error_returns_duplicate(self) -> None:
        existing = SimpleNamespace(id=99)
        responses = [None, None, existing]

        def _filter_by(**_kwargs):
            q = MagicMock()
            q.first.return_value = responses.pop(0)
            return q

        with (
            patch("services.billing.service.PaymentEvent") as event_cls,
            patch("services.billing.service.db.session.add"),
            patch(
                "services.billing.service.db.session.commit",
                side_effect=IntegrityError("insert", {}, Exception("dup")),
            ) as commit_mock,
            patch("services.billing.service.db.session.rollback") as rollback_mock,
        ):
            event_cls.query.filter_by.side_effect = _filter_by
            event_cls.return_value = SimpleNamespace(id=10)
            result = ingest_payment_event(
                payload={"eventType": "PAYMENT_STATUS_CHANGED", "orderId": "ord_3"},
                transmission_id="tx_3",
            )

        self.assertTrue(result["duplicate"])
        self.assertEqual(result["payment_event_id"], 99)
        commit_mock.assert_called_once()
        rollback_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

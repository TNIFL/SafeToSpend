from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.receipt_modal import ReceiptModalJobItem, _call_openai_receipt_parser


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class ReceiptModalServiceTest(unittest.TestCase):
    def test_call_openai_receipt_parser_uses_env_and_normalizes_fields(self) -> None:
        item = ReceiptModalJobItem(
            item_id="item-1",
            client_index=0,
            filename="receipt.heic",
            mime_type="image/heic",
            size_bytes=1234,
            stored_path="/tmp/fake.heic",
        )
        response_payload = {
            "output_text": """
            {
              "counterparty": "스타벅스 광화문",
              "occurred_on": "2026-03-18",
              "occurred_time": "14:32",
              "amount_krw": "12,300원",
              "payment_item": "카페 라떼",
              "payment_method": "카드 1234",
              "memo": "테이크아웃",
              "warnings": ["영수증 일부가 흐릴 수 있습니다."]
            }
            """
        }

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-4.1-mini"}, clear=False),
            patch("services.receipt_modal._prepare_image_data_url", return_value="data:image/jpeg;base64,ZmFrZQ=="),
            patch("services.receipt_modal.requests.post", return_value=_FakeResponse(response_payload)) as post_mock,
        ):
            parsed = _call_openai_receipt_parser(item)

        self.assertEqual(parsed["counterparty"], "스타벅스 광화문")
        self.assertEqual(parsed["occurred_on"], "2026-03-18")
        self.assertEqual(parsed["occurred_time"], "14:32")
        self.assertEqual(parsed["amount_krw"], 12300)
        self.assertEqual(parsed["payment_item"], "카페 라떼")
        self.assertEqual(parsed["payment_method"], "카드 ****1234")
        self.assertEqual(parsed["memo"], "테이크아웃")
        self.assertIn("영수증 일부가 흐릴 수 있습니다.", parsed["warnings"])

        self.assertTrue(post_mock.called)
        _, kwargs = post_mock.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["model"], "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()

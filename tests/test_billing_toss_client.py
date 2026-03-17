from __future__ import annotations

import unittest
from unittest.mock import patch

from services.billing.toss_client import (
    TossBillingApiError,
    TossBillingConfigError,
    build_billing_key_cipher,
    build_billing_key_cipher_for_version,
    build_registration_payload,
    get_active_billing_key_version,
    get_billing_key_secret,
    issue_billing_key,
)


class BillingTossClientTest(unittest.TestCase):
    def test_build_registration_payload_success(self) -> None:
        with patch.dict("os.environ", {"TOSS_PAYMENTS_CLIENT_KEY": "test_ck"}, clear=False):
            payload = build_registration_payload(
                customer_key="cust_test",
                success_url="https://example.com/success",
                fail_url="https://example.com/fail",
            )
        self.assertEqual(payload.client_key, "test_ck")
        self.assertEqual(payload.customer_key, "cust_test")
        self.assertEqual(payload.success_url, "https://example.com/success")
        self.assertEqual(payload.fail_url, "https://example.com/fail")

    def test_build_registration_payload_requires_customer_key(self) -> None:
        with patch.dict("os.environ", {"TOSS_PAYMENTS_CLIENT_KEY": "test_ck"}, clear=False):
            with self.assertRaises(TossBillingConfigError):
                build_registration_payload(
                    customer_key="",
                    success_url="https://example.com/success",
                    fail_url="https://example.com/fail",
                )

    def test_issue_billing_key_missing_params(self) -> None:
        with self.assertRaises(TossBillingApiError) as ctx:
            issue_billing_key(auth_key="", customer_key="")
        self.assertEqual(ctx.exception.code, "missing_params")

    def test_billing_key_cipher_roundtrip(self) -> None:
        with patch.dict("os.environ", {"BILLING_KEY_ENCRYPTION_SECRET": "0123456789abcdef0123456789abcdef"}, clear=False):
            cipher = build_billing_key_cipher()
        token = cipher.encrypt("billing-key-123")
        self.assertNotEqual(token, "billing-key-123")
        plain = cipher.decrypt(token)
        self.assertEqual(plain, "billing-key-123")

    def test_billing_key_cipher_tamper_detected(self) -> None:
        with patch.dict("os.environ", {"BILLING_KEY_ENCRYPTION_SECRET": "fedcba9876543210fedcba9876543210"}, clear=False):
            cipher = build_billing_key_cipher()
        token = cipher.encrypt("billing-key-xyz")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(TossBillingConfigError):
            cipher.decrypt(tampered)

    def test_versioned_secret_resolution(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "BILLING_KEY_ACTIVE_VERSION": "v2",
                "BILLING_KEY_ENCRYPTION_SECRET_V2": "v2-secret-0123456789abcdef",
            },
            clear=False,
        ):
            self.assertEqual(get_active_billing_key_version(), "v2")
            self.assertEqual(get_billing_key_secret(), "v2-secret-0123456789abcdef")
            cipher = build_billing_key_cipher_for_version("v2")
            token = cipher.encrypt("billing-key-v2")
            self.assertEqual(cipher.decrypt(token), "billing-key-v2")


if __name__ == "__main__":
    unittest.main()

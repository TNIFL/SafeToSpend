from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from domain.models import BankAccountLink, UserBankAccount
from services.import_popbill import _redacted_account_reference, _resolve_live_account_number
from services.privacy_guards import (
    hash_sensitive_identifier,
    is_disallowed_identifier_storage,
    mask_bank_identifier,
    redact_identifier_for_render,
    sanitize_account_like_value,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_bank_module():
    module_name = "test_web_bank_module"
    path = ROOT / "routes/web/bank.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class BankIdentifierGuardsTest(unittest.TestCase):
    def test_mask_bank_identifier_keeps_only_last4(self) -> None:
        self.assertEqual(mask_bank_identifier("123-456-789012"), "****9012")
        self.assertEqual(redact_identifier_for_render("9876543210"), "****3210")

    def test_sanitize_account_like_value_returns_hash_mask_and_token(self) -> None:
        result = sanitize_account_like_value("123-456-789012")
        self.assertEqual(result.normalized_digits, "123456789012")
        self.assertEqual(result.last4, "9012")
        self.assertEqual(result.masked, "****9012")
        self.assertEqual(result.hashed, hash_sensitive_identifier("123456789012"))
        self.assertTrue(result.storage_token.startswith("acct_"))

    def test_is_disallowed_identifier_storage_blocks_raw_but_allows_token(self) -> None:
        self.assertTrue(is_disallowed_identifier_storage("123456789012"))
        self.assertFalse(is_disallowed_identifier_storage("****9012"))
        self.assertFalse(is_disallowed_identifier_storage("acct_1234567890abcdef12345678"))

    def test_import_popbill_redacts_account_reference(self) -> None:
        self.assertEqual(_redacted_account_reference("0004", "123456789012"), "0004-****9012")

    def test_import_popbill_resolves_live_account_number_from_fingerprint(self) -> None:
        link = BankAccountLink(user_pk=1, bank_code="0004", account_number="acct_abcdef", bank_account_id=3)
        account = UserBankAccount(
            id=3,
            user_pk=1,
            bank_code="0004",
            account_fingerprint="fp-demo",
            account_last4="9012",
        )
        resolved = _resolve_live_account_number(
            link,
            accounts_by_id={3: account},
            live_accounts_by_fingerprint={("0004", "fp-demo"): "123456789012"},
        )
        self.assertEqual(resolved, "123456789012")

    def test_import_popbill_falls_back_to_legacy_raw_only_for_legacy_rows(self) -> None:
        link = BankAccountLink(user_pk=1, bank_code="0004", account_number="123456789012", bank_account_id=None)
        resolved = _resolve_live_account_number(
            link,
            accounts_by_id={},
            live_accounts_by_fingerprint={},
        )
        self.assertEqual(resolved, "123456789012")

    def test_bank_route_job_error_summary_masks_raw_account_number(self) -> None:
        bank_module = _load_bank_module()
        summary = bank_module._sanitize_job_error_summary(
            {"errors": [{"account": "0004-123456789012", "error": "boom"}]}
        )
        self.assertEqual(summary["errors"][0]["account"], "0004-****9012")


if __name__ == "__main__":
    unittest.main()

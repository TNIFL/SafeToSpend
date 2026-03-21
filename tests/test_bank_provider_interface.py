from __future__ import annotations

import unittest
from unittest.mock import patch

from app import create_app
from services.bank_provider import (
    BankProvider,
    BankProviderAccount,
    BankProviderConnectionStatus,
    BankProviderManagementLink,
    BankSyncResult,
    get_bank_provider,
)
from services.import_popbill import PopbillImportResult
from services.popbill_bank_provider import PopbillBankProvider


class _FakeBankProvider(BankProvider):
    def get_provider_name(self) -> str:
        return "fake"

    def get_provider_display_name(self) -> str:
        return "테스트 공급자"

    def get_connection_status(self) -> BankProviderConnectionStatus:
        return BankProviderConnectionStatus(configured=True)

    def list_accounts(self) -> list[BankProviderAccount]:
        return [
            BankProviderAccount(
                bank_code="0004",
                account_number="1234567890",
                account_name="테스트계좌",
            )
        ]

    def get_account_management_link(self) -> BankProviderManagementLink:
        return BankProviderManagementLink(url="https://example.com/manage", popup_width=1200, popup_height=700)

    def sync_transactions(self, *, user_pk: int, start=None, end=None) -> BankSyncResult:
        return BankSyncResult(
            import_job_id=91,
            total_rows=4,
            inserted_rows=3,
            duplicate_rows=1,
            failed_rows=0,
        )


class BankProviderInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_provider_entrypoint_returns_popbill_provider_by_default(self) -> None:
        provider = get_bank_provider()

        self.assertIsInstance(provider, PopbillBankProvider)
        self.assertIsInstance(provider, BankProvider)
        self.assertEqual(provider.get_provider_name(), "popbill")

    def test_popbill_provider_delegates_account_management_link_to_existing_wrapper(self) -> None:
        provider = PopbillBankProvider()

        with patch("services.popbill_bank_provider.get_bank_account_mgt_url", return_value="https://example.com/popbill"):
            link = provider.get_account_management_link()

        self.assertEqual(link.url, "https://example.com/popbill")
        self.assertEqual(link.popup_width, provider.popup_width)
        self.assertEqual(link.popup_height, provider.popup_height)

    def test_popbill_provider_sync_delegates_to_existing_import_service(self) -> None:
        provider = PopbillBankProvider()

        with patch(
            "services.popbill_bank_provider.sync_popbill_for_user",
            return_value=PopbillImportResult(
                import_job_id=17,
                total_rows=9,
                inserted_rows=5,
                duplicate_rows=3,
                failed_rows=1,
                errors=[{"error": "row parse error"}],
            ),
        ) as sync_mock:
            result = provider.sync_transactions(user_pk=7)

        sync_mock.assert_called_once_with(user_pk=7, start=None, end=None)
        self.assertEqual(result.import_job_id, 17)
        self.assertEqual(result.inserted_rows, 5)
        self.assertEqual(result.duplicate_rows, 3)
        self.assertEqual(result.failed_rows, 1)
        self.assertEqual(result.errors[0]["error"], "row parse error")

    def test_provider_url_route_uses_generic_provider_entrypoint(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = 7

        with patch("routes.web.bank.get_bank_provider", return_value=_FakeBankProvider()):
            response = self.client.get("/bank/provider-url")
            legacy_response = self.client.get("/bank/popbill-url")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(legacy_response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["provider_name"], "fake")
        self.assertEqual(data["provider_display_name"], "테스트 공급자")
        self.assertEqual(data["url"], "https://example.com/manage")

    def test_bank_index_renders_with_generic_provider_context(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = 7

        with patch("routes.web.bank.get_bank_provider", return_value=_FakeBankProvider()):
            response = self.client.get("/bank")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("테스트 공급자", body)
        self.assertIn("테스트 공급자에서 계좌 등록/관리", body)
        self.assertIn("테스트계좌", body)

    def test_sync_route_keeps_manual_sync_flow_with_generic_provider(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = 7

        with patch("routes.web.bank.get_bank_provider", return_value=_FakeBankProvider()):
            response = self.client.post("/bank/sync", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/bank"))


if __name__ == "__main__":
    unittest.main()

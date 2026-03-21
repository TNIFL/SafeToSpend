from __future__ import annotations

from datetime import date

from services.bank_provider import (
    BankProvider,
    BankProviderAccount,
    BankProviderConfigError,
    BankProviderConnectionStatus,
    BankProviderError,
    BankProviderManagementLink,
    BankProviderSyncError,
    BankSyncResult,
)
from services.import_popbill import PopbillImportError, sync_popbill_for_user
from services.popbill_easyfinbank import (
    PopbillApiError,
    PopbillConfigError,
    get_bank_account_mgt_url,
    get_config,
    list_bank_accounts,
)


class PopbillBankProvider(BankProvider):
    popup_width = 1550
    popup_height = 680

    def get_provider_name(self) -> str:
        return "popbill"

    def get_provider_display_name(self) -> str:
        return "팝빌"

    def get_connection_status(self) -> BankProviderConnectionStatus:
        try:
            get_config()
            return BankProviderConnectionStatus(configured=True)
        except PopbillConfigError as exc:
            return BankProviderConnectionStatus(configured=False, error_message=str(exc))
        except Exception as exc:
            return BankProviderConnectionStatus(configured=False, error_message=f"알 수 없는 오류: {exc}")

    def list_accounts(self) -> list[BankProviderAccount]:
        try:
            accounts = list_bank_accounts()
        except PopbillConfigError as exc:
            raise BankProviderConfigError(str(exc)) from exc
        except PopbillApiError as exc:
            raise BankProviderError(str(exc)) from exc
        except Exception as exc:
            raise BankProviderError(f"알 수 없는 오류: {exc}") from exc

        return [
            BankProviderAccount(
                bank_code=str(getattr(account, "bankCode", "") or "").strip(),
                account_number=str(getattr(account, "accountNumber", "") or "").strip(),
                account_name=str(getattr(account, "accountName", "") or "").strip() or None,
            )
            for account in accounts
        ]

    def get_account_management_link(self) -> BankProviderManagementLink:
        try:
            url = get_bank_account_mgt_url()
        except PopbillConfigError as exc:
            raise BankProviderConfigError(str(exc)) from exc
        except PopbillApiError as exc:
            raise BankProviderError(str(exc)) from exc
        except Exception as exc:
            raise BankProviderError(f"알 수 없는 오류: {exc}") from exc

        return BankProviderManagementLink(
            url=url,
            popup_width=self.popup_width,
            popup_height=self.popup_height,
        )

    def sync_transactions(
        self,
        *,
        user_pk: int,
        start: date | None = None,
        end: date | None = None,
    ) -> BankSyncResult:
        try:
            result = sync_popbill_for_user(user_pk=user_pk, start=start, end=end)
        except PopbillConfigError as exc:
            raise BankProviderConfigError(str(exc)) from exc
        except PopbillImportError as exc:
            raise BankProviderSyncError(str(exc)) from exc
        except PopbillApiError as exc:
            raise BankProviderSyncError(str(exc)) from exc
        except Exception as exc:
            raise BankProviderSyncError(f"알 수 없는 오류: {exc}") from exc

        return BankSyncResult(
            import_job_id=int(result.import_job_id),
            total_rows=int(result.total_rows),
            inserted_rows=int(result.inserted_rows),
            duplicate_rows=int(result.duplicate_rows),
            failed_rows=int(result.failed_rows),
            errors=tuple(result.errors),
        )

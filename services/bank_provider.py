from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


DEFAULT_BANK_PROVIDER = "popbill"
SUPPORTED_BANK_PROVIDERS = (DEFAULT_BANK_PROVIDER,)


@dataclass(frozen=True)
class BankProviderConnectionStatus:
    configured: bool
    error_message: str | None = None


@dataclass(frozen=True)
class BankProviderAccount:
    bank_code: str
    account_number: str
    account_name: str | None = None


@dataclass(frozen=True)
class BankProviderManagementLink:
    url: str
    popup_width: int | None = None
    popup_height: int | None = None


@dataclass(frozen=True)
class BankSyncResult:
    import_job_id: int
    total_rows: int
    inserted_rows: int
    duplicate_rows: int
    failed_rows: int
    errors: tuple[dict, ...] = ()


class BankProviderError(RuntimeError):
    pass


class BankProviderConfigError(BankProviderError):
    pass


class BankProviderSyncError(BankProviderError):
    pass


class BankProvider(ABC):
    @abstractmethod
    def get_provider_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_provider_display_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_connection_status(self) -> BankProviderConnectionStatus:
        raise NotImplementedError

    @abstractmethod
    def list_accounts(self) -> list[BankProviderAccount]:
        raise NotImplementedError

    @abstractmethod
    def get_account_management_link(self) -> BankProviderManagementLink:
        raise NotImplementedError

    @abstractmethod
    def sync_transactions(
        self,
        *,
        user_pk: int,
        start: date | None = None,
        end: date | None = None,
    ) -> BankSyncResult:
        raise NotImplementedError


def normalize_bank_provider_name(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return DEFAULT_BANK_PROVIDER
    if raw in SUPPORTED_BANK_PROVIDERS:
        return raw
    raise BankProviderConfigError(f"지원하지 않는 BANK_PROVIDER입니다: {raw}")


def get_bank_provider(provider_name: str | None = None) -> BankProvider:
    selected = normalize_bank_provider_name(provider_name or os.getenv("BANK_PROVIDER"))
    if selected == "popbill":
        from services.popbill_bank_provider import PopbillBankProvider

        return PopbillBankProvider()
    raise BankProviderConfigError(f"지원하지 않는 BANK_PROVIDER입니다: {selected}")

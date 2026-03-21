from __future__ import annotations

TX_SOURCE_MANUAL = "manual"
TX_SOURCE_CSV = "csv"
TX_SOURCE_RECEIPT_MODAL = "receipt_modal"
TX_SOURCE_BANK_SYNC = "bank_sync"

TX_PROVIDER_POPBILL = "popbill"
TX_PROVIDER_BANKDA = "bankda"

KNOWN_BANK_PROVIDERS = {
    TX_PROVIDER_POPBILL,
    TX_PROVIDER_BANKDA,
}

SOURCE_DISPLAY_LABELS = {
    TX_SOURCE_MANUAL: "수동입력",
    TX_SOURCE_CSV: "수동업로드",
    TX_SOURCE_RECEIPT_MODAL: "영수증등록",
    TX_SOURCE_BANK_SYNC: "자동연동",
}

PROVIDER_DISPLAY_LABELS = {
    TX_PROVIDER_POPBILL: "팝빌",
    TX_PROVIDER_BANKDA: "뱅크다",
}


def _normalize(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    return raw or None


def resolve_transaction_origin(source: str | None, provider: str | None = None) -> tuple[str | None, str | None]:
    normalized_source = _normalize(source)
    normalized_provider = _normalize(provider)

    if normalized_source in KNOWN_BANK_PROVIDERS and not normalized_provider:
        return TX_SOURCE_BANK_SYNC, normalized_source

    return normalized_source, normalized_provider


def get_transaction_source_label(source: str | None, provider: str | None = None) -> str:
    normalized_source, _ = resolve_transaction_origin(source, provider)
    if normalized_source:
        return SOURCE_DISPLAY_LABELS.get(normalized_source, "기타")
    return "기타"


def get_transaction_provider_label(source: str | None, provider: str | None = None) -> str:
    normalized_source, normalized_provider = resolve_transaction_origin(source, provider)
    if normalized_source != TX_SOURCE_BANK_SYNC:
        return "없음"
    if normalized_provider:
        return PROVIDER_DISPLAY_LABELS.get(normalized_provider, normalized_provider)
    return "확인 전"


def get_transaction_badge_label(source: str | None, provider: str | None = None) -> str:
    return get_transaction_source_label(source, provider)


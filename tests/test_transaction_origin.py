from __future__ import annotations

import unittest

from services.transaction_origin import (
    TX_PROVIDER_POPBILL,
    TX_SOURCE_BANK_SYNC,
    get_transaction_badge_label,
    get_transaction_provider_label,
    get_transaction_source_label,
    resolve_transaction_origin,
)


class TransactionOriginTest(unittest.TestCase):
    def test_resolve_legacy_popbill_source_to_bank_sync_with_provider(self) -> None:
        self.assertEqual(
            resolve_transaction_origin("popbill", None),
            (TX_SOURCE_BANK_SYNC, TX_PROVIDER_POPBILL),
        )

    def test_resolve_new_bank_sync_shape_keeps_provider(self) -> None:
        self.assertEqual(
            resolve_transaction_origin("bank_sync", "popbill"),
            (TX_SOURCE_BANK_SYNC, TX_PROVIDER_POPBILL),
        )

    def test_source_and_provider_labels_are_provider_aware(self) -> None:
        self.assertEqual(get_transaction_source_label("bank_sync", "popbill"), "자동연동")
        self.assertEqual(get_transaction_provider_label("bank_sync", "popbill"), "팝빌")
        self.assertEqual(get_transaction_badge_label("bank_sync", "popbill"), "자동연동")

    def test_non_bank_sources_keep_provider_empty(self) -> None:
        self.assertEqual(get_transaction_source_label("manual", None), "수동입력")
        self.assertEqual(get_transaction_provider_label("manual", None), "없음")


if __name__ == "__main__":
    unittest.main()

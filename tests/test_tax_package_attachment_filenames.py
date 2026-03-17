from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from services.tax_package import _build_attachment_export_filename


class TaxPackageAttachmentFilenameTest(unittest.TestCase):
    def _tx(
        self,
        *,
        occurred_at: datetime | None = None,
        amount_krw: int | None = 15_800,
        counterparty: str | None = "스타벅스 역삼점",
        memo: str | None = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            occurred_at=occurred_at,
            amount_krw=amount_krw,
            counterparty=counterparty,
            memo=memo,
        )

    def _ev(
        self,
        *,
        original_filename: str | None = "receipt.jpg",
        mime_type: str | None = "image/jpeg",
        note: str | None = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            original_filename=original_filename,
            mime_type=mime_type,
            note=note,
        )

    def test_normal_filename_uses_transaction_context(self) -> None:
        tx = self._tx(occurred_at=datetime(2026, 3, 14, 13, 24, 55))
        ev = self._ev(original_filename="receipt.jpg")
        name = _build_attachment_export_filename(tx=tx, ev=ev, sequence=1)
        self.assertEqual(name, "20260314_132455_15800원_스타벅스역삼점_영수증_001.jpg")

    def test_missing_time_uses_time_unknown_fallback(self) -> None:
        tx = self._tx(occurred_at=None)
        ev = self._ev(original_filename="evidence.png")
        fixed_now = datetime(2026, 1, 2, 3, 4, 5)
        with patch("services.tax_package.utcnow", return_value=fixed_now):
            name = _build_attachment_export_filename(tx=tx, ev=ev, sequence=1)
        self.assertTrue(name.startswith("20260102_시간미상_15800원_스타벅스역삼점_증빙_001."))

    def test_missing_amount_uses_amount_unknown_fallback(self) -> None:
        tx = self._tx(occurred_at=datetime(2026, 3, 14, 13, 24, 55), amount_krw=None)
        ev = self._ev()
        name = _build_attachment_export_filename(tx=tx, ev=ev, sequence=1)
        self.assertIn("_금액미상_", name)

    def test_missing_counterparty_falls_back_to_memo_then_unknown(self) -> None:
        tx_memo = self._tx(
            occurred_at=datetime(2026, 3, 14, 13, 24, 55),
            counterparty="",
            memo="점심 / 결제",
        )
        ev = self._ev()
        name_with_memo = _build_attachment_export_filename(tx=tx_memo, ev=ev, sequence=1)
        self.assertIn("_점심결제_", name_with_memo)

        tx_unknown = self._tx(
            occurred_at=datetime(2026, 3, 14, 13, 24, 55),
            counterparty="",
            memo="",
        )
        name_unknown = _build_attachment_export_filename(tx=tx_unknown, ev=ev, sequence=1)
        self.assertIn("_거래처미상_", name_unknown)

    def test_special_characters_are_removed_and_extension_is_kept(self) -> None:
        tx = self._tx(
            occurred_at=datetime(2026, 3, 14, 13, 24, 55),
            counterparty='A/B:C*D?E"F<G>H|I',
        )
        ev = self._ev(original_filename="weird.name.jpeg")
        name = _build_attachment_export_filename(tx=tx, ev=ev, sequence=1)
        self.assertNotRegex(name, r'[\\/:*?"<>|]')
        self.assertTrue(name.endswith(".jpeg"))

    def test_extension_falls_back_to_mime_type(self) -> None:
        tx = self._tx(occurred_at=datetime(2026, 3, 14, 13, 24, 55))
        ev = self._ev(original_filename=None, mime_type="application/pdf")
        name = _build_attachment_export_filename(tx=tx, ev=ev, sequence=1)
        self.assertTrue(name.endswith(".pdf"))

    def test_sequence_suffix_changes_for_multiple_attachments(self) -> None:
        tx = self._tx(occurred_at=datetime(2026, 3, 14, 13, 24, 55))
        ev = self._ev()
        name_1 = _build_attachment_export_filename(tx=tx, ev=ev, sequence=1)
        name_2 = _build_attachment_export_filename(tx=tx, ev=ev, sequence=2)
        self.assertTrue(name_1.endswith("_001.jpg"))
        self.assertTrue(name_2.endswith("_002.jpg"))


if __name__ == "__main__":
    unittest.main()

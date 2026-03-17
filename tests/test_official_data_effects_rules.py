from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from services.official_data_effects import (
    collect_official_data_effects_for_user,
    compute_nhis_official_effects,
    compute_tax_official_effects,
)


class OfficialDataEffectsRulesTest(unittest.TestCase):
    def _doc(
        self,
        *,
        doc_id: int,
        source_system: str,
        document_type: str,
        parse_status: str = "parsed",
        reference_date: date | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        payload: dict | None = None,
        parsed_at: datetime | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=doc_id,
            source_system=source_system,
            document_type=document_type,
            display_name=document_type,
            parse_status=parse_status,
            parser_version="official_data_parser_v1",
            extracted_payload_json=dict(payload or {}),
            extracted_key_summary_json={},
            verified_reference_date=reference_date,
            document_period_start=period_start,
            document_period_end=period_end,
            parsed_at=parsed_at or datetime(2026, 3, 10, 9, 0, 0),
        )

    def test_parsed_withholding_document_is_applied(self) -> None:
        doc = self._doc(
            doc_id=1,
            source_system="hometax",
            document_type="hometax_withholding_statement",
            reference_date=date(2026, 3, 1),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            payload={"total_withheld_tax_krw": 840_000, "verified_reference_date": "2026-03-01"},
        )
        effects = compute_tax_official_effects([doc], month_key="2026-03", today=date(2026, 3, 16))
        self.assertEqual(effects.verified_withholding_tax_krw, 840_000)
        self.assertTrue(effects.verified_withholding_applied)
        self.assertEqual(effects.priority_source, "official_verified_snapshot")
        self.assertEqual(len(effects.applied_documents), 1)

    def test_needs_review_and_unsupported_are_ignored(self) -> None:
        docs = [
            self._doc(doc_id=1, source_system="hometax", document_type="hometax_withholding_statement", parse_status="needs_review"),
            self._doc(doc_id=2, source_system="nhis", document_type="nhis_payment_confirmation", parse_status="unsupported"),
        ]
        tax_effects = compute_tax_official_effects(docs, today=date(2026, 3, 16))
        nhis_effects = compute_nhis_official_effects(docs, today=date(2026, 3, 16))
        self.assertEqual(tax_effects.verified_withholding_tax_krw, 0)
        self.assertEqual(nhis_effects.verified_nhis_paid_amount_krw, 0)
        self.assertGreaterEqual(len(tax_effects.ignored_documents), 1)
        self.assertGreaterEqual(len(nhis_effects.ignored_documents), 1)

    def test_stale_documents_are_not_applied(self) -> None:
        doc = self._doc(
            doc_id=3,
            source_system="hometax",
            document_type="hometax_withholding_statement",
            reference_date=date(2025, 7, 1),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            payload={"total_withheld_tax_krw": 500_000, "verified_reference_date": "2025-07-01"},
        )
        effects = compute_tax_official_effects([doc], month_key="2026-03", today=date(2026, 3, 16))
        self.assertFalse(effects.verified_withholding_applied)
        self.assertEqual(len(effects.stale_documents), 1)

    def test_nhis_effects_are_reference_only_and_conservative(self) -> None:
        doc = self._doc(
            doc_id=4,
            source_system="nhis",
            document_type="nhis_payment_confirmation",
            reference_date=date(2026, 3, 2),
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            payload={"total_paid_amount_krw": 321_000, "verified_reference_date": "2026-03-02"},
        )
        effects = compute_nhis_official_effects([doc], month_key="2026-03", today=date(2026, 3, 16))
        self.assertTrue(effects.nhis_official_data_applied)
        self.assertEqual(effects.verified_nhis_paid_amount_krw, 321_000)
        self.assertEqual(effects.nhis_official_status_label, "공식 자료 기준 확인")

    def test_newer_manual_input_beats_official_tax_snapshot(self) -> None:
        doc = self._doc(
            doc_id=5,
            source_system="hometax",
            document_type="hometax_withholding_statement",
            reference_date=date(2026, 3, 1),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            payload={"total_withheld_tax_krw": 720_000, "verified_reference_date": "2026-03-01"},
            parsed_at=datetime(2026, 3, 1, 9, 0, 0),
        )
        effects = compute_tax_official_effects(
            [doc],
            month_key="2026-03",
            today=date(2026, 3, 16),
            profile_json={"withheld_tax_annual_krw": 900_000},
            profile_updated_at=datetime(2026, 3, 5, 12, 0, 0),
        )
        self.assertTrue(effects.manual_override_wins)
        self.assertFalse(effects.verified_withholding_applied)
        self.assertEqual(effects.priority_source, "manual_newer_than_official")


if __name__ == "__main__":
    unittest.main()

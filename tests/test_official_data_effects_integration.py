from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.official_data_effects import OfficialDataEffectsBundle, OfficialNhisEffects, OfficialTaxEffects
from services.receipt_tax_effects import ReceiptTaxEffectsSummary
from services.risk import compute_tax_estimate
from services.nhis_runtime import compute_nhis_monthly_buffer


class _QueryStub:
    def __init__(self, *, rows=None, scalar_value=None):
        self._rows = list(rows or [])
        self._scalar_value = scalar_value

    def select_from(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value


class OfficialDataEffectsIntegrationTest(unittest.TestCase):
    def _zero_receipts(self) -> ReceiptTaxEffectsSummary:
        return ReceiptTaxEffectsSummary(
            reflected_expense_krw=0,
            pending_review_expense_krw=0,
            excluded_expense_krw=0,
            consult_tax_review_expense_krw=0,
            reflected_transaction_count=0,
            pending_transaction_count=0,
            excluded_transaction_count=0,
            consult_tax_review_transaction_count=0,
            skipped_manual_transaction_count=0,
            evaluated_transaction_count=0,
            entries=(),
        )

    def _effects_bundle(self, *, withholding: int = 0, tax_ref: str | None = None, nhis_amount: int = 0, nhis_ref: str | None = None, nhis_applied: bool = False, nhis_recheck: bool = False) -> OfficialDataEffectsBundle:
        tax = OfficialTaxEffects(
            verified_withholding_tax_krw=withholding,
            verified_paid_tax_krw=0,
            verified_tax_reference_date=(None if tax_ref is None else __import__('datetime').date.fromisoformat(tax_ref)),
            official_data_confidence_level=("high" if withholding else "low"),
            applied_documents=({"document_type": "hometax_withholding_statement"},) if withholding else (),
            ignored_documents=(),
            stale_documents=(),
            effect_messages=(("홈택스 공식 자료 기준으로 이미 빠진 세금을 보정했어요.",) if withholding else ()),
            reference_business_card_usage_krw=0,
            applied_withholding_document_id=(1 if withholding else None),
            applied_paid_tax_document_id=None,
            manual_override_wins=False,
            priority_source=("official_verified_snapshot" if withholding else "none"),
            verified_withholding_applied=bool(withholding),
            verified_paid_tax_applied=False,
        )
        nhis = OfficialNhisEffects(
            verified_nhis_paid_amount_krw=nhis_amount,
            verified_nhis_reference_date=(None if nhis_ref is None else __import__('datetime').date.fromisoformat(nhis_ref)),
            official_data_confidence_level=("high" if nhis_applied else "low"),
            applied_documents=({"document_type": "nhis_payment_confirmation"},) if nhis_applied else (),
            ignored_documents=(),
            stale_documents=(),
            effect_messages=(("건보료 공식 자료 기준일을 함께 표시하고 신뢰도 판단에 반영했어요.",) if nhis_applied else ()),
            nhis_official_status_label=("공식 자료 기준 확인" if nhis_applied else ("재확인 권장" if nhis_recheck else "공식 자료 없음")),
            nhis_official_data_applied=nhis_applied,
            nhis_recheck_recommended=nhis_recheck,
        )
        return OfficialDataEffectsBundle(
            verified_withholding_tax_krw=tax.verified_withholding_tax_krw,
            verified_paid_tax_krw=0,
            verified_tax_reference_date=tax.verified_tax_reference_date,
            verified_nhis_paid_amount_krw=nhis.verified_nhis_paid_amount_krw,
            verified_nhis_reference_date=nhis.verified_nhis_reference_date,
            official_data_confidence_level=("high" if withholding or nhis_applied else "low"),
            applied_documents=tuple([*tax.applied_documents, *nhis.applied_documents]),
            ignored_documents=(),
            stale_documents=(),
            effect_messages=tuple([*tax.effect_messages, *nhis.effect_messages]),
            tax=tax,
            nhis=nhis,
        )

    def test_tax_estimate_keeps_existing_values_without_official_data(self) -> None:
        with (
            patch("services.risk._get_settings", return_value=SimpleNamespace(default_tax_rate=0.15)),
            patch("services.risk.get_tax_profile", return_value={
                "official_taxable_income_annual_krw": 20_000_000,
                "annual_gross_income_krw": 50_000_000,
                "annual_deductible_expense_krw": 10_000_000,
                "income_classification": "business",
                "withholding_3_3": "no",
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
                "tax_advanced_input_confirmed": True,
            }),
            patch("services.risk.is_tax_profile_complete", return_value=True),
            patch("services.risk.pick_income_override_for_month", return_value={"applied": False, "entry": None, "target_year": 2025, "used_year": None}),
            patch("services.risk.aggregate_income_override", return_value={}),
            patch("services.risk.compute_receipt_tax_effects_for_month", return_value=self._zero_receipts()),
            patch("services.risk.collect_official_data_effects_for_user", return_value=self._effects_bundle()),
            patch(
                "services.risk.db.session.query",
                side_effect=[
                    _QueryStub(rows=[(2_000_000, "A", "정산", "income")]),
                    _QueryStub(scalar_value=300_000),
                    _QueryStub(scalar_value=0),
                ],
            ),
        ):
            est = compute_tax_estimate(user_pk=1, month_key="2026-03")
        self.assertFalse(est.official_data_applied)
        self.assertEqual(est.tax_delta_from_official_data_krw, 0)

    def test_tax_estimate_applies_verified_withholding_snapshot(self) -> None:
        with (
            patch("services.risk._get_settings", return_value=SimpleNamespace(default_tax_rate=0.15)),
            patch("services.risk.get_tax_profile", return_value={
                "official_taxable_income_annual_krw": 20_000_000,
                "annual_gross_income_krw": 50_000_000,
                "annual_deductible_expense_krw": 10_000_000,
                "income_classification": "business",
                "withholding_3_3": "no",
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
                "tax_advanced_input_confirmed": True,
            }),
            patch("services.risk.is_tax_profile_complete", return_value=True),
            patch("services.risk.pick_income_override_for_month", return_value={"applied": False, "entry": None, "target_year": 2025, "used_year": None}),
            patch("services.risk.aggregate_income_override", return_value={}),
            patch("services.risk.compute_receipt_tax_effects_for_month", return_value=self._zero_receipts()),
            patch("services.risk.collect_official_data_effects_for_user", return_value=self._effects_bundle(withholding=1_200_000, tax_ref="2026-03-01")),
            patch(
                "services.risk.db.session.query",
                side_effect=[
                    _QueryStub(rows=[(2_000_000, "A", "정산", "income")]),
                    _QueryStub(scalar_value=300_000),
                    _QueryStub(scalar_value=0),
                ],
            ),
        ):
            est = compute_tax_estimate(user_pk=1, month_key="2026-03")
        self.assertTrue(est.official_data_applied)
        self.assertEqual(est.official_verified_withholding_tax_krw, 1_200_000)
        self.assertEqual(str(est.official_tax_reference_date), "2026-03-01")
        self.assertLess(est.tax_due_est_krw, est.tax_due_before_official_adjustment_krw)
        self.assertNotEqual(est.tax_delta_from_official_data_krw, 0)

    def test_nhis_payload_exposes_official_reference_without_overwriting_amount(self) -> None:
        fake_status = SimpleNamespace(snapshot=None, is_stale=False, update_error="", is_fallback_default=False)
        with (
            patch("services.nhis_runtime.get_tax_profile", return_value={}),
            patch("services.nhis_runtime.get_monthly_health_insurance_buffer", return_value=(0, None)),
            patch("services.nhis_runtime.check_nhis_ready", return_value={"ready": True, "guard_warning": "", "guard_warnings": []}),
            patch("services.nhis_runtime.ensure_active_snapshot", return_value=fake_status),
            patch("services.nhis_runtime.snapshot_to_display_dict", return_value={}),
            patch("services.nhis_runtime.load_canonical_nhis_profile", return_value={"member_type": "regional", "annual_income_krw": 40_000_000, "non_salary_annual_income_krw": 0, "property_tax_base_total_krw": 100_000_000}),
            patch("services.nhis_runtime.estimate_nhis_monthly_dict", return_value={"member_type": "regional", "mode": "rules_regional", "confidence_level": "medium", "health_est_krw": 120_000, "ltc_est_krw": 10_000, "total_est_krw": 130_000, "notes": [], "warnings": [], "can_estimate": True}),
            patch("services.nhis_runtime.collect_official_data_effects_for_user", return_value=self._effects_bundle(nhis_amount=333_000, nhis_ref="2026-03-02", nhis_applied=True)),
        ):
            amount, _note, payload = compute_nhis_monthly_buffer(user_pk=1, month_key="2026-03")
        self.assertEqual(amount, 130_000)
        meta = dict(payload.get("result_meta") or {})
        self.assertTrue(meta.get("nhis_official_data_applied"))
        self.assertEqual(meta.get("nhis_official_paid_amount_krw"), 333_000)
        self.assertEqual(meta.get("nhis_official_reference_date"), "2026-03-02")


if __name__ == "__main__":
    unittest.main()

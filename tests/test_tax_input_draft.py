from __future__ import annotations

import unittest
from unittest.mock import patch

from services.onboarding import evaluate_tax_required_inputs
from services.tax_input_draft import build_tax_input_draft


class _QueryStub:
    def __init__(self, *, scalar_value=None, rows=None):
        self._scalar_value = scalar_value
        self._rows = list(rows or [])

    def select_from(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def scalar(self):
        return self._scalar_value

    def all(self):
        return list(self._rows)


class TaxInputDraftTest(unittest.TestCase):
    def test_builds_income_and_expense_draft_from_transactions(self) -> None:
        with (
            patch("services.tax_input_draft.get_tax_profile", return_value={}),
            patch(
                "services.tax_input_draft.db.session.query",
                side_effect=[
                    _QueryStub(scalar_value=120_000_000),
                    _QueryStub(scalar_value=30_000_000),
                    _QueryStub(rows=[]),
                ],
            ),
        ):
            out = build_tax_input_draft(user_pk=11)

        draft_values = dict(out.get("draft_values") or {})
        self.assertEqual(int(draft_values.get("annual_gross_income_krw") or 0), 120_000_000)
        self.assertEqual(int(draft_values.get("annual_deductible_expense_krw") or 0), 30_000_000)
        self.assertEqual(int(draft_values.get("prepaid_tax_annual_krw") or 0), 0)
        self.assertTrue(bool(out.get("requires_user_confirmation")))

    def test_draft_only_does_not_promote_accuracy_level(self) -> None:
        with (
            patch("services.tax_input_draft.get_tax_profile", return_value={}),
            patch(
                "services.tax_input_draft.db.session.query",
                side_effect=[
                    _QueryStub(scalar_value=80_000_000),
                    _QueryStub(scalar_value=20_000_000),
                    _QueryStub(rows=[]),
                ],
            ),
        ):
            draft = build_tax_input_draft(user_pk=17)

        profile_like = {
            "income_classification": "business",
            "annual_gross_income_krw": draft.get("draft_values", {}).get("annual_gross_income_krw"),
            "annual_deductible_expense_krw": draft.get("draft_values", {}).get("annual_deductible_expense_krw"),
            "withheld_tax_annual_krw": 0,
            "prepaid_tax_annual_krw": 0,
            "tax_basic_inputs_confirmed": False,
        }
        required = evaluate_tax_required_inputs(profile_like)
        self.assertFalse(bool(required.get("high_confidence_inputs_ready")))
        self.assertIn("tax_basic_inputs_confirmed", list(required.get("high_confidence_missing_fields") or []))

    def test_existing_user_values_are_not_overwritten_by_draft(self) -> None:
        profile = {
            "annual_gross_income_krw": 55_000_000,
            "annual_deductible_expense_krw": 10_000_000,
            "withheld_tax_annual_krw": 400_000,
            "prepaid_tax_annual_krw": 50_000,
            "income_classification": "business",
        }
        with (
            patch("services.tax_input_draft.get_tax_profile", return_value=profile),
            patch("services.tax_input_draft.db.session.query", side_effect=[_QueryStub(rows=[])]),
        ):
            out = build_tax_input_draft(user_pk=19)

        draft_values = dict(out.get("draft_values") or {})
        self.assertNotIn("annual_gross_income_krw", draft_values)
        self.assertNotIn("annual_deductible_expense_krw", draft_values)
        self.assertNotIn("withheld_tax_annual_krw", draft_values)


if __name__ == "__main__":
    unittest.main()

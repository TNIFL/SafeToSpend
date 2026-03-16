from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from services.receipt_expense_guidance import build_receipt_expense_inline_guidance
from services.receipt_expense_rules import (
    extract_follow_up_answers_from_form,
    save_receipt_follow_up_answers_and_re_evaluate,
)


ROOT = Path(__file__).resolve().parents[1]


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def in_(self, values):
        return ("in", self.name, tuple(values))


class _FakeAnswerModel:
    user_pk = _Field("user_pk")
    transaction_id = _Field("transaction_id")
    question_key = _Field("question_key")

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.answer_value = getattr(self, "answer_value", None)
        self.answer_text = getattr(self, "answer_text", None)
        self.answered_at = getattr(self, "answered_at", None)
        self.answered_by = getattr(self, "answered_by", None)
        self.updated_at = getattr(self, "updated_at", None)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []

    def filter(self, expression):
        self._filters.append(expression)
        return self

    def all(self):
        rows = list(self._rows)
        for operator, field_name, value in self._filters:
            if operator == "eq":
                rows = [row for row in rows if getattr(row, field_name, None) == value]
            elif operator == "in":
                rows = [row for row in rows if getattr(row, field_name, None) in value]
        return rows


class _FakeSession:
    def __init__(self):
        self.rows = []

    def query(self, model):
        return _FakeQuery(self.rows)

    def add(self, row):
        if row not in self.rows:
            self.rows.append(row)

    def flush(self):
        return None


def _tx(**kwargs):
    base = {
        "id": 11,
        "counterparty": "스타벅스",
        "memo": "거래처 미팅 커피",
        "amount_krw": 18000,
        "occurred_at": datetime(2026, 3, 12, 14, 30),
        "direction": "out",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class ReceiptExpenseFollowupIntegrationTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_extract_follow_up_answers_from_form(self) -> None:
        payload = extract_follow_up_answers_from_form(
            {
                "followup__business_meal_with_client__value": "yes",
                "followup__business_meal_with_client__text": "A사 미팅 · 참석자 2명",
            }
        )
        self.assertEqual(payload["business_meal_with_client"]["answer_value"], "yes")
        self.assertEqual(payload["business_meal_with_client"]["answer_text"], "A사 미팅 · 참석자 2명")

    def test_save_follow_up_answers_and_re_evaluate_returns_updated_decision(self) -> None:
        session = _FakeSession()
        result = save_receipt_follow_up_answers_and_re_evaluate(
            session,
            _FakeAnswerModel,
            user_pk=7,
            answered_by=7,
            tx=_tx(),
            evidence_item=SimpleNamespace(id=31),
            answers_payload={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅 · 참석자 2명",
                }
            },
            draft={"attendee_note": "거래처 2명"},
            focus_kind="receipt_attach",
        )
        self.assertEqual(result["decision"]["level"], "needs_review")
        self.assertEqual(len(session.rows), 1)
        self.assertEqual(session.rows[0].question_key, "business_meal_with_client")

    def test_save_follow_up_answers_updates_existing_answer(self) -> None:
        session = _FakeSession()
        tx = _tx()
        save_receipt_follow_up_answers_and_re_evaluate(
            session,
            _FakeAnswerModel,
            user_pk=7,
            answered_by=7,
            tx=tx,
            evidence_item=SimpleNamespace(id=31),
            answers_payload={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "첫 메모",
                }
            },
            draft={"attendee_note": "거래처 2명"},
            focus_kind="receipt_attach",
        )
        result = save_receipt_follow_up_answers_and_re_evaluate(
            session,
            _FakeAnswerModel,
            user_pk=7,
            answered_by=7,
            tx=tx,
            evidence_item=SimpleNamespace(id=31),
            answers_payload={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "수정된 메모",
                }
            },
            draft={"attendee_note": "거래처 2명"},
            focus_kind="receipt_attach",
        )
        self.assertEqual(len(session.rows), 1)
        self.assertEqual(session.rows[0].answer_text, "수정된 메모")
        self.assertEqual(
            result["follow_up_answers"]["business_meal_with_client"]["answer_text"],
            "수정된 메모",
        )

    def test_invalid_question_key_is_rejected(self) -> None:
        session = _FakeSession()
        with self.assertRaisesRegex(ValueError, "invalid_question_key"):
            save_receipt_follow_up_answers_and_re_evaluate(
                session,
                _FakeAnswerModel,
                user_pk=7,
                answered_by=7,
                tx=_tx(),
                evidence_item=SimpleNamespace(id=31),
                answers_payload={
                    "not_real_question": {
                        "answer_value": "yes",
                    }
                },
                focus_kind="receipt_attach",
            )

    def test_guidance_wrapper_restores_current_answers(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(),
            draft={"attendee_note": "거래처 2명"},
            focus_kind="receipt_attach",
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅 · 참석자 2명",
                }
            },
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["follow_up_questions"][0]["current_value"], "yes")
        self.assertTrue(payload["applied_follow_up_answers"])

    def test_route_and_templates_wire_followup_form(self) -> None:
        route_body = self._read("routes/web/calendar/review.py")
        partial_body = self._read("templates/calendar/partials/receipt_expense_hint.html")
        confirm_body = self._read("templates/calendar/partials/receipt_wizard_confirm.html")
        match_body = self._read("templates/calendar/partials/receipt_wizard_match.html")
        review_body = self._read("templates/calendar/review.html")
        self.assertIn("def review_expense_followup_save", route_body)
        self.assertIn("save_receipt_follow_up_answers_and_re_evaluate", route_body)
        self.assertIn("followup__", partial_body)
        self.assertIn("return_view", partial_body)
        self.assertIn("expense_followup_action", confirm_body)
        self.assertIn("expense_followup_action", match_body)
        self.assertIn("expense_followup_action", review_body)


if __name__ == "__main__":
    unittest.main()

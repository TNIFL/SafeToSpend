from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from services.receipt_expense_guidance import build_receipt_expense_inline_guidance
from services.receipt_expense_rules import (
    extract_reinforcement_payload_from_form,
    save_receipt_follow_up_answers_and_re_evaluate,
    save_receipt_reinforcement_and_re_evaluate,
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


class _FakeReinforcementModel:
    user_pk = _Field("user_pk")
    transaction_id = _Field("transaction_id")

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.updated_at = getattr(self, "updated_at", None)
        self.created_at = getattr(self, "created_at", None)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []

    def filter(self, expression):
        self._filters.append(expression)
        return self

    def _filtered(self):
        rows = list(self._rows)
        for operator, field_name, value in self._filters:
            if operator == "eq":
                rows = [row for row in rows if getattr(row, field_name, None) == value]
            elif operator == "in":
                rows = [row for row in rows if getattr(row, field_name, None) in value]
        return rows

    def all(self):
        return self._filtered()

    def first(self):
        rows = self._filtered()
        return rows[0] if rows else None


class _FakeSession:
    def __init__(self):
        self.answer_rows = []
        self.reinforcement_rows = []

    def query(self, model):
        if model is _FakeAnswerModel:
            return _FakeQuery(self.answer_rows)
        if model is _FakeReinforcementModel:
            return _FakeQuery(self.reinforcement_rows)
        raise AssertionError(f"unexpected model: {model}")

    def add(self, row):
        if isinstance(row, _FakeAnswerModel):
            if row not in self.answer_rows:
                self.answer_rows.append(row)
            return
        if isinstance(row, _FakeReinforcementModel):
            if row not in self.reinforcement_rows:
                self.reinforcement_rows.append(row)
            return
        raise AssertionError(f"unexpected row: {row}")

    def flush(self):
        return None

    def rollback(self):
        return None


class _StoredFile:
    def __init__(self, *, file_key: str, original_filename: str):
        self.file_key = file_key
        self.original_filename = original_filename
        self.mime_type = "application/pdf"
        self.size_bytes = 1024
        self.sha256 = "dummy"


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


class ReceiptExpenseReinforcementIntegrationTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_extract_reinforcement_payload_from_form(self) -> None:
        payload = extract_reinforcement_payload_from_form(
            {
                "reinforce__business_context_note": "A사 미팅 준비",
                "reinforce__attendee_names": "A사 김팀장",
                "reinforce__client_or_counterparty_name": "A사",
            }
        )
        self.assertEqual(payload["business_context_note"], "A사 미팅 준비")
        self.assertEqual(payload["attendee_names"], "A사 김팀장")

    def test_save_reinforcement_re_evaluates_existing_followup_case(self) -> None:
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
                    "answer_text": "A사 미팅",
                }
            },
            focus_kind="receipt_attach",
        )
        result = save_receipt_reinforcement_and_re_evaluate(
            session,
            _FakeReinforcementModel,
            _FakeAnswerModel,
            user_pk=7,
            updated_by=7,
            tx=tx,
            evidence_item=SimpleNamespace(id=31),
            reinforcement_payload={
                "business_context_note": "A사 제안 미팅 중 음료 결제",
                "attendee_names": "A사 김팀장, 박대리",
                "client_or_counterparty_name": "A사",
            },
            draft={},
            focus_kind="receipt_attach",
            receipt_type="paper",
            month_key="2026-03",
        )
        self.assertEqual(result["decision"]["level"], "high_likelihood")
        self.assertEqual(len(session.reinforcement_rows), 1)
        self.assertFalse(result["decision"]["remaining_gaps"])

    def test_save_reinforcement_updates_existing_row_and_supporting_file_metadata(self) -> None:
        session = _FakeSession()
        tx = _tx(id=12, counterparty="Apple Store", memo="맥북 구입", amount_krw=2190000)
        called = {}

        class _Upload:
            filename = "usage-note.pdf"

        def _store_supporting_file_fn(**kwargs):
            called.update(kwargs)
            return _StoredFile(
                file_key="u7/2026-03/tx12/support.pdf",
                original_filename="usage-note.pdf",
            )

        first = save_receipt_reinforcement_and_re_evaluate(
            session,
            _FakeReinforcementModel,
            _FakeAnswerModel,
            user_pk=7,
            updated_by=7,
            tx=tx,
            evidence_item=SimpleNamespace(id=32),
            reinforcement_payload={
                "asset_usage_note": "영상 편집 업무용",
                "business_context_note": "콘텐츠 제작 장비",
            },
            month_key="2026-03",
        )
        self.assertEqual(first["decision"]["level"], "consult_tax_review")

        second = save_receipt_reinforcement_and_re_evaluate(
            session,
            _FakeReinforcementModel,
            _FakeAnswerModel,
            user_pk=7,
            updated_by=7,
            tx=tx,
            evidence_item=SimpleNamespace(id=32),
            reinforcement_payload={
                "asset_usage_note": "수정된 업무 메모",
            },
            month_key="2026-03",
            supporting_file=_Upload(),
            store_supporting_file_fn=_store_supporting_file_fn,
        )
        self.assertEqual(len(session.reinforcement_rows), 1)
        self.assertEqual(session.reinforcement_rows[0].asset_usage_note, "수정된 업무 메모")
        self.assertEqual(session.reinforcement_rows[0].supporting_file_name, "usage-note.pdf")
        self.assertEqual(called["month_key"], "2026-03")
        self.assertEqual(second["decision"]["level"], "consult_tax_review")

    def test_guidance_wrapper_restores_reinforcement_data(self) -> None:
        payload = build_receipt_expense_inline_guidance(
            tx=_tx(),
            focus_kind="receipt_attach",
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅",
                }
            },
            reinforcement_data={
                "business_context_note": "A사 제안 미팅 중 음료 결제",
                "attendee_names": "A사 김팀장, 박대리",
                "client_or_counterparty_name": "A사",
            },
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["reinforcement_readiness"], "sufficient")
        self.assertTrue(payload["applied_reinforcement"])

    def test_route_and_templates_wire_reinforcement_form(self) -> None:
        route_body = self._read("routes/web/calendar/review.py")
        partial_body = self._read("templates/calendar/partials/receipt_expense_hint.html")
        confirm_body = self._read("templates/calendar/partials/receipt_wizard_confirm.html")
        match_body = self._read("templates/calendar/partials/receipt_wizard_match.html")
        review_body = self._read("templates/calendar/review.html")
        self.assertIn("def review_expense_reinforcement_save", route_body)
        self.assertIn("save_receipt_reinforcement_and_re_evaluate", route_body)
        self.assertIn("reinforce__", partial_body)
        self.assertIn("expense_reinforcement_action", confirm_body)
        self.assertIn("expense_reinforcement_action", match_body)
        self.assertIn("expense_reinforcement_action", review_body)


if __name__ == "__main__":
    unittest.main()

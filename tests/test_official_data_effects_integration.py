from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, session

from services.risk import compute_risk_summary


class _ScalarQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def scalar(self):
        return self.value


class _LedgerQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


overview_module = _load_module("test_effects_overview_module", "routes/web/overview.py")
web_calendar_module = _load_module("test_effects_web_calendar_module", "routes/web/web_calendar.py")


class OfficialDataEffectsIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.secret_key = "official-data-effects-integration"

    @patch("services.risk._get_settings")
    @patch("services.risk.collect_official_tax_effects_for_user_month")
    @patch("services.risk.db.session.query")
    def test_risk_summary_keeps_existing_value_without_official_data(self, query_mock, effect_mock, settings_mock) -> None:
        query_mock.side_effect = [
            _ScalarQuery(1_000_000),
            _ScalarQuery(200_000),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(30_000),
        ]
        settings_mock.return_value = SimpleNamespace(default_tax_rate=0.15)
        effect_mock.return_value = {
            "official_withheld_tax_krw": 0,
            "official_paid_tax_krw": 0,
            "official_tax_reference_date": None,
            "official_tax_effect_status": "none",
            "official_tax_effect_strength": "none",
            "official_tax_effect_reason": "none",
            "official_tax_effect_source_count": 0,
        }

        summary = compute_risk_summary(7, month_key="2026-03")
        self.assertEqual(summary.tax_due_before_official_data_krw, 150000)
        self.assertEqual(summary.tax_due_after_official_data_krw, 150000)
        self.assertEqual(summary.buffer_target_krw, 150000)
        self.assertEqual(summary.buffer_shortage_krw, 120000)
        self.assertEqual(summary.tax_delta_from_official_data_krw, 0)

    @patch("services.risk._get_settings")
    @patch("services.risk.collect_official_tax_effects_for_user_month")
    @patch("services.risk.db.session.query")
    def test_risk_summary_applies_a_or_b_tax_effect(self, query_mock, effect_mock, settings_mock) -> None:
        query_mock.side_effect = [
            _ScalarQuery(1_000_000),
            _ScalarQuery(200_000),
            _ScalarQuery(1),
            _ScalarQuery(2),
            _ScalarQuery(3),
            _ScalarQuery(4),
            _ScalarQuery(30_000),
        ]
        settings_mock.return_value = SimpleNamespace(default_tax_rate=0.15)
        effect_mock.return_value = {
            "official_withheld_tax_krw": 100_000,
            "official_paid_tax_krw": 0,
            "official_tax_reference_date": "2026-03-05",
            "official_tax_effect_status": "applied",
            "official_tax_effect_strength": "medium",
            "official_tax_effect_reason": "공식 양식 구조를 검증한 자료 기준으로 이미 빠진 세금을 반영했어요.",
            "official_tax_effect_source_count": 1,
        }

        summary = compute_risk_summary(7, month_key="2026-03")
        self.assertEqual(summary.tax_due_before_official_data_krw, 150000)
        self.assertEqual(summary.tax_due_after_official_data_krw, 50000)
        self.assertEqual(summary.buffer_target_krw, 50000)
        self.assertEqual(summary.buffer_shortage_krw, 20000)
        self.assertEqual(summary.tax_delta_from_official_data_krw, -100000)
        self.assertEqual(summary.official_tax_reference_date, "2026-03-05")
        self.assertEqual(summary.official_tax_effect_status, "applied")

    @patch("services.risk._get_settings")
    @patch("services.risk.collect_official_tax_effects_for_user_month")
    @patch("services.risk.db.session.query")
    def test_risk_summary_does_not_apply_review_needed_effect(self, query_mock, effect_mock, settings_mock) -> None:
        query_mock.side_effect = [
            _ScalarQuery(800_000),
            _ScalarQuery(150_000),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(10_000),
        ]
        settings_mock.return_value = SimpleNamespace(default_tax_rate=0.1)
        effect_mock.return_value = {
            "official_withheld_tax_krw": 0,
            "official_paid_tax_krw": 0,
            "official_tax_reference_date": "2026-03-05",
            "official_tax_effect_status": "review_needed",
            "official_tax_effect_strength": "none",
            "official_tax_effect_reason": "검토가 더 필요한 자료라 세금 숫자에는 자동 반영하지 않았어요.",
            "official_tax_effect_source_count": 1,
        }

        summary = compute_risk_summary(7, month_key="2026-03")
        self.assertEqual(summary.tax_due_before_official_data_krw, 80000)
        self.assertEqual(summary.tax_due_after_official_data_krw, 80000)
        self.assertEqual(summary.tax_delta_from_official_data_krw, 0)
        self.assertEqual(summary.official_tax_effect_status, "review_needed")

    @patch("services.risk._get_settings")
    @patch("services.risk.collect_official_tax_effects_for_user_month")
    @patch("services.risk.db.session.query")
    def test_risk_summary_applies_paid_tax_history_effect(self, query_mock, effect_mock, settings_mock) -> None:
        query_mock.side_effect = [
            _ScalarQuery(1_200_000),
            _ScalarQuery(250_000),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(0),
            _ScalarQuery(20_000),
        ]
        settings_mock.return_value = SimpleNamespace(default_tax_rate=0.1)
        effect_mock.return_value = {
            "official_withheld_tax_krw": 0,
            "official_paid_tax_krw": 70000,
            "official_tax_reference_date": "2026-03-12",
            "official_tax_effect_status": "applied",
            "official_tax_effect_strength": "medium",
            "official_tax_effect_reason": "공식 양식 구조를 검증한 자료 기준으로 이미 납부한 세금을 반영했어요.",
            "official_tax_effect_source_count": 1,
        }

        summary = compute_risk_summary(7, month_key="2026-03")
        self.assertEqual(summary.tax_due_before_official_data_krw, 120000)
        self.assertEqual(summary.tax_due_after_official_data_krw, 50000)
        self.assertEqual(summary.official_paid_tax_krw, 70000)
        self.assertEqual(summary.tax_delta_from_official_data_krw, -70000)

    @patch("test_effects_overview_module.collect_nhis_effects_for_user")
    @patch("test_effects_overview_module.compute_overview")
    @patch("test_effects_overview_module.render_template")
    def test_overview_route_includes_visual_feedback_models(self, render_template_mock, compute_overview_mock, nhis_effect_mock) -> None:
        compute_overview_mock.return_value = {
            "month_key": "2026-03",
            "tax_due_before_official_data_krw": 150000,
            "tax_due_after_official_data_krw": 50000,
            "official_withheld_tax_krw": 100000,
            "official_paid_tax_krw": 0,
            "official_tax_reference_date": "2026-03-05",
            "official_tax_effect_status": "applied",
            "official_tax_effect_strength": "medium",
            "official_tax_effect_reason": "공식 양식 구조를 검증한 자료 기준으로 이미 빠진 세금을 반영했어요.",
            "official_tax_effect_source_count": 1,
            "official_tax_effect_document_types": ("hometax_withholding_statement",),
        }
        nhis_effect_mock.return_value = {
            "nhis_effect_status": "reference_available",
            "nhis_reference_date": "2026-03-03",
            "nhis_latest_paid_amount_krw": 333000,
            "nhis_effect_strength": "medium",
            "nhis_effect_reason": "최근 공식 납부 기준 참고 상태로만 연결하고, 건보료 계산값을 바로 덮어쓰지는 않아요.",
            "nhis_recheck_required": False,
            "nhis_effect_source_count": 1,
            "nhis_effect_document_types": ("nhis_payment_confirmation",),
        }
        render_template_mock.side_effect = lambda template_name, **context: context

        with self.app.test_request_context("/overview?month=2026-03"):
            session["user_id"] = 7
            context = overview_module.overview.__wrapped__()

        self.assertIn("official_tax_visual_feedback", context)
        self.assertIn("nhis_visual_feedback", context)
        self.assertEqual(context["official_tax_visual_feedback"]["tax_delta_krw"], -100000)
        self.assertTrue(context["official_tax_visual_feedback"]["should_animate"])
        self.assertFalse(context["nhis_visual_feedback"]["should_animate"])

    @patch("test_effects_web_calendar_module.collect_nhis_effects_for_user")
    @patch("test_effects_web_calendar_module.compute_risk_summary")
    @patch("test_effects_web_calendar_module.db.session.query")
    @patch("test_effects_web_calendar_module.render_template")
    @patch("test_effects_web_calendar_module.SafeToSpendSettings")
    def test_tax_buffer_route_includes_visual_feedback_models(
        self,
        settings_model_mock,
        render_template_mock,
        query_mock,
        compute_risk_summary_mock,
        nhis_effect_mock,
    ) -> None:
        compute_risk_summary_mock.return_value = SimpleNamespace(
            gross_income_krw=1_000_000,
            buffer_target_krw=80_000,
            buffer_total_krw=30_000,
            official_withheld_tax_krw=0,
            official_paid_tax_krw=70_000,
            official_tax_reference_date="2026-03-12",
            official_tax_effect_status="applied",
            official_tax_effect_strength="medium",
            official_tax_effect_reason="공식 양식 구조를 검증한 자료 기준으로 이미 납부한 세금을 반영했어요.",
            official_tax_effect_source_count=1,
            official_tax_effect_document_types=("hometax_tax_payment_history",),
            tax_due_before_official_data_krw=150_000,
            tax_due_after_official_data_krw=80_000,
        )
        nhis_effect_mock.return_value = {
            "nhis_effect_status": "reference_available",
            "nhis_reference_date": "2026-03-03",
            "nhis_latest_paid_amount_krw": 333000,
            "nhis_effect_strength": "medium",
            "nhis_effect_reason": "최근 공식 납부 기준 참고 상태로만 연결하고, 건보료 계산값을 바로 덮어쓰지는 않아요.",
            "nhis_recheck_required": False,
            "nhis_effect_source_count": 1,
            "nhis_effect_document_types": ("nhis_payment_confirmation",),
        }
        query_mock.return_value = _LedgerQuery([])
        settings_model_mock.query = SimpleNamespace(get=lambda user_pk: SimpleNamespace(default_tax_rate=0.15))
        render_template_mock.side_effect = lambda template_name, **context: context

        with self.app.test_request_context("/dashboard/tax-buffer?month=2026-03"):
            session["user_id"] = 7
            context = web_calendar_module.tax_buffer()

        self.assertIn("official_tax_visual_feedback", context)
        self.assertIn("nhis_visual_feedback", context)
        self.assertEqual(context["official_tax_visual_feedback"]["buffer_delta_krw"], -70000)
        self.assertTrue(context["official_tax_visual_feedback"]["should_animate"])
        self.assertEqual(context["nhis_visual_feedback"]["nhis_effect_status"], "reference_available")


if __name__ == "__main__":
    unittest.main()

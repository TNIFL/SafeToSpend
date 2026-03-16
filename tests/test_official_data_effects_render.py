from __future__ import annotations

import unittest
from pathlib import Path

from flask import Flask, render_template_string


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataEffectsRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        self.app.add_url_rule('/dashboard/tax-buffer', endpoint='web_calendar.tax_buffer', view_func=lambda: 'tax-buffer')

    def test_notice_partial_renders_tax_and_nhis_copy(self) -> None:
        with self.app.app_context():
            body = render_template_string(
                '{% include "partials/official_data_effect_notice.html" %}',
                official_tax_effect_notice={
                    'show': True,
                    'title': '홈택스 자료 반영 상태',
                    'summary': '공식 양식 구조를 검증한 자료 기준으로 이미 빠진 세금을 반영했어요.',
                    'strength_label': '보통',
                    'reference_date': '2026-03-05',
                    'source_count': 1,
                    'document_kind_summary': '원천징수 반영, 납부내역 반영',
                    'recheck_required': False,
                    'delta_krw': -100000,
                    'before_tax_due_krw': 150000,
                    'after_tax_due_krw': 50000,
                },
                nhis_effect_notice={
                    'show': True,
                    'title': 'NHIS 참고 상태',
                    'summary': '최근 공식 납부 기준 참고 상태로만 연결하고, 건보료 계산값을 바로 덮어쓰지는 않아요.',
                    'strength_label': '보통',
                    'reference_date': '2026-03-03',
                    'latest_paid_amount_krw': 333000,
                    'document_kind_summary': '납부확인 참고, 자격자료 참고',
                    'recheck_required': True,
                },
            )
        self.assertIn('홈택스 자료 반영 상태', body)
        self.assertIn('예상세금 변화', body)
        self.assertIn('150,000원 → 50,000원', body)
        self.assertIn('원천징수 반영, 납부내역 반영', body)
        self.assertIn('최근 공식 납부 기준 참고', body)
        self.assertIn('납부확인 참고, 자격자료 참고', body)
        self.assertIn('재확인 권장', body)
        self.assertIn('구조 검증과 기관 확인은 같은 뜻이 아니에요.', body)

    def test_notice_partial_avoids_forbidden_copy(self) -> None:
        with self.app.app_context():
            body = render_template_string(
                '{% include "partials/official_data_effect_notice.html" %}',
                official_tax_effect_notice={
                    'show': True,
                    'title': '홈택스 자료 반영 상태',
                    'summary': '업로드한 자료 기준 참고 상태로만 유지해요.',
                    'strength_label': '약',
                    'reference_date': '2026-03-05',
                    'source_count': 1,
                    'document_kind_summary': '납부내역 참고',
                    'recheck_required': False,
                    'delta_krw': None,
                    'before_tax_due_krw': None,
                    'after_tax_due_krw': None,
                },
                nhis_effect_notice={'show': False},
            )
        self.assertNotIn('확정됨', body)
        self.assertNotIn('원본 보증', body)
        self.assertNotIn('100% 정확', body)

    def test_review_surface_renders_delta_summary_and_cta(self) -> None:
        with self.app.test_request_context('/dashboard/review?month=2026-03'):
            body = render_template_string(
                '{% include "partials/official_data_effect_notice.html" %}',
                official_data_effect_surface='review',
                month_key='2026-03',
                official_tax_effect_notice={
                    'show': True,
                    'title': '홈택스 자료 반영 상태',
                    'summary': '공식 양식 구조를 검증한 자료 기준으로 이미 빠진 세금을 반영했어요.',
                    'strength_label': '보통',
                    'reference_date': '2026-03-05',
                    'source_count': 1,
                    'document_kind_summary': '원천징수 반영',
                    'recheck_required': False,
                    'delta_krw': -100000,
                    'before_tax_due_krw': 150000,
                    'after_tax_due_krw': 50000,
                },
                official_tax_visual_feedback={
                    'show': True,
                    'reference_date': '2026-03-05',
                    'document_kind_summary': '원천징수 반영',
                    'tax_delta_krw': -100000,
                    'before_tax_due_krw': 150000,
                    'after_tax_due_krw': 50000,
                },
                nhis_effect_notice={'show': False},
            )
        self.assertIn('이번 달 공식 자료 기준 세금 변화 요약', body)
        self.assertIn('150,000원 →', body)
        self.assertIn('50,000원', body)
        self.assertIn('세금 보관함에서 자세히 보기', body)

    def test_templates_include_notice_partial_and_visual_feedback_hooks(self) -> None:
        overview = (ROOT / 'templates/overview.html').read_text(encoding='utf-8')
        tax_buffer = (ROOT / 'templates/calendar/tax_buffer.html').read_text(encoding='utf-8')
        review = (ROOT / 'templates/calendar/review.html').read_text(encoding='utf-8')
        self.assertIn('official_data_effect_notice.html', overview)
        self.assertIn('official_data_effect_notice.html', tax_buffer)
        self.assertIn('official_data_effect_notice.html', review)
        self.assertIn('official-data-number-animate.js', overview)
        self.assertIn('official-data-number-animate.js', tax_buffer)
        self.assertIn('data-od-before', overview)
        self.assertIn('data-od-before', tax_buffer)
        self.assertIn('official_tax_visual_feedback', overview)
        self.assertIn('official_tax_visual_feedback', tax_buffer)


if __name__ == '__main__':
    unittest.main()

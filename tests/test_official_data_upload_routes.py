from __future__ import annotations

import io
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
import types

from services.official_data_extractors import OfficialDataFileError


ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


receipt_stub = types.ModuleType("services.receipt_expense_guidance")
receipt_stub.get_receipt_expense_guidance_content = lambda: {"title": "stub", "summary": "stub", "sections": [], "sources": []}
sys.modules.setdefault("services.receipt_expense_guidance", receipt_stub)

web_guide_bp = _load_module("test_web_guide_module", "routes/web/guide.py").web_guide_bp
web_official_data_bp = _load_module("test_web_official_data_module", "routes/web/official_data.py").web_official_data_bp
OFFICIAL_DATA_MODULE = "test_web_official_data_module"


class OfficialDataUploadRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.config['SECRET_KEY'] = 'official-data-upload-routes'
        app.register_blueprint(web_guide_bp)
        app.register_blueprint(web_official_data_bp)
        app.context_processor(lambda: {'csrf_token': 'test-csrf'})

        endpoints = {
            'web_main.landing': '/',
            'web_main.pricing': '/pricing',
            'web_main.preview': '/preview',
            'web_auth.login': '/login',
            'web_auth.logout': '/logout',
            'web_auth.register': '/register',
            'web_overview.overview': '/overview',
            'web_inbox.index': '/dashboard/inbox',
            'web_inbox.import_page': '/dashboard/import',
            'web_bank.index': '/dashboard/bank',
            'web_package.page': '/dashboard/package',
            'web_vault.index': '/dashboard/vault',
            'web_profile.tax_profile': '/dashboard/profile/tax',
            'web_profile.nhis_page': '/dashboard/profile/nhis',
            'web_profile.mypage': '/dashboard/mypage',
            'web_profile.admin_assets_data': '/dashboard/admin/assets',
            'web_profile.admin_nhis_rates': '/dashboard/admin/nhis-rates',
            'web_calendar.reconcile': '/dashboard/reconcile',
            'web_support.support_home': '/dashboard/support',
            'web_admin.admin_index': '/dashboard/admin',
            'web_admin.admin_ops': '/dashboard/admin/ops',
            'web_admin.admin_support': '/dashboard/admin/support',
            'web_calendar.review': '/dashboard/review',
            'web_calendar.tax_buffer': '/dashboard/tax-buffer',
            'web_calendar.month_calendar': '/dashboard/calendar',
        }
        for endpoint, rule in endpoints.items():
            app.add_url_rule(rule, endpoint=endpoint, view_func=lambda endpoint=endpoint: endpoint)

        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 5

    def _fake_document(self, **overrides):
        base = {
            'id': 7,
            'display_name': '건보료 납부확인서',
            'document_type': 'nhis_payment_confirmation',
            'parse_status': 'parsed',
            'raw_file_storage_mode': 'none',
            'structure_validation_status': 'passed',
            'verification_status': 'none',
            'parse_error_code': None,
            'parse_error_detail': None,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _fake_context(self, document, **overrides):
        context = {
            'document': document,
            'status_title': '구조 검증 완료',
            'status_summary': '이 자료를 기준으로 자동 관리해볼게요.',
            'status_tone': 'success',
            'source_label': 'NHIS',
            'summary_rows': [
                {'label': '발급기관', 'value': '국민건강보험공단'},
                {'label': '기준일', 'value': '2026-03-03'},
            ],
            'recheck_label': '반영 가능',
            'recheck_detail': '현재 기준일 안에서는 구조 검증을 통과한 자료 기준으로 참고할 수 있어요.',
            'trust_grade': 'B',
            'trust_grade_label': '공식 양식 구조와 일치',
            'trust_scope_label': '기관 확인 전 구조 검증 자료',
            'guide_url': '/guide/official-data#nhis',
        }
        context.update(overrides)
        return context

    def test_upload_page_renders_supported_format_copy(self) -> None:
        resp = self.client.get('/dashboard/official-data/upload?document_type=nhis_payment_confirmation')
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('공식 자료 올리기', body)
        self.assertIn('CSV, XLSX, 텍스트 추출 가능한 PDF', body)
        self.assertIn('파일 전체 선택 저장은 준비 중', body)

    @patch(f'{OFFICIAL_DATA_MODULE}.build_official_data_result_context')
    @patch(f'{OFFICIAL_DATA_MODULE}.get_official_data_document_for_user')
    @patch(f'{OFFICIAL_DATA_MODULE}.process_official_data_upload')
    def test_upload_success_redirects_and_result_page_shows_basis_date(self, process_upload, get_document, build_context) -> None:
        document = self._fake_document(parse_status='parsed')
        process_upload.return_value = SimpleNamespace(document=document, status_title='구조 검증 완료', status_summary='ok', status_tone='success')
        get_document.return_value = document
        build_context.return_value = self._fake_context(document)

        resp = self.client.post(
            '/dashboard/official-data/upload',
            data={
                'document_type': 'nhis_payment_confirmation',
                'official_data_file': (io.BytesIO(b'%PDF-1.4\nfixture\n'), 'nhis.pdf'),
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('공식 자료 확인 결과', body)
        self.assertIn('기준일', body)
        self.assertIn('2026-03-03', body)
        self.assertIn('structure validation', body)
        self.assertIn('verification', body)

    @patch(f'{OFFICIAL_DATA_MODULE}.build_official_data_result_context')
    @patch(f'{OFFICIAL_DATA_MODULE}.get_official_data_document_for_user')
    @patch(f'{OFFICIAL_DATA_MODULE}.process_official_data_upload')
    def test_upload_unsupported_state_is_rendered(self, process_upload, get_document, build_context) -> None:
        document = self._fake_document(parse_status='unsupported', parse_error_code='unsupported_extension')
        process_upload.return_value = SimpleNamespace(document=document, status_title='지원 안 함', status_summary='지원 형식이 아니에요.', status_tone='warn')
        get_document.return_value = document
        build_context.return_value = self._fake_context(
            document,
            status_title='지원 안 함',
            status_summary='지원하는 공식 원본 형식이 아니에요.',
            status_tone='warn',
            recheck_label='검토 필요',
            recheck_detail='지원 형식이 아니라 자동 관리 기준으로 쓰지 않았어요.',
        )

        resp = self.client.post(
            '/dashboard/official-data/upload',
            data={
                'document_type': 'hometax_withholding_statement',
                'official_data_file': (io.BytesIO(b'fake-image'), 'capture.jpg'),
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('지원 안 함', body)
        self.assertIn('unsupported_extension', body)

    @patch(f'{OFFICIAL_DATA_MODULE}.build_official_data_result_context')
    @patch(f'{OFFICIAL_DATA_MODULE}.get_official_data_document_for_user')
    @patch(f'{OFFICIAL_DATA_MODULE}.process_official_data_upload')
    def test_upload_needs_review_state_is_rendered(self, process_upload, get_document, build_context) -> None:
        document = self._fake_document(parse_status='needs_review', parse_error_code='known_source_but_unrecognized')
        process_upload.return_value = SimpleNamespace(document=document, status_title='검토 필요', status_summary='일부만 읽혔어요.', status_tone='warn')
        get_document.return_value = document
        build_context.return_value = self._fake_context(
            document,
            status_title='검토 필요',
            status_summary='자동 반영하지 않았어요.',
            status_tone='warn',
            recheck_label='검토 필요',
            recheck_detail='원본 파일을 다시 확인해 주세요.',
        )

        resp = self.client.post(
            '/dashboard/official-data/upload',
            data={
                'document_type': 'hometax_business_card_usage',
                'official_data_file': (io.BytesIO(b'header1,header2\nvalue1,value2\n'), 'unknown.csv'),
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('검토 필요', body)
        self.assertIn('known_source_but_unrecognized', body)

    def test_upload_page_handles_missing_file_without_redirect(self) -> None:
        resp = self.client.post(
            '/dashboard/official-data/upload',
            data={'document_type': 'hometax_withholding_statement'},
            content_type='multipart/form-data',
        )
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('파일을 먼저 선택해 주세요', body)

    @patch(f'{OFFICIAL_DATA_MODULE}.process_official_data_upload')
    def test_upload_file_error_is_rendered_as_warn_state(self, process_upload) -> None:
        process_upload.side_effect = OfficialDataFileError('파일이 너무 커요.')
        resp = self.client.post(
            '/dashboard/official-data/upload',
            data={
                'document_type': 'hometax_withholding_statement',
                'official_data_file': (io.BytesIO(b'header\\nvalue\\n'), 'sample.csv'),
            },
            content_type='multipart/form-data',
        )
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('업로드 형식을 다시 확인해 주세요', body)
        self.assertIn('파일이 너무 커요.', body)


if __name__ == '__main__':
    unittest.main()

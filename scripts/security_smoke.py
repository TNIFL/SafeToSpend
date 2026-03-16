from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from flask import render_template


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_env() -> None:
    os.environ.setdefault("SECRET_KEY", "security-smoke-test-key")
    os.environ.setdefault("APP_ENV", "development")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html or "")
    return (m.group(1) if m else "").strip()


def main() -> int:
    _ensure_env()
    from app import create_app
    from core.extensions import db
    from core.security import sanitize_next_url
    from domain.models import AssetItem, User
    from scripts.sql_safety_scan import main as sql_safety_main
    from services.llm_safe import run_prompt_injection_self_test

    app = create_app()
    app.config.update(TESTING=True)

    user_pk = 0
    db_error = ""
    db_ready = False
    with app.app_context():
        try:
            user = User.query.filter_by(email="security-smoke@safetospend.local").first()
            if user is None:
                user = User(email="security-smoke@safetospend.local")
                user.set_password("Test1234!")
                db.session.add(user)
                db.session.commit()
            user_pk = int(user.id)
            db_ready = True
        except Exception as exc:
            db.session.rollback()
            db_error = str(exc)
            db_ready = False

        # XSS escape 확인: 사용자 텍스트가 스크립트로 실행 가능한 형태로 렌더되면 안 된다.
        with app.test_request_context("/security-smoke"):
            rendered = render_template(
                "partials/mini_guide.html",
                guide_open=True,
                guide_title="테스트",
                guide_one_liner="",
                guide_details_html="<script>alert(1)</script>",
                guide_link_url="",
                guide_link_text="",
            )
        _assert("<script>alert(1)</script>" not in rendered, "XSS escape 실패: script 태그가 그대로 렌더되었습니다.")
        _assert("&lt;script&gt;alert(1)&lt;/script&gt;" in rendered, "XSS escape 실패: 이스케이프된 텍스트가 보이지 않습니다.")

    client = app.test_client()

    r_home = client.get("/")
    _assert(r_home.status_code < 500, "랜딩 페이지에서 500이 발생하면 안 됩니다.")
    _assert(r_home.headers.get("X-Frame-Options") == "DENY", "보안 헤더(X-Frame-Options)가 누락되었습니다.")
    _assert(r_home.headers.get("X-Content-Type-Options") == "nosniff", "보안 헤더(X-Content-Type-Options)가 누락되었습니다.")
    csp = str(r_home.headers.get("Content-Security-Policy") or "")
    _assert("script-src 'self'" in csp, "CSP에 script-src 'self' 정책이 필요합니다.")

    # SQL 인젝션 위험 패턴 스캔
    _assert(sql_safety_main() == 0, "SQL 안전 스캔에서 위험 패턴이 발견되었습니다.")

    # CSRF 없는 요청 차단
    r_post_no_csrf = client.post("/login", data={"email": "x", "password": "y"})
    _assert(r_post_no_csrf.status_code in {302, 400}, "CSRF 없는 웹 POST는 차단되어야 합니다.")

    r_api_no_bearer = client.get("/api/this-path-should-be-protected")
    _assert(r_api_no_bearer.status_code == 401, "/api/*는 Bearer 토큰 없이 접근되면 안 됩니다.")
    r_api_logout_no_bearer = client.post("/api/auth/logout", json={})
    _assert(r_api_logout_no_bearer.status_code == 401, "/api/auth/logout은 Bearer 토큰 보호가 필요합니다.")

    safe1 = sanitize_next_url("https://evil.com", "/dashboard")
    safe2 = sanitize_next_url("//evil.com/path", "/dashboard")
    safe3 = sanitize_next_url("/dashboard/review?tab=required", "/dashboard")
    _assert(safe1 == "/dashboard", "외부 next URL 차단이 동작하지 않습니다.")
    _assert(safe2 == "/dashboard", "스킴 없는 외부 next URL 차단이 동작하지 않습니다.")
    _assert(safe3 == "/dashboard/review?tab=required", "정상 내부 next URL이 허용되지 않습니다.")

    # LLM 프롬프트 인젝션 가드 테스트(5개 케이스)
    llm_guard = run_prompt_injection_self_test()
    _assert(bool(llm_guard.get("ok")), "LLM 프롬프트 인젝션 가드 자가점검에 실패했습니다.")

    assets_csrf_status: int | str = "SKIP"
    comma_parse_status: str = "SKIP"
    if db_ready and user_pk > 0:
        with client.session_transaction() as sess:
            sess["user_id"] = user_pk

        # assets 페이지 접근 및 CSRF 토큰 확보
        r_assets_get = client.get("/dashboard/assets?month=2026-03&skip_quiz=1")
        _assert(r_assets_get.status_code < 500, "/dashboard/assets GET에서 500이 발생하면 안 됩니다.")
        csrf = _extract_csrf(r_assets_get.get_data(as_text=True))
        _assert(bool(csrf), "assets 페이지에서 csrf_token을 찾지 못했습니다.")

        # CSRF 없는 assets POST 차단
        r_assets_no_csrf = client.post(
            "/dashboard/assets?month=2026-03&skip_quiz=1&format=json",
            data={
                "month": "2026-03",
                "housing_mode": "rent",
                "rent_deposit_krw": "1,200,000",
                "rent_monthly_krw": "100,000",
            },
        )
        _assert(r_assets_no_csrf.status_code in {302, 400}, "assets POST는 CSRF 토큰이 없으면 차단되어야 합니다.")
        assets_csrf_status = int(r_assets_no_csrf.status_code)

        # 콤마 입력 저장/파싱 회귀
        r_assets_save = client.post(
            "/dashboard/assets?month=2026-03&skip_quiz=1&format=json",
            data={
                "csrf_token": csrf,
                "month": "2026-03",
                "action": "save_main",
                "housing_mode": "rent",
                "rent_deposit_krw": "1,200,000",
                "rent_monthly_krw": "100,000",
                "other_income_annual_krw": "2,500,000",
            },
        )
        _assert(r_assets_save.status_code < 500, "콤마 포함 assets 저장에서 500이 발생하면 안 됩니다.")

        with app.app_context():
            rent_item = (
                AssetItem.query.filter(AssetItem.user_pk == user_pk, AssetItem.kind == "rent")
                .order_by(AssetItem.updated_at.desc(), AssetItem.id.desc())
                .first()
            )
            _assert(rent_item is not None, "assets 저장 후 전월세 항목이 생성되지 않았습니다.")
            rent_input = dict(rent_item.input_json or {})
            _assert(int(rent_input.get("rent_deposit_krw") or 0) == 1_200_000, "보증금 콤마 파싱이 실패했습니다.")
            _assert(int(rent_input.get("rent_monthly_krw") or 0) == 100_000, "월세 콤마 파싱이 실패했습니다.")
        comma_parse_status = "PASS"

    print(
        {
            "ok": True,
            "sql_scan": "PASS",
            "csrf_block_login": r_post_no_csrf.status_code,
            "csrf_block_assets": assets_csrf_status,
            "xss_escape": "PASS",
            "llm_guard": "PASS",
            "comma_parse_assets": comma_parse_status,
            "db_ready": db_ready,
            "db_error": db_error[:120] if db_error else "",
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print({"ok": False, "error": str(exc)})
        raise

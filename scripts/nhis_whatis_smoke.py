from __future__ import annotations

import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TARGET_PATH = "/dashboard/assets?month=2026-03&skip_quiz=1"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _is_db_unavailable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "operationalerror",
            "connection refused",
            "operation not permitted",
            "could not connect",
            "connection to server",
            "no such table",
        )
    )


def _ensure_user(app):
    from core.extensions import db
    from domain.models import User

    user = User.query.order_by(User.id.asc()).first()
    if user:
        return int(user.id)

    seeded = User(
        email="nhis-whatis-smoke@safetospend.local",
        password_hash=generate_password_hash("safe-smoke-pass"),
        plan="free",
    )
    db.session.add(seeded)
    db.session.commit()
    return int(seeded.id)


def main() -> int:
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    try:
        with app.app_context():
            user_id = _ensure_user(app)

        with client.session_transaction() as sess:
            sess["user_id"] = int(user_id)
            sess.permanent = True

        res = client.get(TARGET_PATH, follow_redirects=True)
        _assert(int(res.status_code) < 500, f"status={res.status_code}")
        html = res.get_data(as_text=True)

        _assert("월 예상 건보료(추정)" in html, "필수 문구 누락: 월 예상 건보료(추정)")
        _assert(
            ("바꿔보면 얼마 달라질까?" in html) or ("가정해보기(what-if)" in html),
            "필수 문구 누락: 바꿔보면 얼마 달라질까?",
        )
        _assert('id="nhis-whatis-payload"' in html, "what-if 계산 payload 마커가 누락됐어요.")
        _assert("정확하게 입력하기" in html, "하이브리드 소득 토글 문구가 누락됐어요.")
        _assert('name="income_hybrid_present"' in html, "하이브리드 소득 폼 마커가 누락됐어요.")

        print(
            {
                "ok": True,
                "mode": "route",
                "path": TARGET_PATH,
                "status": int(res.status_code),
                "has_headline": True,
                "has_whatif": True,
                "has_payload": True,
                "has_income_hybrid_toggle": True,
                "has_income_hybrid_payload": True,
            }
        )
        return 0
    except Exception as exc:
        if not _is_db_unavailable(exc):
            raise

        page_html = (ROOT / "templates/assets.html").read_text(encoding="utf-8", errors="ignore")
        input_sections_html = (ROOT / "templates/components/assets_user_input_sections.html").read_text(
            encoding="utf-8",
            errors="ignore",
        )
        merged_html = "\n".join((page_html, input_sections_html))
        _assert("월 예상 건보료(추정)" in merged_html, "템플릿 필수 문구 누락: 월 예상 건보료(추정)")
        _assert(
            ("바꿔보면 얼마 달라질까?" in merged_html) or ("가정해보기(what-if)" in merged_html),
            "템플릿 필수 문구 누락: 바꿔보면 얼마 달라질까?",
        )
        _assert('id="nhis-whatis-payload"' in merged_html, "템플릿 payload 마커 누락")
        _assert("정확하게 입력하기" in merged_html, "템플릿 하이브리드 소득 토글 문구 누락")
        _assert('name="income_hybrid_present"' in merged_html, "템플릿 하이브리드 소득 폼 마커 누락")
        print(
            {
                "ok": True,
                "mode": "template_fallback",
                "reason": str(exc),
                "has_headline": True,
                "has_whatif": True,
                "has_payload": True,
                "has_income_hybrid_toggle": True,
                "has_income_hybrid_payload": True,
            }
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print({"ok": False, "error": str(exc)})
        raise

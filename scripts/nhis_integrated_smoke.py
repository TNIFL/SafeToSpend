from __future__ import annotations

from app import app
from core.extensions import db
from domain.models import User


MONTH = "2026-03"


def _ensure_user() -> int:
    with app.app_context():
        user = User.query.order_by(User.id.asc()).first()
        if user:
            return int(user.id)
        user = User(email="nhis_integrated_smoke@example.com")
        user.set_password("Temp1234!")
        db.session.add(user)
        db.session.commit()
        return int(user.id)


def main() -> int:
    user_id = _ensure_user()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["_csrf_token"] = "nhis-integrated-smoke-token"

    res_nhis = client.get(f"/dashboard/nhis?month={MONTH}&source=nhis")
    html_nhis = res_nhis.get_data(as_text=True)
    assert res_nhis.status_code == 200, "GET /dashboard/nhis 실패"
    assert "건보료" in html_nhis, "NHIS 제목 누락"
    assert "빠른 입력(30초)" in html_nhis, "입력 섹션 누락"
    assert "질문 1 · 가입유형" in html_nhis, "빠른 입력 질문 1 누락"
    assert "질문 2 · 현재 거주 형태" in html_nhis, "빠른 입력 질문 2 누락"
    assert "질문 3 · 금융소득(이자+배당)" in html_nhis, "빠른 입력 질문 3 누락"
    assert "입력 출처 바꾸기(고급)" not in html_nhis, "기본 화면에서 고급 입력 출처 전환 UI 노출"
    blocked_markers = (
        "계산 불가(공식 입력/데이터 부족)",
        "지금은 결과를 준비 중이에요.",
        "공식 기준 확인 중이라 잠시 숫자를 숨겼어요.",
    )
    blocked = any(marker in html_nhis for marker in blocked_markers)
    if not blocked:
        assert "왜 이 금액이 나왔나요?" in html_nhis, "원인 카드 누락"
        assert "한 줄 진단:" in html_nhis, "진단 라인 누락"
    else:
        assert "입력하러 가기" in html_nhis, "결과 준비중 CTA(입력 이동) 누락"
        assert "다시 확인" in html_nhis, "결과 준비중 CTA(다시 확인) 누락"
    assert "보증금 1,000만 올리면" not in html_nhis, "구형 가정 버튼 회귀"
    assert "월세 10만 올리면" not in html_nhis, "구형 월세 가정 버튼 회귀"

    res_nhis_debug = client.get(f"/dashboard/nhis?month={MONTH}&debug=1")
    html_nhis_debug = res_nhis_debug.get_data(as_text=True)
    assert res_nhis_debug.status_code == 200, "GET /dashboard/nhis?debug=1 실패"
    assert "입력 출처 바꾸기(고급)" in html_nhis_debug, "debug 화면에서 고급 입력 출처 전환 UI 누락"

    res_nhis_retry = client.get(f"/dashboard/nhis?month={MONTH}&retry=1")
    html_nhis_retry = res_nhis_retry.get_data(as_text=True)
    assert res_nhis_retry.status_code == 200, "GET /dashboard/nhis?retry=1 실패"
    assert "저장에 실패했어요. 입력을 확인한 뒤 다시 시도해 주세요." in html_nhis_retry, "retry 안내 누락"

    post_data = {
        "csrf_token": "nhis-integrated-smoke-token",
        "source": "nhis",
        "month": MONTH,
        "target_month": MONTH,
        "action": "save_main",
        "member_type": "regional",
        "housing_mode": "rent",
        "rent_deposit_krw": "120000000",
        "rent_monthly_krw": "700000",
        "other_income_annual_krw": "12000000",
        "history_sync_enabled": "0",
        "history_rows": "0",
    }
    res_post = client.post(f"/dashboard/nhis?month={MONTH}&source=nhis", data=post_data, follow_redirects=False)
    assert res_post.status_code in {302, 303}, "POST /dashboard/nhis 리다이렉트 실패"
    location = str(res_post.headers.get("Location") or "")
    assert "/dashboard/nhis" in location, "저장 후 기준 화면 복귀 실패"

    post_fin_boundary_data = {
        "csrf_token": "nhis-integrated-smoke-token",
        "source": "nhis",
        "month": MONTH,
        "target_month": MONTH,
        "action": "save_main",
        "member_type": "regional",
        "housing_mode": "rent",
        "rent_deposit_krw": "120000000",
        "rent_monthly_krw": "700000",
        "income_hybrid_present": "1",
        "income_hybrid_enabled": "1",
        "income_hybrid_year": "2025",
        "income_hybrid_scope": "both",
        "income_hybrid_input_basis": "income_amount_pre_tax",
        "income_hybrid_is_pre_tax": "1",
        "business_income_amount_krw": "0",
        "fin_income_amount_krw": "9900000",
        "salary_income_amount_krw": "0",
        "pension_income_amount_krw": "0",
        "other_income_amount_krw": "0",
        "history_sync_enabled": "0",
        "history_rows": "0",
    }
    res_fin = client.post(
        f"/dashboard/nhis?month={MONTH}&source=nhis",
        data=post_fin_boundary_data,
        follow_redirects=False,
    )
    assert res_fin.status_code in {302, 303}, "금융소득 경계 저장 리다이렉트 실패"
    res_nhis_fin = client.get(f"/dashboard/nhis?month={MONTH}&source=nhis")
    html_nhis_fin = res_nhis_fin.get_data(as_text=True)
    if not any(marker in html_nhis_fin for marker in blocked_markers):
        assert "금융소득 1,000만 기준 확인" in html_nhis_fin, "금융소득 경계 카드 누락"
        assert "1,000만 기준으로 계산해보기" in html_nhis_fin, "금융소득 경계 버튼 누락"

    res_assets = client.get(f"/dashboard/assets?month={MONTH}&skip_quiz=1", follow_redirects=False)
    assert res_assets.status_code in {301, 302, 303}, "GET /dashboard/assets 리다이렉트 없음"
    assets_location = str(res_assets.headers.get("Location") or "")
    assert "/dashboard/nhis" in assets_location, "assets 경로가 nhis로 유도되지 않음"

    print("PASS: nhis integrated smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

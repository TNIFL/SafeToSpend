from __future__ import annotations

from flask import Blueprint, render_template, session

from core.auth import login_required
from core.time import utcnow
from domain.models import OfficialDataDocument, ReferenceMaterialItem
from services.onboarding import build_onboarding_reflection


web_nhis_bp = Blueprint("web_nhis", __name__)

NHIS_DOCUMENT_TYPES = (
    "nhis_payment_confirmation",
    "nhis_eligibility_status",
)


def _build_nhis_guide_context(user_pk: int) -> dict:
    month_key = utcnow().strftime("%Y-%m")
    official_nhis_count = (
        OfficialDataDocument.query.filter(OfficialDataDocument.user_pk == int(user_pk))
        .filter(OfficialDataDocument.document_type.in_(NHIS_DOCUMENT_TYPES))
        .count()
    )
    reference_material_count = ReferenceMaterialItem.query.filter_by(user_pk=int(user_pk)).count()
    profile = build_onboarding_reflection(int(user_pk))

    profile_title = "현재 설정 정보가 없어 기본 건보 안내를 먼저 보여드리고 있습니다."
    profile_items = [
        "건보료 자료는 공식자료와 참고자료를 나눠 두는 것부터 시작하는 편이 안전합니다.",
        "건강보험 상태를 아직 정하지 않았다면 납부확인서와 자격 관련 문서를 먼저 확인해 두세요.",
    ]

    if profile["is_local_insured"]:
        profile_title = "입력하신 정보 기준으로는 지역가입자 쪽 자료를 먼저 챙기는 편이 좋습니다."
        profile_items = [
            "건강보험 납부확인서와 자격 관련 문서를 공식자료 채널에 먼저 모아 두세요.",
            "이번 달 거래를 정리한 뒤 건보 자료를 같이 보면 세무사에게 설명하기 쉬워집니다.",
        ]
        if profile["is_freelancer"] or profile["is_business_owner"]:
            profile_items.append("프리랜서/사업자 흐름이 있으면 홈택스 납부내역과 같이 준비하는 편이 실용적입니다.")
    elif profile["is_employee_insured"]:
        profile_title = "입력하신 정보 기준으로는 직장가입자 설명과 예외 상황을 먼저 확인하는 편이 좋습니다."
        profile_items = [
            "기본 건보료는 직장가입자 기준으로 보되, 부업이나 추가 소득이 있으면 자료를 따로 챙겨 두세요.",
            "원천징수 관련 문서와 건보 관련 공식자료를 나눠 두면 해석이 덜 섞입니다.",
        ]
        if profile["is_employee_sidejob"]:
            profile_items.append("직장인 + 부업이면 본업 자료와 부업 자료를 같은 달 안에서도 분리해 두는 편이 안전합니다.")

    return {
        "month_key": month_key,
        "official_nhis_count": int(official_nhis_count),
        "reference_material_count": int(reference_material_count),
        "profile_title": profile_title,
        "profile_items": profile_items,
        "profile_has_specific": profile["has_any_specific"],
    }


@web_nhis_bp.get("/dashboard/nhis")
@login_required
def index():
    user_pk = int(session["user_id"])
    return render_template("nhis.html", **_build_nhis_guide_context(user_pk))

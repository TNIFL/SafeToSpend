from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from core.auth import login_required
from domain.models import EvidenceItem, OfficialDataDocument, ReferenceMaterialItem, Transaction, User
from services.dashboard_state import get_state
from services.onboarding import (
    HEALTH_INSURANCE_OPTIONS,
    USER_TYPE_OPTIONS,
    VAT_STATUS_OPTIONS,
    build_onboarding_summary,
    get_onboarding_state,
    save_onboarding_state,
)
from services.plan import build_runtime_plan_state


web_profile_bp = Blueprint("web_profile", __name__)


def _build_account_summary(user_pk: int) -> dict:
    state = get_state(user_pk)
    evidence_total = EvidenceItem.query.filter_by(user_pk=user_pk).count()
    evidence_attached = EvidenceItem.query.filter_by(user_pk=user_pk, status="attached").count()

    return {
        "transaction_count": Transaction.query.filter_by(user_pk=user_pk).count(),
        "evidence_total": evidence_total,
        "evidence_attached": evidence_attached,
        "official_data_count": OfficialDataDocument.query.filter_by(user_pk=user_pk).count(),
        "reference_material_count": ReferenceMaterialItem.query.filter_by(user_pk=user_pk).count(),
        "gross_income": state["rev"],
        "expenses": state["exp"],
        "tax_rate_percent": round(state["rate"] * 100, 1),
    }


def _render_status_settings(*, user_pk: int, next_url: str):
    return render_template(
        "getting_started.html",
        next_url=next_url,
        onboarding_state=get_onboarding_state(user_pk),
        user_type_options=USER_TYPE_OPTIONS,
        health_insurance_options=HEALTH_INSURANCE_OPTIONS,
        vat_status_options=VAT_STATUS_OPTIONS,
        setup_page_title="내 상태 설정",
        setup_kicker="내 상태 설정",
        setup_title="지금 내 상황에 맞게<br>추천 기준만 간단히 맞춰둘 수 있어요.",
        setup_subtitle=(
            "공식자료 추천, NHIS 안내, 정리하기 우선순위에 반영돼요. "
            "잘 모르셔도 괜찮고, 언제든 다시 바꿀 수 있어요."
        ),
        setup_meta_text="현재 저장된 값을 보고 바로 바꿀 수 있어요.",
        setup_form_action=url_for("web_profile.status_settings"),
        show_skip_action=False,
        secondary_href=next_url,
        secondary_label="돌아가기",
        primary_button_label="내 상태 저장",
        setup_footer_note="저장하면 공식자료 추천과 NHIS/정리하기 안내에 바로 반영돼요.",
    )


@web_profile_bp.get("/mypage")
@web_profile_bp.get("/dashboard/account")
@login_required
def mypage():
    user_pk = int(session["user_id"])
    user = User.query.filter_by(id=user_pk).first_or_404()
    return render_template(
        "mypage.html",
        user=user,
        account_summary=_build_account_summary(user_pk),
        onboarding_summary=build_onboarding_summary(user_pk),
        plan_state=build_runtime_plan_state(user_pk=user_pk),
    )


@web_profile_bp.route("/my-status", methods=["GET", "POST"])
@web_profile_bp.route("/dashboard/my-status", methods=["GET", "POST"])
@login_required
def status_settings():
    user_pk = int(session["user_id"])
    next_url = request.form.get("next") or request.args.get("next") or url_for("web_profile.mypage")

    if request.method == "POST":
        try:
            save_onboarding_state(
                user_pk,
                user_type=(request.form.get("user_type") or "").strip(),
                health_insurance=(request.form.get("health_insurance") or "").strip(),
                vat_status=(request.form.get("vat_status") or "").strip(),
            )
        except ValueError:
            flash("모르는 항목은 '잘 모르겠어요'를 선택해 주세요.", "error")
            return redirect(url_for("web_profile.status_settings", next=next_url))

        flash("내 상태 설정을 저장했어요. 추천과 안내에 바로 반영됩니다.", "success")
        return redirect(next_url)

    return _render_status_settings(user_pk=user_pk, next_url=next_url)

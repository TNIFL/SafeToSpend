from __future__ import annotations

from flask import Blueprint, render_template, session

from core.auth import login_required
from domain.models import EvidenceItem, OfficialDataDocument, ReferenceMaterialItem, Transaction, User
from services.dashboard_state import get_state
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
        plan_state=build_runtime_plan_state(user_pk=user_pk),
    )

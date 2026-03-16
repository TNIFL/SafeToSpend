# routes/web/overview.py
from flask import Blueprint, render_template, request, session

from core.auth import login_required
from services.risk import compute_overview
from services.official_data_effects import (
    build_official_tax_effect_notice_context,
    build_official_tax_visual_feedback_for_overview,
)
from services.nhis_effects import (
    build_nhis_effect_notice_context,
    build_nhis_visual_feedback,
    collect_nhis_effects_for_user,
)

web_overview_bp = Blueprint("web_overview", __name__)

@web_overview_bp.route("/overview", methods=["GET"])
@login_required
def overview():
    user_pk = session["user_id"]
    month_key = (request.args.get("month") or "").strip() or None
    ctx = compute_overview(user_pk, month_key=month_key)
    official_tax_effect_state = {
        "official_withheld_tax_krw": ctx.get("official_withheld_tax_krw", 0),
        "official_paid_tax_krw": ctx.get("official_paid_tax_krw", 0),
        "official_tax_reference_date": ctx.get("official_tax_reference_date"),
        "official_tax_effect_strength": ctx.get("official_tax_effect_strength"),
        "official_tax_effect_source_count": ctx.get("official_tax_effect_source_count", 0),
        "official_tax_effect_status": ctx.get("official_tax_effect_status"),
        "official_tax_effect_reason": ctx.get("official_tax_effect_reason"),
        "official_tax_effect_document_types": ctx.get("official_tax_effect_document_types", ()),
    }
    nhis_effect = collect_nhis_effects_for_user(user_pk)
    ctx["official_tax_effect_notice"] = build_official_tax_effect_notice_context(
        official_tax_effect_state,
        before_tax_due_krw=ctx.get("tax_due_before_official_data_krw"),
        after_tax_due_krw=ctx.get("tax_due_after_official_data_krw"),
    )
    ctx["official_tax_visual_feedback"] = build_official_tax_visual_feedback_for_overview(
        official_tax_effect_state,
        before_tax_due_krw=ctx.get("tax_due_before_official_data_krw"),
        after_tax_due_krw=ctx.get("tax_due_after_official_data_krw"),
    )
    ctx["nhis_effect_notice"] = build_nhis_effect_notice_context(nhis_effect)
    ctx["nhis_visual_feedback"] = build_nhis_visual_feedback(nhis_effect)
    return render_template("overview.html", **ctx)

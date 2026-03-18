from __future__ import annotations

from flask import Blueprint, render_template, session

from core.auth import login_required
from core.time import utcnow
from domain.models import OfficialDataDocument, ReferenceMaterialItem


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

    return {
        "month_key": month_key,
        "official_nhis_count": int(official_nhis_count),
        "reference_material_count": int(reference_material_count),
    }


@web_nhis_bp.get("/dashboard/nhis")
@login_required
def index():
    user_pk = int(session["user_id"])
    return render_template("nhis.html", **_build_nhis_guide_context(user_pk))

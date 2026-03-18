from __future__ import annotations

from flask import Blueprint, render_template

from core.admin_guard import admin_required
from domain.models import OfficialDataDocument, ReferenceMaterialItem, Transaction, User


web_admin_bp = Blueprint("web_admin", __name__)


def _build_admin_snapshot() -> dict:
    return {
        "user_count": User.query.count(),
        "transaction_count": Transaction.query.count(),
        "official_data_count": OfficialDataDocument.query.count(),
        "reference_material_count": ReferenceMaterialItem.query.count(),
    }


@web_admin_bp.get("/admin")
@admin_required
def index():
    return render_template("admin/index.html", snapshot=_build_admin_snapshot())

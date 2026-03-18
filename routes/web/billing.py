from __future__ import annotations

from flask import Blueprint, render_template, session

from core.auth import login_required
from services.billing.pricing import build_pricing_page_context


web_billing_bp = Blueprint("web_billing", __name__)


@web_billing_bp.get("/pricing")
def pricing_page():
    user_pk = session.get("user_id")
    return render_template("pricing.html", **build_pricing_page_context(user_pk=user_pk))


@web_billing_bp.get("/dashboard/billing")
@login_required
def index():
    user_pk = session.get("user_id")
    return render_template("billing/index.html", **build_pricing_page_context(user_pk=user_pk))

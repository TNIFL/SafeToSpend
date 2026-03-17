from __future__ import annotations

from flask import Blueprint, render_template


web_legal_bp = Blueprint("web_legal", __name__)


@web_legal_bp.get("/privacy")
def privacy_page():
    return render_template(
        "legal/privacy.html",
        effective_date="2026-03-14",
        updated_date="2026-03-14",
    )


@web_legal_bp.get("/terms")
def terms_page():
    return render_template(
        "legal/terms.html",
        effective_date="2026-03-14",
        updated_date="2026-03-14",
    )


@web_legal_bp.get("/disclaimer")
def disclaimer_page():
    return render_template(
        "legal/disclaimer.html",
        effective_date="2026-03-14",
        updated_date="2026-03-14",
    )

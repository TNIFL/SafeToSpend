from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from core.admin_guard import admin_required
from services.admin_ops import build_ops_summary, clamp_days

web_admin_bp = Blueprint("web_admin", __name__)


@web_admin_bp.get("/admin")
@admin_required
def admin_index():
    return render_template("admin/index.html")


@web_admin_bp.get("/admin/support")
@admin_required
def admin_support():
    return redirect(url_for("web_support.admin_inquiries"))


@web_admin_bp.get("/admin/ops")
@admin_required
def admin_ops():
    days = clamp_days(request.args.get("days"))
    summary = build_ops_summary(days=days)
    return render_template("admin/ops.html", days=days, summary=summary)


@web_admin_bp.get("/admin/api/ops/summary")
@admin_required
def admin_ops_summary_api():
    days = clamp_days(request.args.get("days"))
    summary = build_ops_summary(days=days)
    return jsonify(summary)

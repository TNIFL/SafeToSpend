# routes/web/overview.py
from flask import Blueprint, render_template, session

from core.auth import login_required
from services.risk import compute_overview

web_overview_bp = Blueprint("web_overview", __name__)

@web_overview_bp.route("/overview", methods=["GET"])
@login_required
def overview():
    user_pk = session["user_id"]
    ctx = compute_overview(user_pk)
    return render_template("overview.html", **ctx)

from __future__ import annotations

from flask import Blueprint, flash, render_template, request, session

from core.auth import login_required
from domain.models import User


web_support_bp = Blueprint("web_support", __name__)


def _current_user() -> User:
    user_pk = int(session["user_id"])
    return User.query.filter_by(id=user_pk).first_or_404()


@web_support_bp.route("/support", methods=["GET", "POST"])
@login_required
def support_home():
    user = _current_user()
    submitted_preview = None
    subject = str(request.form.get("subject") or "").strip()
    message = str(request.form.get("message") or "").strip()

    if request.method == "POST":
        if len(subject) < 2:
            flash("문의 제목을 2자 이상 입력해 주세요.", "error")
        elif len(message) < 5:
            flash("문의 내용을 5자 이상 입력해 주세요.", "error")
        else:
            submitted_preview = {
                "subject": subject[:200],
                "message": message[:5000],
            }
            flash(
                "문의 저장 기능은 아직 연결되지 않았습니다. 입력한 내용은 저장되지 않았고, 현재는 안내 채널만 제공합니다.",
                "info",
            )

    return render_template(
        "support/form.html",
        user=user,
        draft_subject=subject,
        draft_message=message,
        submitted_preview=submitted_preview,
    )

# routes/web/dashboard.py
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, jsonify

from core.auth import login_required
from core.extensions import db
from services.reserve import preview
from services.dashboard_state import get_state, save_state
from domain.models import DashboardEntry

web_dashboard_bp = Blueprint("web_dashboard", __name__)


@web_dashboard_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    user_id = session["user_id"]

    # 저장(현재 월 기준값 업데이트 + 기록 추가)
    if request.method == "POST":
        note = (request.form.get("memo") or "").strip() or None
        rev = int(request.form.get("rev") or 0)
        exp = int(request.form.get("exp") or 0)
        rate = float(request.form.get("rate") or 0.15)

        # 1) 현재 상태 저장
        save_state(user_id, rev, exp, rate)

        # 2) 계산 기록(피드용) 저장: kind=calc, amount=쓸수있는돈
        result = preview(rev, exp, rate)
        entry = DashboardEntry(
            user_pk=user_id,
            kind="calc",
            amount=int(result["safe_to_spend"]),
            note=note,
        )
        db.session.add(entry)
        db.session.commit()

        flash("저장되었습니다.", "success")
        return redirect(url_for("web_dashboard.index"))

    # 현재 표시용 값은 user_dashboard_state에서 가져옴
    s = get_state(user_id)
    rev, exp, rate = s["rev"], s["exp"], s["rate"]
    result = preview(rev, exp, rate)

    entries = (
        DashboardEntry.query
        .filter_by(user_pk=user_id)
        .order_by(DashboardEntry.created_at.desc())
        .limit(30)
        .all()
    )

    has_setup = (rev > 0 or exp > 0)

    task_title = "이번 주 할 일 1개: 증빙 1개만 모아두기" if has_setup else "30초 세팅: 이번 달 입금/비용 입력하기"
    task_cta = "증빙 올리기" if has_setup else "지금 입력하기"
    task_anchor = "#" if has_setup else "#quick"

    return render_template(
        "dashboard.html",
        entries=entries,
        has_setup=has_setup,
        rev=rev, exp=exp, rate=rate,
        safe_to_spend=result["safe_to_spend"],
        reserve_amount=result["reserve_amount"],
        profit=result["profit"],
        task_title=task_title,
        task_cta=task_cta,
        task_anchor=task_anchor,
    )


@web_dashboard_bp.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id: int):
    user_id = session["user_id"]
    entry = DashboardEntry.query.filter_by(id=entry_id, user_pk=user_id).first_or_404()

    if request.method == "POST":
        entry.kind = (request.form.get("kind") or "calc").strip()[:16] or "calc"
        entry.amount = int(request.form.get("amount") or 0)
        entry.note = (request.form.get("note") or "").strip()[:255] or None
        db.session.commit()

        wants_json = (
            request.headers.get("X-Requested-With") == "fetch"
            or request.accept_mimetypes.best == "application/json"
        )
        if wants_json:
            return jsonify({
                "id": entry.id,
                "kind": entry.kind,
                "amount": int(entry.amount),
                "note": entry.note or "",
                "date": entry.created_at.strftime("%Y-%m-%d"),
            })

        flash("수정되었습니다.", "success")
        return redirect(url_for("web_dashboard.index"))

    return render_template("dashboard_entry_edit.html", entry=entry)


@web_dashboard_bp.route("/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_entry(entry_id: int):
    user_id = session["user_id"]
    entry = DashboardEntry.query.filter_by(id=entry_id, user_pk=user_id).first_or_404()

    db.session.delete(entry)
    db.session.commit()

    wants_json = (
        request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return jsonify({"ok": True, "id": entry_id})

    flash("삭제되었습니다.", "success")
    return redirect(url_for("web_dashboard.index"))

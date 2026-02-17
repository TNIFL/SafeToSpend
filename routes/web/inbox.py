# routes/web/inbox.py
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_file
from urllib.parse import urlparse

from services.evidence_store import attach_evidence_file, delete_evidence_file, evidence_abs_path

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, ExpenseLabel, IncomeLabel
from services.risk import compute_inbox, compute_inbox_counts

web_inbox_bp = Blueprint("web_inbox", __name__)


def _safe_next_url(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        u = urlparse(raw)
        # 같은 사이트 내 상대 경로만 허용
        if u.scheme or u.netloc:
            return None
        if not u.path.startswith("/"):
            return None
        return u.path + (f"?{u.query}" if u.query else "")
    except Exception:
        return None

@web_inbox_bp.route("/inbox", methods=["GET"])
@login_required
def index():
    user_pk = session["user_id"]
    tab = request.args.get("tab", "evidence")
    if tab not in ("evidence", "mixed", "income"):
        tab = "evidence"

    counts = compute_inbox_counts(user_pk)
    items = compute_inbox(user_pk, tab=tab, limit=60)

    return render_template("inbox.html", tab=tab, counts=counts, items=items)

@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/mark", methods=["POST"])
@login_required
def evidence_mark(evidence_id: int):
    user_pk = session["user_id"]
    status = request.form.get("status", "attached")
    tab = request.form.get("tab", "evidence")

    if status not in ("attached", "not_needed", "missing"):
        status = "attached"

    row = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not row:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab=tab))

    row.status = status
    db.session.commit()
    flash("처리되었습니다.", "success")
    nxt = _safe_next_url(request.form.get("next") or request.referrer)
    return redirect(nxt or url_for("web_inbox.index", tab=tab))


@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/upload", methods=["POST"])
@login_required
def evidence_upload(evidence_id: int):
    user_pk = session["user_id"]
    tab = request.form.get("tab", "evidence")
    f = request.files.get("file")

    try:
        attach_evidence_file(user_pk=user_pk, evidence_id=evidence_id, uploaded=f, max_bytes=int(request.max_content_length or 0) or (20 * 1024 * 1024))
        flash("증빙이 업로드되었습니다.", "success")
    except Exception as e:
        flash(str(e) or "업로드에 실패했습니다.", "error")

    nxt = _safe_next_url(request.form.get("next") or request.referrer)
    return redirect(nxt or url_for("web_inbox.index", tab=tab))


@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/download", methods=["GET"])
@login_required
def evidence_download(evidence_id: int):
    user_pk = session["user_id"]
    ev = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not ev or not ev.file_key:
        flash("파일이 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab="evidence"))

    p = evidence_abs_path(ev.file_key)
    if not p.exists():
        flash("파일을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab="evidence"))

    return send_file(
        p,
        as_attachment=True,
        download_name=(ev.original_filename or p.name),
        mimetype=(ev.mime_type or "application/octet-stream"),
        max_age=0,
    )


@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/delete", methods=["POST"])
@login_required
def evidence_delete(evidence_id: int):
    user_pk = session["user_id"]
    tab = request.form.get("tab", "evidence")
    try:
        delete_evidence_file(user_pk=user_pk, evidence_id=evidence_id)
        flash("즉시 삭제되었습니다.", "success")
    except Exception as e:
        flash(str(e) or "삭제에 실패했습니다.", "error")
    nxt = _safe_next_url(request.form.get("next") or request.referrer)
    return redirect(nxt or url_for("web_inbox.index", tab=tab))

@web_inbox_bp.route("/inbox/expense/<int:label_id>/label", methods=["POST"])
@login_required
def expense_label(label_id: int):
    user_pk = session["user_id"]
    status = request.form.get("status", "business")
    tab = request.form.get("tab", "mixed")

    if status not in ("business", "personal", "mixed", "unknown"):
        status = "business"

    row = ExpenseLabel.query.filter_by(id=label_id, user_pk=user_pk).first()
    if not row:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab=tab))

    row.status = status
    row.labeled_by = "user"
    db.session.commit()
    flash("라벨이 저장되었습니다.", "success")
    return redirect(url_for("web_inbox.index", tab=tab))

@web_inbox_bp.route("/inbox/income/<int:label_id>/label", methods=["POST"])
@login_required
def income_label(label_id: int):
    user_pk = session["user_id"]
    status = request.form.get("status", "income")
    tab = request.form.get("tab", "income")

    if status not in ("income", "non_income", "unknown"):
        status = "income"

    row = IncomeLabel.query.filter_by(id=label_id, user_pk=user_pk).first()
    if not row:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab=tab))

    row.status = status
    row.labeled_by = "user"
    db.session.commit()
    flash("라벨이 저장되었습니다.", "success")
    return redirect(url_for("web_inbox.index", tab=tab))

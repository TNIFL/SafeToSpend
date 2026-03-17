from __future__ import annotations

from datetime import timedelta
import secrets
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from core.admin_guard import admin_required
from core.extensions import db
from core.time import utcnow
from domain.models import Inquiry, User
from services.input_sanitize import safe_str
from services.security_audit import audit_event

web_support_bp = Blueprint("web_support", __name__)
_SUBMIT_COOLDOWN_SECONDS = 30
_SUBMIT_HOURLY_LIMIT = 20
_MY_LIST_PER_PAGE = 20
_ADMIN_LIST_PER_PAGE = 30


def _current_user() -> User | None:
    try:
        uid = int(session.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        return None
    return User.query.filter_by(id=uid).first()


def _csrf_token() -> str:
    token = str(session.get("_csrf_token") or "").strip()
    if not token:
        token = secrets.token_hex(16)
        session["_csrf_token"] = token
        session.modified = True
    return token


def _verify_csrf() -> bool:
    expected = str(session.get("_csrf_token") or "").strip()
    actual = str(request.form.get("csrf_token") or "").strip()
    return bool(expected and actual and secrets.compare_digest(expected, actual))


def _status_label(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "answered":
        return "답변 완료"
    if s == "closed":
        return "종료"
    return "대기 중"


def _render_login_needed() -> str:
    return render_template(
        "support/login_required.html",
        login_url=url_for("web_auth.login", next=request.path),
        register_url=url_for("web_auth.register", next=request.path),
    )


def _parse_page(value: str | None, default: int = 1) -> int:
    try:
        page = int(str(value or "").strip() or default)
    except (TypeError, ValueError):
        page = default
    if page <= 0:
        return default
    return min(page, 500)


def _paged_rows(query, page: int, per_page: int):
    offset = (page - 1) * per_page
    rows = query.offset(offset).limit(per_page + 1).all()
    has_next = len(rows) > per_page
    rows = rows[:per_page]
    has_prev = page > 1
    return rows, has_prev, has_next


def _inquiry_table_ready() -> bool:
    try:
        return bool(inspect(db.engine).has_table("inquiries"))
    except Exception:
        return False


@web_support_bp.route("/support", methods=["GET", "POST"])
def support_home():
    user = _current_user()
    if not user:
        if request.method == "POST":
            flash("문의 작성은 로그인 후 이용할 수 있어요.", "error")
        return _render_login_needed()

    if not _inquiry_table_ready():
        if request.method == "POST":
            flash("문의 기능 설정이 아직 완료되지 않았어요. 잠시 후 다시 시도해 주세요.", "error")
        return render_template(
            "support/form.html",
            csrf_token=_csrf_token(),
            my_url=url_for("web_support.support_my"),
            support_unavailable=True,
        )

    if request.method == "POST":
        if not _verify_csrf():
            flash("요청을 확인할 수 없어요. 다시 시도해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))

        subject = safe_str(request.form.get("subject"), max_len=200)
        message = safe_str(request.form.get("message"), max_len=5000, allow_newline=True)
        if len(subject) < 2:
            flash("제목을 2자 이상 입력해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))
        if len(subject) > 200:
            flash("제목은 200자 이내로 입력해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))
        if len(message) < 5:
            flash("문의 내용을 조금 더 자세히 입력해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))
        if len(message) > 5000:
            flash("문의 내용은 5000자 이내로 입력해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))

        try:
            latest = (
                Inquiry.query.filter_by(user_pk=int(user.id))
                .order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
                .first()
            )
        except SQLAlchemyError:
            db.session.rollback()
            flash("문의 기능을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))
        if latest and latest.created_at:
            delta_sec = int((utcnow() - latest.created_at).total_seconds())
            if delta_sec < _SUBMIT_COOLDOWN_SECONDS:
                wait_sec = max(1, _SUBMIT_COOLDOWN_SECONDS - max(delta_sec, 0))
                flash(f"문의는 {wait_sec}초 후에 다시 보낼 수 있어요.", "error")
                return redirect(url_for("web_support.support_home"))
        try:
            recent_count = (
                Inquiry.query.filter(Inquiry.user_pk == int(user.id))
                .filter(Inquiry.created_at >= (utcnow() - timedelta(hours=1)))
                .count()
            )
        except SQLAlchemyError:
            db.session.rollback()
            recent_count = 0
        if recent_count >= _SUBMIT_HOURLY_LIMIT:
            flash("문의가 많이 접수되어 잠시 후 다시 시도해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))

        row = Inquiry(
            user_pk=int(user.id),
            subject=subject,
            message=message,
            status="open",
            created_at=utcnow(),
        )
        try:
            db.session.add(row)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("문의를 저장하지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
            return redirect(url_for("web_support.support_home"))

        flash("문의가 접수되었어요. 답변이 등록되면 여기서 바로 확인할 수 있어요.", "success")
        return redirect(url_for("web_support.support_detail", inquiry_id=row.id))

    return render_template(
        "support/form.html",
        csrf_token=_csrf_token(),
        my_url=url_for("web_support.support_my"),
        support_unavailable=False,
    )


@web_support_bp.get("/support/my")
def support_my():
    user = _current_user()
    if not user:
        return _render_login_needed()
    if not _inquiry_table_ready():
        flash("문의 기능 설정이 아직 완료되지 않았어요. 잠시 후 다시 시도해 주세요.", "error")
        return render_template(
            "support/my_list.html",
            inquiries=[],
            status_label=_status_label,
            write_url=url_for("web_support.support_home"),
            page=1,
            has_prev=False,
            has_next=False,
            query_error=True,
            feature_unavailable=True,
        )

    page = _parse_page(request.args.get("page"), 1)
    query_error = False
    try:
        base_q = Inquiry.query.filter_by(user_pk=int(user.id)).order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
        rows, has_prev, has_next = _paged_rows(base_q, page, _MY_LIST_PER_PAGE)
    except SQLAlchemyError:
        db.session.rollback()
        flash("문의 목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        rows, has_prev, has_next = [], False, False
        query_error = True
    return render_template(
        "support/my_list.html",
        inquiries=rows,
        status_label=_status_label,
        write_url=url_for("web_support.support_home"),
        page=page,
        has_prev=has_prev,
        has_next=has_next,
        query_error=query_error,
        feature_unavailable=False,
    )


@web_support_bp.get("/support/my/<int:inquiry_id>")
def support_detail(inquiry_id: int):
    user = _current_user()
    if not user:
        return _render_login_needed()
    if not _inquiry_table_ready():
        flash("문의 기능 설정이 아직 완료되지 않았어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_support.support_home"))

    try:
        row = Inquiry.query.filter_by(id=inquiry_id, user_pk=int(user.id)).first()
    except SQLAlchemyError:
        db.session.rollback()
        flash("문의 상세를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_support.support_my"))
    if not row:
        abort(404)

    if row.admin_reply:
        try:
            row.last_viewed_by_user_at = utcnow()
            db.session.add(row)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

    return render_template(
        "support/my_detail.html",
        inquiry=row,
        status_label=_status_label,
        list_url=url_for("web_support.support_my"),
    )


@web_support_bp.get("/admin/inquiries")
@admin_required
def admin_inquiries():
    if not _inquiry_table_ready():
        flash("문의 관리 기능 설정이 아직 완료되지 않았어요(개발용). 마이그레이션을 확인해 주세요.", "error")
        return render_template(
            "admin/inquiries_list.html",
            inquiries=[],
            email_map={},
            status="all",
            status_label=_status_label,
            page=1,
            has_prev=False,
            has_next=False,
            query_error=True,
            feature_unavailable=True,
        )

    status = safe_str(request.args.get("status") or "all", max_len=12).lower()
    if status not in {"all", "open", "answered", "closed"}:
        status = "all"
    page = _parse_page(request.args.get("page"), 1)
    query_error = False

    try:
        q = Inquiry.query
        if status != "all":
            q = q.filter(Inquiry.status == status)
        q = q.order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
        rows, has_prev, has_next = _paged_rows(q, page, _ADMIN_LIST_PER_PAGE)
        user_ids = sorted({int(x.user_pk) for x in rows})
        users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
        email_map = {int(u.id): (u.email or "") for u in users}
    except SQLAlchemyError:
        db.session.rollback()
        flash("문의 관리 목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        rows, has_prev, has_next, email_map = [], False, False, {}
        query_error = True

    return render_template(
        "admin/inquiries_list.html",
        inquiries=rows,
        email_map=email_map,
        status=status,
        status_label=_status_label,
        page=page,
        has_prev=has_prev,
        has_next=has_next,
        query_error=query_error,
        feature_unavailable=False,
    )


@web_support_bp.get("/admin/inquiries/<int:inquiry_id>")
@admin_required
def admin_inquiry_detail(inquiry_id: int):
    if not _inquiry_table_ready():
        flash("문의 관리 기능 설정이 아직 완료되지 않았어요(개발용). 마이그레이션을 확인해 주세요.", "error")
        return redirect(url_for("web_support.admin_inquiries"))

    try:
        row = Inquiry.query.filter_by(id=inquiry_id).first()
    except SQLAlchemyError:
        db.session.rollback()
        flash("문의 상세를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_support.admin_inquiries"))
    if not row:
        abort(404)

    try:
        user = User.query.filter_by(id=row.user_pk).first()
    except SQLAlchemyError:
        db.session.rollback()
        user = None
    return render_template(
        "admin/inquiries_detail.html",
        inquiry=row,
        user_email=(user.email if user else "알 수 없음"),
        status_label=_status_label,
        csrf_token=_csrf_token(),
    )


@web_support_bp.post("/admin/inquiries/<int:inquiry_id>/reply")
@admin_required
def admin_inquiry_reply(inquiry_id: int):
    if not _inquiry_table_ready():
        flash("문의 관리 기능 설정이 아직 완료되지 않았어요(개발용). 마이그레이션을 확인해 주세요.", "error")
        return redirect(url_for("web_support.admin_inquiries"))

    if not _verify_csrf():
        flash("요청을 확인할 수 없어요. 다시 시도해 주세요.", "error")
        audit_event("admin_action_denied", outcome="denied", detail="admin inquiry reply csrf fail")
        return redirect(url_for("web_support.admin_inquiry_detail", inquiry_id=inquiry_id))

    try:
        row = Inquiry.query.filter_by(id=inquiry_id).first()
    except SQLAlchemyError:
        db.session.rollback()
        flash("문의 데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_support.admin_inquiries"))
    if not row:
        abort(404)

    posted_lock = safe_str(request.form.get("reply_lock"), max_len=64)
    current_lock = (row.replied_at.isoformat() if row.replied_at else "")
    if posted_lock != current_lock:
        flash("다른 관리자가 먼저 답변을 수정했어요. 화면을 새로고침해 최신 내용을 확인해 주세요.", "error")
        audit_event("admin_action_denied", outcome="denied", detail="admin inquiry lock conflict")
        return redirect(url_for("web_support.admin_inquiry_detail", inquiry_id=inquiry_id))

    reply = safe_str(request.form.get("admin_reply"), max_len=5000, allow_newline=True)
    if len(reply) < 2:
        flash("답변 내용을 2자 이상 입력해 주세요.", "error")
        return redirect(url_for("web_support.admin_inquiry_detail", inquiry_id=inquiry_id))
    if len(reply) > 5000:
        flash("답변 내용은 5000자 이내로 입력해 주세요.", "error")
        return redirect(url_for("web_support.admin_inquiry_detail", inquiry_id=inquiry_id))

    next_status = safe_str(request.form.get("status") or "answered", max_len=16).lower()
    if next_status not in {"answered", "closed"}:
        next_status = "answered"

    row.admin_reply = reply
    row.status = next_status
    row.replied_at = utcnow()
    try:
        db.session.add(row)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("답변 저장 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.", "error")
        audit_event("admin_action_failed", outcome="denied", detail="admin inquiry reply save failed")
        return redirect(url_for("web_support.admin_inquiry_detail", inquiry_id=inquiry_id))

    flash("답변을 저장했어요.", "success")
    audit_event(
        "admin_action",
        outcome="ok",
        detail=f"inquiry_reply:{inquiry_id}",
        extra={"status": next_status},
    )
    return redirect(url_for("web_support.admin_inquiry_detail", inquiry_id=inquiry_id))

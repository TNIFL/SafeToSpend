# routes/web/dashboard.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, session, redirect, url_for, flash, jsonify
from sqlalchemy import and_, func, or_

from core.auth import login_required
from core.extensions import db
from domain.models import DashboardEntry, EvidenceItem, Transaction
from services.input_sanitize import parse_date_ym
from services.nhis_runtime import compute_nhis_monthly_buffer
from services.official_refs.guard import check_nhis_ready
from services.reserve import preview
from services.risk import compute_tax_estimate
from services.dashboard_state import get_state, save_state
from services.bank_accounts import list_accounts_for_ui

web_dashboard_bp = Blueprint("web_dashboard", __name__)
KST = ZoneInfo("Asia/Seoul")


def _recent_month_keys(count: int = 6) -> list[str]:
    safe_count = max(1, min(int(count or 6), 12))
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    year = int(now_kst.year)
    month = int(now_kst.month)
    out: list[str] = []
    for _ in range(safe_count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month <= 0:
            month = 12
            year -= 1
    out.reverse()
    return out


def _month_range_naive(month_key: str) -> tuple[datetime, datetime]:
    year_str, month_str = str(month_key or "").split("-", 1)
    year = int(year_str)
    month = int(month_str)
    start = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0)
    return start, end


def _current_month_key() -> str:
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    return f"{int(now_kst.year):04d}-{int(now_kst.month):02d}"


def _evidence_completion_snapshot(user_pk: int, month_key: str) -> dict[str, int | bool | float | None]:
    try:
        start_dt, end_dt = _month_range_naive(month_key)
    except Exception:
        return {
            "denominator": 0,
            "numerator": 0,
            "remaining": 0,
            "has_target": False,
            "rate_pct": None,
        }
    try:
        required_total = (
            db.session.query(func.count(EvidenceItem.id))
            .join(
                Transaction,
                and_(
                    EvidenceItem.transaction_id == Transaction.id,
                    EvidenceItem.user_pk == Transaction.user_pk,
                ),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(EvidenceItem.requirement.in_(("required", "maybe")))
            .scalar()
        ) or 0
        attached_total = (
            db.session.query(func.count(EvidenceItem.id))
            .join(
                Transaction,
                and_(
                    EvidenceItem.transaction_id == Transaction.id,
                    EvidenceItem.user_pk == Transaction.user_pk,
                ),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(EvidenceItem.requirement.in_(("required", "maybe")))
            .filter(or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)))
            .scalar()
        ) or 0
        denominator = int(required_total or 0)
        numerator = int(attached_total or 0)
        remaining = max(0, denominator - numerator)
        return {
            "denominator": denominator,
            "numerator": numerator,
            "remaining": remaining,
            "has_target": bool(denominator > 0),
            "rate_pct": (round((numerator * 100.0 / denominator), 1) if denominator > 0 else None),
        }
    except Exception:
        db.session.rollback()
        return {
            "denominator": 0,
            "numerator": 0,
            "remaining": 0,
            "has_target": False,
            "rate_pct": None,
        }


def _evidence_completion_ratio_pct(user_pk: int, month_key: str) -> float | None:
    snapshot = _evidence_completion_snapshot(user_pk=user_pk, month_key=month_key)
    if not bool(snapshot.get("has_target")):
        return None
    rate = snapshot.get("rate_pct")
    return float(rate) if isinstance(rate, (int, float)) else None


def _signed_number_text(value: float | int, *, digits: int = 0, suffix: str = "") -> str:
    num = float(value or 0.0)
    if digits <= 0:
        raw = f"{int(round(num)):,}"
    else:
        raw = f"{num:.{digits}f}"
    if num > 0:
        raw = f"+{raw}"
    return f"{raw}{suffix}"


def _last_two_valid(values: list[float | int | None]) -> tuple[float, float] | None:
    valid = [float(v) for v in values if isinstance(v, (int, float))]
    if len(valid) < 2:
        return None
    return valid[-2], valid[-1]


@web_dashboard_bp.route("/guide", methods=["GET"])
@login_required
def guide():
    _ = int(session["user_id"])
    month_key = parse_date_ym(request.args.get("month")) or _current_month_key()
    from_page_raw = str(request.args.get("from") or "").strip().lower()
    if from_page_raw not in {"dashboard", "review", "tax-buffer", "nhis", "package", "vault", "calendar", "reconcile"}:
        from_page = ""
    else:
        from_page = from_page_raw

    calendar_url = url_for("web_calendar.month_calendar", month=month_key)
    review_url = url_for("web_calendar.review", month=month_key)
    review_required_url = url_for("web_calendar.review", month=month_key, lane="required", focus="receipt_required", q="", limit=30)
    reconcile_url = url_for("web_calendar.reconcile", month=month_key)
    tax_buffer_url = url_for("web_calendar.tax_buffer", month=month_key)
    nhis_url = url_for("web_profile.nhis_page", month=month_key)
    vault_url = url_for("web_vault.index", month=month_key)
    package_url = url_for("web_package.page", month=month_key)
    dashboard_url = url_for("web_dashboard.index")
    import_url = url_for("web_inbox.import_page")
    refs_ready = bool((check_nhis_ready() or {}).get("ready"))
    primary_anchor_map = {
        "dashboard": "order",
        "calendar": "calendar",
        "review": "review",
        "tax-buffer": "tax-buffer",
        "nhis": "nhis",
        "vault": "vault",
        "reconcile": "reconcile",
        "package": "package",
    }
    primary_anchor = primary_anchor_map.get(from_page, "order")

    return render_template(
        "dashboard_guide.html",
        month_key=month_key,
        from_page=from_page,
        primary_anchor=primary_anchor,
        refs_ready=refs_ready,
        dashboard_url=dashboard_url,
        import_url=import_url,
        calendar_url=calendar_url,
        review_url=review_url,
        review_required_url=review_required_url,
        reconcile_url=reconcile_url,
        tax_buffer_url=tax_buffer_url,
        nhis_url=nhis_url,
        vault_url=vault_url,
        package_url=package_url,
    )


@web_dashboard_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    user_id = session["user_id"]
    month_key = _current_month_key()
    account_filter_raw = str(request.args.get("account") or "all").strip().lower()
    account_filter_label = ""
    if account_filter_raw and account_filter_raw != "all":
        if account_filter_raw == "unassigned":
            account_filter_label = "미지정"
        else:
            try:
                account_filter_id = int(account_filter_raw)
            except Exception:
                account_filter_id = 0
            if account_filter_id > 0:
                account_options = list_accounts_for_ui(int(user_id))
                selected = next((row for row in account_options if int(row.get("id") or 0) == account_filter_id), None)
                if selected:
                    account_filter_label = str(selected.get("display_name") or "선택 계좌")

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

    trend_payload = {
        "labels": [],
        "series": {"nhis": [], "tax": [], "completion": []},
        "visible": {"nhis": False, "tax": False, "completion": False},
        "comments": {
            "nhis": "데이터가 더 쌓이면 지난달 대비 변화를 보여드릴게요.",
            "tax": "데이터가 더 쌓이면 지난달 대비 변화를 보여드릴게요.",
            "completion": "데이터가 더 쌓이면 지난달 대비 변화를 보여드릴게요.",
        },
        "empty_messages": {
            "nhis": "데이터가 쌓이면 보여드릴게요.",
            "tax": "공식 계산 기준이 충분할 때 보여드릴게요.",
            "completion": "증빙 데이터가 쌓이면 보여드릴게요.",
        },
    }
    try:
        month_keys = _recent_month_keys(6)
        trend_payload["labels"] = month_keys

        nhis_ready = bool((check_nhis_ready() or {}).get("ready"))
        nhis_series: list[int | None] = []
        if nhis_ready:
            for mk in month_keys:
                try:
                    amount, _note, payload = compute_nhis_monthly_buffer(user_id, month_key=mk)
                    estimate = dict((payload or {}).get("estimate") or {})
                    can_estimate = bool(estimate.get("can_estimate"))
                    value = int(amount or 0)
                    nhis_series.append(max(0, value) if (can_estimate or value > 0) else None)
                except Exception:
                    db.session.rollback()
                    nhis_series.append(None)
        else:
            nhis_series = [None for _ in month_keys]
            trend_payload["empty_messages"]["nhis"] = "공식 기준 준비가 끝나면 보여드릴게요."
        trend_payload["series"]["nhis"] = nhis_series
        trend_payload["visible"]["nhis"] = any(v is not None for v in nhis_series)

        tax_series: list[int | None] = []
        for mk in month_keys:
            try:
                tax_est = compute_tax_estimate(user_id, month_key=mk)
                if bool(getattr(tax_est, "official_calculable", False)):
                    tax_series.append(max(0, int(getattr(tax_est, "buffer_target_krw", 0) or 0)))
                else:
                    tax_series.append(None)
            except Exception:
                db.session.rollback()
                tax_series.append(None)
        trend_payload["series"]["tax"] = tax_series
        trend_payload["visible"]["tax"] = any(v is not None for v in tax_series)

        completion_series: list[float | None] = []
        for mk in month_keys:
            try:
                completion_series.append(_evidence_completion_ratio_pct(user_id, mk))
            except Exception:
                db.session.rollback()
                completion_series.append(None)
        trend_payload["series"]["completion"] = completion_series
        trend_payload["visible"]["completion"] = any(v is not None for v in completion_series)

        nhis_pair = _last_two_valid(nhis_series)
        if nhis_pair:
            delta = nhis_pair[1] - nhis_pair[0]
            trend_payload["comments"]["nhis"] = f"지난달 대비 {_signed_number_text(delta, digits=0, suffix='원')}"
        tax_pair = _last_two_valid(tax_series)
        if tax_pair:
            delta = tax_pair[1] - tax_pair[0]
            trend_payload["comments"]["tax"] = f"지난달 대비 {_signed_number_text(delta, digits=0, suffix='원')}"
        completion_pair = _last_two_valid(completion_series)
        if completion_pair:
            delta = completion_pair[1] - completion_pair[0]
            trend_payload["comments"]["completion"] = f"완성률 {_signed_number_text(delta, digits=1, suffix='%p')}"
    except Exception:
        db.session.rollback()

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
        trend_payload=trend_payload,
        account_filter_label=account_filter_label,
        account_filter_value=(account_filter_raw or "all"),
        month_key=month_key,
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

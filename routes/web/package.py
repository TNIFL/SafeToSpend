# routes/web/package.py
from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, flash, redirect, render_template, request, send_file, session, url_for
from sqlalchemy import func

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, Transaction, UserBankAccount
from services.analytics_events import record_seasonal_card_event
from services.onboarding import tax_profile_summary
from services.plan import (
    PlanPermissionError,
    can_download_package,
    ensure_can_download_package,
    get_user_entitlements,
    plan_label_ko,
)
from services.rate_limit import client_ip, hit_limit
from services.seasonal_ux import (
    activate_pending_seasonal_card,
    build_seasonal_experience,
    build_seasonal_screen_context,
    build_seasonal_tracking_query_params,
    clear_active_seasonal_card,
    clear_pending_seasonal_card,
    decorate_seasonal_context_for_tracking,
    decorate_seasonal_cards_for_tracking,
    get_active_seasonal_card,
    seasonal_card_completion_state,
    seasonal_metric_payload_from_landing_args,
    set_active_seasonal_card,
)
from services.security_audit import audit_event
from services.tax_package import build_tax_package_zip
from services.tax_package import build_tax_package_preview
from services.input_sanitize import parse_bool_yn, parse_date_ym, safe_str
from services.bank_accounts import display_name as bank_account_display_name, list_accounts_for_ui

# vault의 월/생성 로직 재사용
from routes.web.vault import _ensure_month_evidence_rows, _month_dt_range, _month_key, _parse_month

# ✅ 기본은 /dashboard/package
# 만약 네가 진짜로 /package 를 쓰고 싶으면 url_prefix="/dashboard" 를 "" 로 바꾸면 됨.
web_package_bp = Blueprint("web_package", __name__, url_prefix="/dashboard")


def _normalize_package_account_filter(user_pk: int, raw_value: str | None) -> tuple[str, str, int]:
    account_filter_value = safe_str(raw_value, max_len=32).lower()
    account_filter_name = "전체 계좌"
    account_filter_id = 0
    if account_filter_value in ("", "all"):
        return "all", account_filter_name, account_filter_id
    if account_filter_value == "unassigned":
        return "unassigned", "미지정", account_filter_id

    try:
        account_filter_id = int(account_filter_value)
    except Exception:
        account_filter_id = 0
    if account_filter_id <= 0:
        return "all", account_filter_name, 0

    options = list_accounts_for_ui(user_pk, keep_ids=[account_filter_id])
    selected = next((x for x in options if int(x.get("id") or 0) == account_filter_id), None)
    if not selected:
        return "all", account_filter_name, 0
    return str(account_filter_id), str(selected.get("display_name") or "선택 계좌"), account_filter_id


def _apply_tx_account_filter(query, account_filter_value: str, account_filter_id: int):
    if account_filter_value == "unassigned":
        return query.filter(Transaction.bank_account_id.is_(None))
    if int(account_filter_id or 0) > 0:
        return query.filter(Transaction.bank_account_id == int(account_filter_id))
    return query


def _with_account_query(url: str, account_filter_value: str) -> str:
    base = str(url or "").strip()
    if not base:
        return base
    if account_filter_value in ("", "all"):
        return base
    try:
        parts = urlsplit(base)
        query_pairs = dict(parse_qsl(parts.query, keep_blank_values=True))
        query_pairs["account"] = account_filter_value
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_pairs), parts.fragment))
    except Exception:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}account={account_filter_value}"


@web_package_bp.get("/package")
@login_required
def page():
    # ✅ user_id를 인자로 받지 않는다. (세션에서 꺼낸다)
    user_pk = int(session["user_id"])

    month_first = _parse_month(request.args.get("month"))
    month_key = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)
    account_filter_value, account_filter_name, account_filter_id = _normalize_package_account_filter(
        user_pk,
        request.args.get("account"),
    )

    # 증빙 row 보장(누락도 리스트에 포함시키기 위해)
    _ensure_month_evidence_rows(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    # 월 거래(지출 중심 + 증빙 join)
    base = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
    )
    base = _apply_tx_account_filter(base, account_filter_value, account_filter_id)

    # 누락 리스트(필수/검토인데 missing 인 것)
    missing_rows = (
        base.filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement.in_(["required", "maybe"]))
        .all()
    )
    missing_account_map: dict[int, str] = {}
    missing_account_ids = {
        int(getattr(tx, "bank_account_id", 0) or 0)
        for _, tx in missing_rows
        if int(getattr(tx, "bank_account_id", 0) or 0) > 0
    }
    account_label_map: dict[int, str] = {}
    if missing_account_ids:
        account_rows = (
            db.session.query(UserBankAccount)
            .filter(UserBankAccount.user_pk == user_pk)
            .filter(UserBankAccount.id.in_(list(missing_account_ids)))
            .all()
        )
        account_label_map = {int(row.id): bank_account_display_name(row) for row in account_rows if row and row.id}
    for _, tx in missing_rows:
        tx_id = int(getattr(tx, "id", 0) or 0)
        if tx_id <= 0:
            continue
        account_id = int(getattr(tx, "bank_account_id", 0) or 0)
        if account_id > 0:
            missing_account_map[tx_id] = str(account_label_map.get(account_id) or "선택 계좌")
        else:
            missing_account_map[tx_id] = "미지정"

    # 상단 지표
    gross_income = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "in")
    )
    gross_income = _apply_tx_account_filter(gross_income, account_filter_value, account_filter_id).scalar()
    gross_income = gross_income or 0

    total_out = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "out")
    )
    total_out = _apply_tx_account_filter(total_out, account_filter_value, account_filter_id).scalar() or 0

    missing_total = len(missing_rows)
    missing_required = sum(1 for ev, _ in missing_rows if ev.requirement == "required")
    missing_maybe = sum(1 for ev, _ in missing_rows if ev.requirement == "maybe")

    # 첨부/누락 진행률 계산용 (해당 월의 evidence 상태 카운트)
    attached_total = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.status == "attached")
    )
    attached_total = _apply_tx_account_filter(attached_total, account_filter_value, account_filter_id).scalar() or 0

    # 누락은 required/maybe + missing
    denom = attached_total + missing_total

    # month nav (항상 안전한 방식)
    prev_month = (month_first.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m")
    next_month = (month_first.replace(day=28) + timedelta(days=10)).replace(day=1).strftime("%Y-%m")

    zip_preview = build_tax_package_preview(
        user_pk=user_pk,
        month_key=month_key,
        account_filter=account_filter_value,
        account_id=account_filter_id,
    )
    top_issues = (((zip_preview or {}).get("preflight") or {}).get("top_issues") or [])
    for issue in top_issues:
        if not isinstance(issue, dict):
            continue
        issue["action_url"] = _with_account_query(str(issue.get("action_url") or ""), account_filter_value)

    preflight = (zip_preview or {}).get("preflight") or {}
    profile_summary = tax_profile_summary(user_pk)
    ent = get_user_entitlements(user_pk)
    can_package_download_flag = bool(can_download_package(user_pk))
    seasonal_experience = build_seasonal_experience(
        user_pk=int(user_pk),
        month_key=month_key,
        urls={
            "review": url_for("web_calendar.review", month=month_key, focus="receipt_required", account=(account_filter_value or None)),
            "tax_buffer": url_for("web_calendar.tax_buffer", month=month_key, account=(account_filter_value or None)),
            "package": url_for("web_package.page", month=month_key, account=(account_filter_value or None)),
            "profile": url_for(
                "web_profile.tax_profile",
                step=2,
                next=url_for("web_package.page", month=month_key, account=(account_filter_value or None)),
                return_to_next=1,
                recovery_source="season_package_card",
            ),
        },
    )
    seasonal_experience = decorate_seasonal_cards_for_tracking(
        seasonal_experience,
        source_screen="package",
        month_key=month_key,
        click_url_builder=lambda metric_payload, target_url: url_for(
            "web_overview.seasonal_card_click",
            **build_seasonal_tracking_query_params(metric_payload, redirect_to=str(target_url or "")),
        ),
    )
    seasonal_context = build_seasonal_screen_context(seasonal_experience, "package")
    seasonal_context = decorate_seasonal_context_for_tracking(
        seasonal_context,
        month_key=month_key,
        click_url_builder=lambda metric_payload, target_url: url_for(
            "web_overview.seasonal_card_click",
            **build_seasonal_tracking_query_params(metric_payload, redirect_to=str(target_url or "")),
        ),
    )
    if seasonal_context and isinstance(seasonal_context.get("metric_payload"), dict):
        metric_payload = dict(seasonal_context.get("metric_payload") or {})
        record_seasonal_card_event(
            user_pk=int(user_pk),
            event="seasonal_card_shown",
            route="web_package.page",
            season_focus=str(metric_payload.get("season_focus") or ""),
            card_type=str(metric_payload.get("card_type") or ""),
            cta_target=str(metric_payload.get("cta_target") or ""),
            source_screen="package",
            priority=int(metric_payload.get("priority") or 0),
            completion_state_before=str(metric_payload.get("completion_state_before") or "todo"),
            month_key=str(metric_payload.get("month_key") or month_key),
        )
    landing_payload = seasonal_metric_payload_from_landing_args(request.args)
    if landing_payload and str(landing_payload.get("cta_target") or "") == "package":
        if str(landing_payload.get("completion_action") or ""):
            active_metric = activate_pending_seasonal_card(session) or set_active_seasonal_card(session, landing_payload)
        else:
            clear_pending_seasonal_card(session)
            active_metric = landing_payload
        record_seasonal_card_event(
            user_pk=int(user_pk),
            event="seasonal_card_landed",
            route="web_package.page",
            season_focus=str(active_metric.get("season_focus") or ""),
            card_type=str(active_metric.get("card_type") or ""),
            cta_target=str(active_metric.get("cta_target") or ""),
            source_screen=str(active_metric.get("source_screen") or "unknown"),
            priority=int(active_metric.get("priority") or 0),
            completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
            month_key=str(active_metric.get("month_key") or month_key),
        )

    return render_template(
        "package/index.html",
        month_key=month_key,
        month_first=month_first,
        prev_month=prev_month,
        next_month=next_month,
        gross_income=int(gross_income),
        total_out=int(total_out),
        missing_total=int(missing_total),
        missing_required=int(missing_required),
        missing_maybe=int(missing_maybe),
        attached_total=int(attached_total),
        denom=int(denom),
        missing_rows=missing_rows[:80],  # 화면은 최대 80건만
        zip_preview=zip_preview,
        preflight=preflight,
        profile_summary=profile_summary,
        can_package_download=can_package_download_flag,
        plan_code=ent.plan_code,
        plan_label=plan_label_ko(ent.plan_code),
        plan_status=ent.plan_status,
        account_filter_value=account_filter_value,
        account_filter_name=account_filter_name,
        missing_account_map=missing_account_map,
        seasonal_context=seasonal_context,
        seasonal_experience=seasonal_experience,
    )


@web_package_bp.get("/package/download")
@login_required
def download():
    user_pk = int(session["user_id"])
    account_filter_value, _account_filter_name, account_filter_id = _normalize_package_account_filter(
        user_pk,
        request.args.get("account"),
    )

    ip = client_ip()
    limited, wait_sec = hit_limit(key=f"web:package:download:ip:{ip}", limit=20, window_seconds=60)
    if limited:
        flash(f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_package.page", account=(account_filter_value or None)))

    try:
        ensure_can_download_package(user_pk)
    except PlanPermissionError as e:
        flash(str(e), "error")
        audit_event("package_download_denied", user_pk=user_pk, outcome="denied", detail="plan_not_allowed", extra={"ip": ip})
        return redirect(url_for("web_main.pricing"))

    month_key = parse_date_ym(request.args.get("month")) or ""
    force = parse_bool_yn(request.args.get("force")) is True

    preview = build_tax_package_preview(
        user_pk=user_pk,
        month_key=month_key,
        account_filter=account_filter_value,
        account_id=account_filter_id,
    )
    preflight = (preview or {}).get("preflight") or {}
    if preflight.get("status") == "fail" and not force:
        flash("필수 점검 항목이 남아 있어요. 품질리포트의 FAIL 항목을 먼저 해결해 주세요.", "error")
        audit_event("package_download_denied", user_pk=user_pk, outcome="denied", detail="preflight_fail", extra={"ip": ip})
        return redirect(url_for("web_package.page", month=month_key, account=(account_filter_value or None)))

    zip_io, filename = build_tax_package_zip(
        user_pk=user_pk,
        month_key=month_key,
        account_filter=account_filter_value,
        account_id=account_filter_id,
    )
    try:
        zip_io.seek(0)
    except Exception:
        pass
    audit_event("package_download", user_pk=user_pk, outcome="ok", extra={"ip": ip, "month": month_key})
    active_metric = get_active_seasonal_card(session)
    if active_metric and str(active_metric.get("completion_action") or "") == "package_downloaded":
        refreshed_experience = build_seasonal_experience(
            user_pk=int(user_pk),
            month_key=month_key,
            urls={},
        )
        record_seasonal_card_event(
            user_pk=int(user_pk),
            event="seasonal_card_completed",
            route="web_package.download",
            season_focus=str(active_metric.get("season_focus") or ""),
            card_type=str(active_metric.get("card_type") or ""),
            cta_target=str(active_metric.get("cta_target") or ""),
            source_screen=str(active_metric.get("source_screen") or "unknown"),
            priority=int(active_metric.get("priority") or 0),
            completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
            completion_state_after=(
                seasonal_card_completion_state(refreshed_experience, str(active_metric.get("card_type") or ""))
                or str(active_metric.get("completion_state_before") or "todo")
            ),
            month_key=str(active_metric.get("month_key") or month_key),
        )
        clear_active_seasonal_card(session)

    return send_file(
        zip_io,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )

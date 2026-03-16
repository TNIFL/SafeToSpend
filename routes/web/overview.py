# routes/web/overview.py
from datetime import datetime, timedelta, date
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from core.auth import login_required
from core.extensions import db
from domain.models import (
    CounterpartyExpenseRule,
    CounterpartyRule,
    EvidenceItem,
    ExpenseLabel,
    IncomeLabel,
    RecurringCandidate,
    Settings,
    Transaction,
)
from services.input_sanitize import parse_date_ym, safe_str
from services.analytics_events import record_input_funnel_event, record_seasonal_card_event
from services.risk import compute_overview, normalize_counterparty_key
from services.seasonal_ux import (
    append_seasonal_landing_params,
    build_seasonal_experience,
    build_seasonal_tracking_query_params,
    decorate_seasonal_cards_for_tracking,
    normalize_seasonal_metric_payload,
    store_pending_seasonal_card,
)

web_overview_bp = Blueprint("web_overview", __name__)


def _safe_local_redirect(raw: str | None, *, fallback: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return str(fallback or "/overview")
    try:
        parsed = urlsplit(value)
    except Exception:
        return str(fallback or "/overview")
    if parsed.scheme or parsed.netloc:
        return str(fallback or "/overview")
    if not str(parsed.path or "").startswith("/"):
        return str(fallback or "/overview")
    return value


def _parse_month_key(value: str | None) -> date:
    raw = parse_date_ym(value)
    if not raw:
        today = datetime.now().date()
        return date(today.year, today.month, 1)
    try:
        y, m = raw.split("-", 1)
        yy = int(y)
        mm = int(m)
        if yy < 2000 or yy > 2100 or mm < 1 or mm > 12:
            raise ValueError
        return date(yy, mm, 1)
    except Exception:
        today = datetime.now().date()
        return date(today.year, today.month, 1)


def _add_months(base: date, delta: int) -> date:
    month_index = (base.year * 12 + (base.month - 1)) + int(delta)
    year = month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, 1)


@web_overview_bp.route("/overview", methods=["GET"])
@login_required
def overview():
    user_pk = session["user_id"]
    requested_month = _parse_month_key(request.args.get("month"))
    ctx = compute_overview(user_pk, month_key=requested_month.strftime("%Y-%m"))
    seasonal_urls = {
        "review": str(ctx.get("review_required_url") or ctx.get("review_classify_url") or ctx.get("next_action_url") or ""),
        "tax_buffer": str(ctx.get("tax_buffer_url") or ""),
        "package": str(ctx.get("package_url") or ""),
        "profile": str(((ctx.get("tax_recovery_cta") or {}).get("url")) or ctx.get("next_action_url") or ""),
    }
    ctx["seasonal_experience"] = build_seasonal_experience(
        user_pk=int(user_pk),
        month_key=str(ctx.get("month_key") or requested_month.strftime("%Y-%m")),
        urls=seasonal_urls,
    )
    current_month_key = str(ctx.get("month_key") or requested_month.strftime("%Y-%m"))
    fallback_overview_url = url_for("web_overview.overview", month=current_month_key)
    ctx["seasonal_experience"] = decorate_seasonal_cards_for_tracking(
        ctx.get("seasonal_experience"),
        source_screen="overview",
        month_key=current_month_key,
        click_url_builder=lambda metric_payload, target_url: url_for(
            "web_overview.seasonal_card_click",
            **build_seasonal_tracking_query_params(
                metric_payload,
                redirect_to=_safe_local_redirect(target_url, fallback=fallback_overview_url),
            ),
        ),
    )
    for card in (ctx.get("seasonal_experience") or {}).get("cards") or []:
        metric_payload = dict(card.get("metric_payload") or {})
        if not metric_payload:
            continue
        record_seasonal_card_event(
            user_pk=int(user_pk),
            event="seasonal_card_shown",
            route="web_overview.overview",
            season_focus=str(metric_payload.get("season_focus") or ""),
            card_type=str(metric_payload.get("card_type") or ""),
            cta_target=str(metric_payload.get("cta_target") or ""),
            source_screen="overview",
            priority=int(metric_payload.get("priority") or 0),
            completion_state_before=str(metric_payload.get("completion_state_before") or "todo"),
            month_key=str(metric_payload.get("month_key") or ""),
        )
    current_month = _parse_month_key(current_month_key)

    prev_month = _add_months(current_month, -1)
    next_month = _add_months(current_month, 1)
    prev_year = _add_months(current_month, -12)
    next_year = _add_months(current_month, 12)
    now = datetime.now().date()

    ctx["month_nav"] = {
        "current": current_month.strftime("%Y-%m"),
        "prev_month": prev_month.strftime("%Y-%m"),
        "next_month": next_month.strftime("%Y-%m"),
        "prev_year": prev_year.strftime("%Y-%m"),
        "next_year": next_year.strftime("%Y-%m"),
        "this_month": f"{now.year:04d}-{now.month:02d}",
    }

    if (request.args.get("month_end_reminder") or "").strip() == "hide":
        session["month_end_banner_hidden_month"] = str(ctx.get("month_key") or "")
        session.modified = True
        return redirect(url_for("web_overview.overview", month=ctx.get("month_key")))

    hidden_month = str(session.get("month_end_banner_hidden_month") or "")
    if hidden_month and hidden_month == str(ctx.get("month_key") or ""):
        ctx["show_month_end_banner"] = False

    st = Settings.query.filter_by(user_pk=user_pk).first()
    if st and (not bool(getattr(st, "month_end_reminder_enabled", True))):
        ctx["show_month_end_banner"] = False

    ctx["month_end_hide_url"] = url_for(
        "web_overview.overview",
        month=ctx.get("month_key"),
        month_end_reminder="hide",
    )
    tax_meta = dict(ctx.get("tax_result_meta") or {})
    nhis_meta = dict(ctx.get("nhis_result_meta") or {})
    tax_cta = dict(ctx.get("tax_recovery_cta") or {})
    nhis_cta = dict(ctx.get("nhis_recovery_cta") or {})
    current_tax_level = str((ctx.get("tax_display_policy") or {}).get("accuracy_level") or tax_meta.get("accuracy_level") or "limited")
    current_nhis_level = str((ctx.get("nhis_display_policy") or {}).get("accuracy_level") or nhis_meta.get("accuracy_level") or "limited")
    current_tax_reason = str(tax_meta.get("reason") or "")
    current_nhis_reason = str(nhis_meta.get("reason") or "")
    if tax_cta.get("show"):
        record_input_funnel_event(
            user_pk=int(user_pk),
            event="tax_recovery_cta_shown",
            route="web_overview.overview",
            screen="overview",
            accuracy_level_before=current_tax_level,
            accuracy_level_after=current_tax_level,
            reason_code_before=current_tax_reason,
            reason_code_after=current_tax_reason,
            extra={"month_key": str(ctx.get("month_key") or "")},
        )
    if current_tax_reason == "missing_income_classification":
        record_input_funnel_event(
            user_pk=int(user_pk),
            event="tax_inline_income_classification_shown",
            route="web_overview.overview",
            screen="overview",
            accuracy_level_before=current_tax_level,
            accuracy_level_after=current_tax_level,
            reason_code_before=current_tax_reason,
            reason_code_after=current_tax_reason,
            extra={"month_key": str(ctx.get("month_key") or "")},
        )
    if nhis_cta.get("show"):
        record_input_funnel_event(
            user_pk=int(user_pk),
            event="nhis_recovery_cta_shown",
            route="web_overview.overview",
            screen="overview",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"month_key": str(ctx.get("month_key") or "")},
        )
    if current_nhis_reason == "missing_membership_type":
        record_input_funnel_event(
            user_pk=int(user_pk),
            event="nhis_inline_membership_type_shown",
            route="web_overview.overview",
            screen="overview",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"month_key": str(ctx.get("month_key") or "")},
        )
    return render_template("overview.html", **ctx)


@web_overview_bp.get("/overview/seasonal-card-click")
@login_required
def seasonal_card_click():
    user_pk = int(session["user_id"])
    fallback_url = url_for("web_overview.overview")
    redirect_to = _safe_local_redirect(request.args.get("redirect_to"), fallback=fallback_url)
    metric_payload = normalize_seasonal_metric_payload(
        {
            "season_focus": request.args.get("season_focus"),
            "card_type": request.args.get("seasonal_card_type"),
            "cta_target": request.args.get("seasonal_cta_target"),
            "source_screen": request.args.get("seasonal_source_screen"),
            "priority": request.args.get("seasonal_priority"),
            "completion_state_before": request.args.get("seasonal_completion_state_before"),
            "month_key": request.args.get("seasonal_month_key"),
            "completion_action": request.args.get("seasonal_completion_action"),
        }
    )
    record_seasonal_card_event(
        user_pk=int(user_pk),
        event="seasonal_card_clicked",
        route="web_overview.seasonal_card_click",
        season_focus=str(metric_payload.get("season_focus") or ""),
        card_type=str(metric_payload.get("card_type") or ""),
        cta_target=str(metric_payload.get("cta_target") or ""),
        source_screen=str(metric_payload.get("source_screen") or "unknown"),
        priority=int(metric_payload.get("priority") or 0),
        completion_state_before=str(metric_payload.get("completion_state_before") or "todo"),
        month_key=str(metric_payload.get("month_key") or ""),
    )
    store_pending_seasonal_card(session, metric_payload)
    return redirect(append_seasonal_landing_params(redirect_to, metric_payload))


@web_overview_bp.post("/overview/recurring/<int:candidate_id>/apply")
@login_required
def apply_recurring_candidate(candidate_id: int):
    user_pk = session["user_id"]
    action = safe_str(request.form.get("action"), max_len=20)
    month = parse_date_ym(request.form.get("month")) or ""

    cand = (
        RecurringCandidate.query
        .filter_by(id=int(candidate_id), user_pk=user_pk)
        .first()
    )
    if not cand:
        flash("정기 거래 후보를 찾지 못했어요.", "error")
        return redirect(url_for("web_overview.overview"))

    cp_key = normalize_counterparty_key(cand.counterparty)
    if not cp_key:
        flash("거래처 정보가 비어 있어 자동 분류를 적용할 수 없어요.", "error")
        return redirect(url_for("web_overview.overview"))

    try:
        now = datetime.now()
        changed = 0
        cand_direction = str(cand.direction or "").strip().lower()
        if cand_direction == "out":
            if action not in ("business", "personal"):
                flash("적용할 분류를 선택해 주세요.", "error")
                return redirect(url_for("web_overview.overview"))

            rule = CounterpartyExpenseRule.query.filter_by(user_pk=user_pk, counterparty_key=cp_key).first()
            if not rule:
                rule = CounterpartyExpenseRule(user_pk=user_pk, counterparty_key=cp_key, rule=action, active=True)
            else:
                rule.rule = action
                rule.active = True
            db.session.add(rule)

            cutoff = now - timedelta(days=120)
            out_rows = (
                db.session.query(Transaction.id, Transaction.counterparty)
                .filter(Transaction.user_pk == user_pk, Transaction.direction == "out")
                .filter(Transaction.occurred_at >= cutoff)
                .filter(Transaction.counterparty.isnot(None))
                .all()
            )
            target_tx_ids = [
                int(tx_id)
                for tx_id, counterparty in out_rows
                if normalize_counterparty_key(counterparty) == cp_key
            ]
            if target_tx_ids:
                labels = (
                    ExpenseLabel.query
                    .filter(ExpenseLabel.user_pk == user_pk, ExpenseLabel.transaction_id.in_(target_tx_ids))
                    .all()
                )
                label_map = {int(l.transaction_id): l for l in labels}
                ev_rows = (
                    EvidenceItem.query
                    .filter(EvidenceItem.user_pk == user_pk, EvidenceItem.transaction_id.in_(target_tx_ids))
                    .all()
                )
                ev_map = {int(e.transaction_id): e for e in ev_rows}

                for tx_id in target_tx_ids:
                    lbl = label_map.get(tx_id)
                    if not lbl:
                        lbl = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)
                    if lbl.status in ("unknown", "mixed") or not lbl.status:
                        lbl.status = action
                        lbl.confidence = max(int(lbl.confidence or 0), 90)
                        lbl.labeled_by = "auto"
                        lbl.decided_at = now
                        db.session.add(lbl)
                        changed += 1

                    ev = ev_map.get(tx_id)
                    if not ev:
                        ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id)
                    if action == "business":
                        ev.requirement = "required"
                        if ev.status in (None, "", "missing", "maybe"):
                            ev.status = "missing"
                    else:
                        ev.requirement = "not_needed"
                        ev.status = "not_needed"
                    db.session.add(ev)
        elif cand_direction == "in":
            if action not in ("income", "non_income"):
                flash("적용할 분류를 선택해 주세요.", "error")
                return redirect(url_for("web_overview.overview"))

            rule = CounterpartyRule.query.filter_by(user_pk=user_pk, counterparty_key=cp_key).first()
            if not rule:
                rule = CounterpartyRule(user_pk=user_pk, counterparty_key=cp_key, rule=action, active=True)
            else:
                rule.rule = action
                rule.active = True
            db.session.add(rule)

            cutoff = now - timedelta(days=120)
            in_rows = (
                db.session.query(Transaction.id, Transaction.counterparty)
                .filter(Transaction.user_pk == user_pk, Transaction.direction == "in")
                .filter(Transaction.occurred_at >= cutoff)
                .filter(Transaction.counterparty.isnot(None))
                .all()
            )
            target_tx_ids = [
                int(tx_id)
                for tx_id, counterparty in in_rows
                if normalize_counterparty_key(counterparty) == cp_key
            ]
            if target_tx_ids:
                labels = (
                    IncomeLabel.query
                    .filter(IncomeLabel.user_pk == user_pk, IncomeLabel.transaction_id.in_(target_tx_ids))
                    .all()
                )
                label_map = {int(l.transaction_id): l for l in labels}
                for tx_id in target_tx_ids:
                    lbl = label_map.get(tx_id)
                    if not lbl:
                        lbl = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)
                    if lbl.status in ("unknown", None, ""):
                        lbl.status = action
                        lbl.confidence = max(int(lbl.confidence or 0), 90)
                        lbl.labeled_by = "auto"
                        lbl.decided_at = now
                        db.session.add(lbl)
                        changed += 1
        else:
            flash("정기 거래 후보 방향을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
            return redirect(url_for("web_overview.overview"))

        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("자동 분류 적용 중 오류가 발생했어요.", "error")
        return redirect(url_for("web_overview.overview"))

    if changed > 0:
        flash(f"자동 분류 규칙을 저장하고 {changed}건에 바로 적용했어요.", "success")
    else:
        flash("자동 분류 규칙을 저장했어요. 다음 가져오기부터 자동 적용돼요.", "success")

    if month:
        return redirect(url_for("web_overview.overview", month=month))
    return redirect(url_for("web_overview.overview"))

from __future__ import annotations

from datetime import timedelta

from flask import flash, redirect, render_template, request, session, url_for
from sqlalchemy import func
from services.bank_accounts import get_linked_account_balances, list_accounts_for_ui
from services.analytics_events import record_input_funnel_event, record_seasonal_card_event
from services.input_sanitize import parse_date_ym, parse_int_krw, safe_str
from services.nhis_runtime import build_nhis_recovery_cta, compute_nhis_monthly_buffer
from services.official_data_effects import summarize_official_data_effects
from services.risk import build_tax_recovery_cta, build_tax_result_meta
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
from services.tax_input_draft import build_tax_input_draft


RECEIPT_EFFECT_QUERY_KEYS = (
    "receipt_effect_event",
    "receipt_effect_level",
    "current_tax_due_est_krw",
    "current_buffer_target_krw",
    "tax_delta_from_receipts_krw",
    "buffer_delta_from_receipts_krw",
    "receipt_reflected_expense_krw",
    "receipt_pending_expense_krw",
    "tax_before",
    "tax_after",
    "buffer_before",
    "buffer_after",
    "expense_before",
    "expense_after",
    "profit_before",
    "profit_after",
)


def legacy_tax_package_redirect(month_key: str):
    """Legacy URL adapter: /dashboard/tax-package -> /dashboard/package/download."""
    return redirect(url_for("web_package.download", month=month_key))


def register_tax_routes(*, bp, uid_getter, parse_month, compute_tax_estimate, db, TaxBufferLedger):
    def _receipt_effect_nav_params() -> dict[str, str]:
        params: dict[str, str] = {}
        for key in RECEIPT_EFFECT_QUERY_KEYS:
            raw = request.args.get(key)
            if raw is not None and str(raw).strip() != "":
                params[key] = str(raw)
        return params

    def _int_arg(name: str) -> int | None:
        try:
            raw = request.args.get(name)
            return int(raw) if raw is not None and raw != "" else None
        except Exception:
            return None

    def _compute_monthly_tax_estimate(user_pk: int, *, month_key: str):
        try:
            return compute_tax_estimate(
                user_pk,
                month_key=month_key,
                prefer_monthly_signal=True,
            )
        except TypeError:
            return compute_tax_estimate(user_pk, month_key=month_key)

    def _seasonal_click_url(metric_payload: dict[str, object], target_url: str) -> str:
        return url_for(
            "web_overview.seasonal_card_click",
            **build_seasonal_tracking_query_params(metric_payload, redirect_to=str(target_url or "")),
        )

    @bp.get("/tax-package")
    def tax_package():
        """레거시 URL 호환: 실제 구현은 web_package.download로 위임."""
        month_key = parse_date_ym(request.args.get("month")) or ""
        return legacy_tax_package_redirect(month_key)

    @bp.get("/tax-buffer")
    def tax_buffer():
        user_pk = uid_getter()
        linked_accounts, linked_accounts_has_unavailable = get_linked_account_balances(user_pk, limit=6)
        account_options = list_accounts_for_ui(user_pk)
        account_filter_raw = safe_str(request.args.get("account"), max_len=32).lower()
        account_filter_name = "전체 계좌"
        if account_filter_raw == "unassigned":
            account_filter_name = "미지정"
        else:
            try:
                account_filter_id = int(account_filter_raw)
            except Exception:
                account_filter_id = 0
            if account_filter_id > 0:
                account_options = list_accounts_for_ui(user_pk, keep_ids=[account_filter_id])
                selected_account = next((x for x in account_options if int(x.get("id") or 0) == account_filter_id), None)
                if selected_account:
                    account_filter_name = str(selected_account.get("display_name") or "선택 계좌")
                else:
                    account_filter_raw = "all"
            else:
                account_filter_raw = "all"
        show_account_filter_badge = account_filter_raw not in {"", "all"}
        account_filter_value = account_filter_raw if account_filter_raw else "all"

        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")

        est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
        tax_calc_meta = build_tax_result_meta(est)
        tax_calc_meta["mode"] = str(getattr(est, "tax_calculation_mode", "unknown") or "unknown")
        tax_calc_meta["official_calculable"] = bool(getattr(est, "official_calculable", False))
        tax_calc_meta["is_limited_estimate"] = bool(getattr(est, "is_limited_estimate", False))
        tax_calc_meta["official_block_reason"] = str(getattr(est, "official_block_reason", "") or "")
        tax_calc_meta["taxable_income_input_source"] = str(
            getattr(est, "taxable_income_input_source", "missing") or "missing"
        )
        tax_calc_meta["annual_tax_credit_input_krw"] = int(getattr(est, "annual_tax_credit_input_krw", 0) or 0)
        tax_calc_meta["withheld_tax_input_annual_krw"] = int(getattr(est, "withheld_tax_input_annual_krw", 0) or 0)
        tax_calc_meta["prepaid_tax_input_annual_krw"] = int(getattr(est, "prepaid_tax_input_annual_krw", 0) or 0)
        health_insurance_buffer, health_insurance_note, nhis_payload = compute_nhis_monthly_buffer(
            user_pk=user_pk,
            month_key=month_key,
        )
        nhis_calc_meta = dict((nhis_payload or {}).get("result_meta") or {})
        tax_accuracy_level = str(tax_calc_meta.get("accuracy_level") or "limited").strip().lower()
        nhis_accuracy_level = str(nhis_calc_meta.get("accuracy_level") or "limited").strip().lower()
        tax_display_policy = {
            "accuracy_level": tax_accuracy_level,
            "blocked": tax_accuracy_level == "blocked",
            "limited": tax_accuracy_level == "limited",
            "strong": tax_accuracy_level in {"exact_ready", "high_confidence"},
        }
        nhis_display_policy = {
            "accuracy_level": nhis_accuracy_level,
            "blocked": nhis_accuracy_level == "blocked",
            "limited": nhis_accuracy_level == "limited",
            "strong": nhis_accuracy_level in {"exact_ready", "high_confidence"},
        }
        tax_required_inputs = dict(tax_calc_meta.get("required_inputs") or {})
        tax_missing_high_fields = [str(v) for v in (tax_required_inputs.get("high_confidence_missing_fields") or []) if str(v)]
        tax_missing_exact_fields = [str(v) for v in (tax_required_inputs.get("exact_ready_missing_fields") or []) if str(v)]
        tax_needs_user_input_fields = [str(v) for v in (tax_calc_meta.get("needs_user_input_fields") or []) if str(v)]
        tax_missing_priority_fields = list(
            dict.fromkeys([*tax_needs_user_input_fields, *tax_missing_exact_fields, *tax_missing_high_fields])
        )
        tax_required_input_labels = {
            "official_taxable_income_annual_krw": "연 과세표준(고급 입력)",
            "income_classification": "소득 유형",
            "annual_gross_income_krw": "총수입",
            "annual_deductible_expense_krw": "업무 관련 지출",
            "withheld_tax_annual_krw": "이미 떼인 세금(원천징수)",
            "prepaid_tax_annual_krw": "이미 낸 세금(기납부)",
            "tax_basic_inputs_confirmed": "기본 입력 저장",
            "tax_advanced_input_confirmed": "고급 입력 저장",
        }
        tax_rate = float(est.tax_rate)
        income_total = int(est.income_included_krw)
        recommended = int(est.buffer_target_krw)
        total_setaside_recommended = int(recommended) + int(health_insurance_buffer)
        balance = int(est.buffer_total_krw)

        est_profit = int(est.estimated_profit_krw or 0)
        after_tax_profit = est_profit - recommended

        ledger = (
            db.session.query(TaxBufferLedger)
            .filter(TaxBufferLedger.user_pk == user_pk)
            .order_by(TaxBufferLedger.created_at.desc(), TaxBufferLedger.id.desc())
            .limit(40)
            .all()
        )

        running = balance
        ledger_rows = []
        for row in ledger:
            ledger_rows.append(
                {
                    "created_at": row.created_at,
                    "delta": int(row.delta_amount_krw or 0),
                    "running": int(running),
                    "note": row.note or "",
                }
            )
            running = running - int(row.delta_amount_krw or 0)

        progress_pct = 0
        if recommended > 0:
            progress_pct = int(min(100, max(0, (balance / recommended) * 100)))

        shortage = max(0, recommended - balance)
        overage = max(0, balance - recommended)

        toast = safe_str(request.args.get("toast"), max_len=20)
        toast_amount = None
        try:
            toast_amount = parse_int_krw(request.args.get("amount"))
        except Exception:
            toast_amount = None
        toast_note = safe_str(request.args.get("note"), max_len=120)
        profile_kwargs = {
            "step": 2,
            "next": url_for("web_calendar.tax_buffer", month=month_key, account=(account_filter_value or None)),
            "return_to_next": 1,
            "recovery_source": "tax_tax_buffer_cta",
        }
        if str(tax_calc_meta.get("reason") or "") == "missing_income_classification":
            profile_kwargs["focus"] = "income_classification"
        profile_edit_url = url_for("web_profile.tax_profile", **profile_kwargs)
        nhis_edit_url = url_for(
            "web_profile.nhis_page",
            month=month_key,
            account=(account_filter_value or None),
            recovery_source="nhis_tax_buffer_cta",
        )
        tax_recovery_cta = build_tax_recovery_cta(
            tax_calc_meta,
            recovery_url=profile_edit_url,
        )
        tax_input_draft = build_tax_input_draft(user_pk=user_pk)
        nhis_recovery_cta = build_nhis_recovery_cta(
            nhis_calc_meta,
            recovery_url=nhis_edit_url,
        )
        tax_reason = str(tax_calc_meta.get("reason") or "")
        nhis_reason = str(nhis_calc_meta.get("reason") or "")
        if tax_recovery_cta.get("show"):
            record_input_funnel_event(
                user_pk=user_pk,
                event="tax_recovery_cta_shown",
                route="web_calendar.tax_buffer",
                screen="tax_buffer",
                accuracy_level_before=tax_accuracy_level,
                accuracy_level_after=tax_accuracy_level,
                reason_code_before=tax_reason,
                reason_code_after=tax_reason,
                extra={"month_key": month_key},
            )
        if tax_reason == "missing_income_classification":
            record_input_funnel_event(
                user_pk=user_pk,
                event="tax_inline_income_classification_shown",
                route="web_calendar.tax_buffer",
                screen="tax_buffer",
                accuracy_level_before=tax_accuracy_level,
                accuracy_level_after=tax_accuracy_level,
                reason_code_before=tax_reason,
                reason_code_after=tax_reason,
                extra={"month_key": month_key},
            )
        if nhis_recovery_cta.get("show"):
            record_input_funnel_event(
                user_pk=user_pk,
                event="nhis_recovery_cta_shown",
                route="web_calendar.tax_buffer",
                screen="tax_buffer",
                accuracy_level_before=nhis_accuracy_level,
                accuracy_level_after=nhis_accuracy_level,
                reason_code_before=nhis_reason,
                reason_code_after=nhis_reason,
                extra={"month_key": month_key},
            )
        if nhis_reason == "missing_membership_type":
            record_input_funnel_event(
                user_pk=user_pk,
                event="nhis_inline_membership_type_shown",
                route="web_calendar.tax_buffer",
                screen="tax_buffer",
                accuracy_level_before=nhis_accuracy_level,
                accuracy_level_after=nhis_accuracy_level,
                reason_code_before=nhis_reason,
                reason_code_after=nhis_reason,
                extra={"month_key": month_key},
            )
        review_income_url = url_for(
            "web_calendar.review",
            month=month_key,
            focus="income_confirm",
            account=(account_filter_value or None),
        )
        prev_month = (month_first.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m")
        next_month = (month_first.replace(day=28) + timedelta(days=10)).replace(day=1).strftime("%Y-%m")
        receipt_effect_nav_params = _receipt_effect_nav_params()
        calendar_url = url_for(
            "web_calendar.month_calendar",
            month=month_key,
            account=(account_filter_value or None),
            **receipt_effect_nav_params,
        )
        seasonal_experience = build_seasonal_experience(
            user_pk=int(user_pk),
            month_key=month_key,
            urls={
                "review": url_for(
                    "web_calendar.review",
                    month=month_key,
                    lane="required",
                    focus="receipt_required",
                    account=(account_filter_value or None),
                ),
                "tax_buffer": url_for(
                    "web_calendar.tax_buffer",
                    month=month_key,
                    account=(account_filter_value or None),
                ),
                "package": url_for("web_package.page", month=month_key, account=(account_filter_value or None)),
                "profile": profile_edit_url,
            },
        )
        seasonal_experience = decorate_seasonal_cards_for_tracking(
            seasonal_experience,
            source_screen="tax_buffer",
            month_key=month_key,
            click_url_builder=_seasonal_click_url,
        )
        seasonal_context = build_seasonal_screen_context(seasonal_experience, "tax_buffer")
        seasonal_context = decorate_seasonal_context_for_tracking(
            seasonal_context,
            month_key=month_key,
            click_url_builder=_seasonal_click_url,
        )
        official_data_effect_notice = summarize_official_data_effects(
            tax_estimate=est,
            nhis_result_meta=nhis_calc_meta,
        )
        if seasonal_context and isinstance(seasonal_context.get("metric_payload"), dict):
            metric_payload = dict(seasonal_context.get("metric_payload") or {})
            record_seasonal_card_event(
                user_pk=int(user_pk),
                event="seasonal_card_shown",
                route="web_calendar.tax_buffer",
                season_focus=str(metric_payload.get("season_focus") or ""),
                card_type=str(metric_payload.get("card_type") or ""),
                cta_target=str(metric_payload.get("cta_target") or ""),
                source_screen="tax_buffer",
                priority=int(metric_payload.get("priority") or 0),
                completion_state_before=str(metric_payload.get("completion_state_before") or "todo"),
                month_key=str(metric_payload.get("month_key") or month_key),
            )
        landing_payload = seasonal_metric_payload_from_landing_args(request.args)
        if landing_payload and str(landing_payload.get("cta_target") or "") == "tax_buffer":
            if str(landing_payload.get("completion_action") or ""):
                active_metric = activate_pending_seasonal_card(session) or set_active_seasonal_card(session, landing_payload)
            else:
                clear_pending_seasonal_card(session)
                active_metric = landing_payload
            record_seasonal_card_event(
                user_pk=int(user_pk),
                event="seasonal_card_landed",
                route="web_calendar.tax_buffer",
                season_focus=str(active_metric.get("season_focus") or ""),
                card_type=str(active_metric.get("card_type") or ""),
                cta_target=str(active_metric.get("cta_target") or ""),
                source_screen=str(active_metric.get("source_screen") or "unknown"),
                priority=int(active_metric.get("priority") or 0),
                completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
                month_key=str(active_metric.get("month_key") or month_key),
            )

        return render_template(
            "calendar/tax_buffer.html",
            month_key=month_key,
            month_first=month_first,
            prev_month=prev_month,
            next_month=next_month,
            tax_rate=tax_rate,
            income_total=income_total,
            recommended=recommended,
            balance=balance,
            gap=shortage,
            ledger_rows=ledger_rows,
            progress_pct=progress_pct,
            shortage=shortage,
            overage=overage,
            est=est,
            after_tax_profit=after_tax_profit,
            toast=toast,
            toast_amount=toast_amount,
            toast_note=toast_note,
            receipt_effect_event=(request.args.get("receipt_effect_event") == "1"),
            receipt_effect_level=str(request.args.get("receipt_effect_level") or ""),
            current_tax_due_est_krw=_int_arg("current_tax_due_est_krw"),
            current_buffer_target_krw=_int_arg("current_buffer_target_krw"),
            tax_delta_from_receipts_krw=_int_arg("tax_delta_from_receipts_krw"),
            buffer_delta_from_receipts_krw=_int_arg("buffer_delta_from_receipts_krw"),
            receipt_reflected_expense_krw=_int_arg("receipt_reflected_expense_krw"),
            receipt_pending_expense_krw=_int_arg("receipt_pending_expense_krw"),
            tax_before=_int_arg("tax_before"),
            tax_after=_int_arg("tax_after"),
            buffer_before=_int_arg("buffer_before"),
            buffer_after=_int_arg("buffer_after"),
            expense_before=_int_arg("expense_before"),
            expense_after=_int_arg("expense_after"),
            profit_before=_int_arg("profit_before"),
            profit_after=_int_arg("profit_after"),
            profile_edit_url=profile_edit_url,
            nhis_edit_url=nhis_edit_url,
            review_income_url=review_income_url,
            calendar_url=calendar_url,
            health_insurance_buffer=int(health_insurance_buffer),
            health_insurance_note=(health_insurance_note or ""),
            total_setaside_recommended=int(total_setaside_recommended),
            nhis_payload=nhis_payload,
            nhis_calc_meta=nhis_calc_meta,
            tax_calc_meta=tax_calc_meta,
            tax_required_inputs=tax_required_inputs,
            tax_missing_priority_fields=tax_missing_priority_fields,
            tax_required_input_labels=tax_required_input_labels,
            tax_display_policy=tax_display_policy,
            nhis_display_policy=nhis_display_policy,
            tax_recovery_cta=tax_recovery_cta,
            tax_input_draft=tax_input_draft,
            nhis_recovery_cta=nhis_recovery_cta,
            linked_accounts=linked_accounts,
            linked_accounts_has_unavailable=bool(linked_accounts_has_unavailable),
            show_account_filter_badge=show_account_filter_badge,
            account_filter_name=account_filter_name,
            account_filter_value=account_filter_value,
            official_data_effect_notice=official_data_effect_notice,
            seasonal_context=seasonal_context,
            seasonal_experience=seasonal_experience,
        )

    @bp.post("/tax-buffer/adjust")
    def tax_buffer_adjust():
        user_pk = uid_getter()

        month_key = parse_date_ym(request.form.get("month")) or ""
        if not month_key:
            month_key = parse_date_ym(request.args.get("month")) or ""
        account_filter_value = safe_str(
            request.form.get("account") or request.args.get("account"),
            max_len=32,
        ).lower()
        if account_filter_value not in {"all", "unassigned"}:
            try:
                account_id = int(account_filter_value)
            except Exception:
                account_id = 0
            if account_id <= 0:
                account_filter_value = "all"
        elif not account_filter_value:
            account_filter_value = "all"

        delta = parse_int_krw(request.form.get("delta"), allow_negative=True) or 0

        note = safe_str(request.form.get("note"), max_len=120)

        if delta == 0:
            return redirect(
                url_for(
                    "web_calendar.tax_buffer",
                    month=month_key,
                    account=(account_filter_value or None),
                )
                if month_key
                else url_for("web_calendar.tax_buffer", account=(account_filter_value or None))
            )

        cur = (
            db.session.query(func.coalesce(func.sum(TaxBufferLedger.delta_amount_krw), 0))
            .filter(TaxBufferLedger.user_pk == user_pk)
            .scalar()
            or 0
        )
        cur = int(cur)

        if delta < 0 and (cur + delta) < 0:
            flash("세금 금고 잔액보다 더 많이 ‘납부/사용’할 수 없어요.", "error")
            return redirect(
                url_for(
                    "web_calendar.tax_buffer",
                    month=month_key,
                    account=(account_filter_value or None),
                )
                if month_key
                else url_for("web_calendar.tax_buffer", account=(account_filter_value or None))
            )

        if not note:
            note = "세금 보관" if delta > 0 else "세금 납부"

        db.session.add(TaxBufferLedger(user_pk=user_pk, delta_amount_krw=delta, note=note))
        db.session.commit()

        active_metric = get_active_seasonal_card(session)
        if active_metric and str(active_metric.get("completion_action") or "") == "tax_buffer_adjusted":
            refreshed_experience = build_seasonal_experience(
                user_pk=int(user_pk),
                month_key=month_key,
                urls={},
            )
            record_seasonal_card_event(
                user_pk=int(user_pk),
                event="seasonal_card_completed",
                route="web_calendar.tax_buffer_adjust",
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
                extra={"delta_amount_krw": int(delta or 0)},
            )
            clear_active_seasonal_card(session)

        kind = "deposit" if delta > 0 else "withdraw"
        amount = abs(int(delta))

        return redirect(
            url_for(
                "web_calendar.tax_buffer",
                month=month_key,
                toast=kind,
                amount=amount,
                note=note,
                account=(account_filter_value or None),
            )
            if month_key
            else url_for(
                "web_calendar.tax_buffer",
                toast=kind,
                amount=amount,
                note=note,
                account=(account_filter_value or None),
            )
        )

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeSerializer

from core.auth import login_required
from core.runtime_secret_guard import validate_runtime_secret_key
from core.security import sanitize_next_url
from domain.models import PaymentAttempt
from services.billing.service import (
    BillingCheckoutValidationError,
    BillingRegistrationError,
    complete_registration_success_by_order,
    confirm_checkout_intent_charge,
    get_checkout_intent,
    get_checkout_intent_by_resume_token,
    mark_registration_failed_by_order,
    resolve_checkout_billing_method,
    resume_checkout_intent_after_registration,
    start_checkout_intent,
    start_registration_attempt,
)
from services.billing.toss_client import (
    TossBillingApiError,
    TossBillingConfigError,
    build_billing_key_cipher_for_version,
    get_active_billing_key_version,
    build_registration_payload,
    issue_billing_key,
)
from services.input_sanitize import safe_str
from services.plan import (
    get_user_entitlements,
    plan_label_ko,
)
from services.sensitive_mask import mask_sensitive_numbers


web_billing_bp = Blueprint("web_billing", __name__, url_prefix="/dashboard/billing")


def _callback_signer() -> URLSafeSerializer:
    secret = str(current_app.config.get("SECRET_KEY") or "").strip()
    if not secret:
        raise BillingRegistrationError("등록 토큰 보안 설정을 확인해 주세요.")
    validate_runtime_secret_key(
        secret=secret,
        app_env=str(current_app.config.get("APP_ENV") or ""),
        bind_host=str(current_app.config.get("RUNTIME_BIND_HOST") or ""),
    )
    return URLSafeSerializer(secret_key=secret, salt="billing-register-callback-v1")


def _build_callback_state(order_id: str, customer_key: str, *, resume_token: str | None = None) -> str:
    signer = _callback_signer()
    payload = {"order_id": str(order_id or ""), "customer_key": str(customer_key or "")}
    token = str(resume_token or "").strip()
    if token:
        payload["resume"] = token
    return signer.dumps(payload)


def _load_callback_state(state: str) -> dict[str, str] | None:
    token = str(state or "").strip()
    if not token:
        return None
    try:
        payload = _callback_signer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "order_id": str((payload or {}).get("order_id") or "").strip(),
        "customer_key": str((payload or {}).get("customer_key") or "").strip(),
        "resume": str((payload or {}).get("resume") or "").strip(),
    }


def _verify_callback_state(payload: dict[str, str] | None, *, order_id: str, customer_key: str | None) -> bool:
    if not payload:
        return False
    payload_order = str((payload or {}).get("order_id") or "").strip()
    payload_customer = str((payload or {}).get("customer_key") or "").strip()
    if payload_order != str(order_id or "").strip():
        return False
    passed_customer = str(customer_key or "").strip()
    if passed_customer and payload_customer and passed_customer != payload_customer:
        return False
    return True


def _friendly_registration_error(exc: Exception) -> str:
    if isinstance(exc, TossBillingConfigError):
        return "지금은 결제수단 등록 준비가 되지 않았어요. 잠시 후 다시 시도해 주세요."
    if isinstance(exc, TossBillingApiError):
        return "결제수단 등록을 완료하지 못했어요. 잠시 후 다시 시도해 주세요."
    if isinstance(exc, BillingRegistrationError):
        return str(exc)
    return "결제수단 등록 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."


def _friendly_registration_fail_notice(*, fail_code: str | None, fail_message: str | None) -> str:
    code = str(fail_code or "").strip().lower()
    message = str(fail_message or "").strip()
    if code in {"user_cancel", "rejected_by_user", "canceled"}:
        return "결제수단 등록을 취소했어요. 필요하면 다시 시도해 주세요."
    if ("already" in code) or ("duplicate" in code) or ("이미" in message):
        return "이미 등록된 결제수단이거나 재등록이 제한된 카드예요. 기존 결제수단으로 계속 이용할 수 있어요."
    if message:
        return f"결제수단 등록을 완료하지 못했어요. {message}"
    return "이 결제수단은 아직 등록되지 않았어요. 다시 시도해 주세요."


def _attempt_operation_label(attempt_type: str | None) -> str:
    key = str(attempt_type or "").strip().lower()
    return {
        "initial": "최초 구독 결제",
        "upgrade_full_charge": "프로 업그레이드 결제",
        "addon_proration": "추가 계좌 결제",
        "recurring": "정기 결제",
        "retry": "재시도 결제",
    }.get(key, "결제")


def _load_payment_attempt_for_result(*, order_id: str | None, payment_key: str | None) -> PaymentAttempt | None:
    oid = safe_str(order_id, max_len=80)
    pkey = safe_str(payment_key, max_len=160)
    attempt = None
    if oid:
        attempt = PaymentAttempt.query.filter_by(order_id=oid).first()
    if (not attempt) and pkey:
        attempt = PaymentAttempt.query.filter_by(payment_key=pkey).first()
    return attempt


def _resolve_return_to_for_intent(intent) -> str:
    fallback = url_for("web_main.pricing")
    if not intent:
        return fallback
    snapshot = dict(getattr(intent, "pricing_snapshot_json", {}) or {})
    raw = str(snapshot.get("return_to") or "").strip()
    safe = sanitize_next_url(raw, fallback=fallback)
    if safe.startswith("/dashboard/billing/"):
        return fallback
    return safe


def _flash_billing_result_once(*, order_id: str, result_key: str, message: str, category: str) -> None:
    oid = str(order_id or "").strip()
    if not oid:
        flash(message, category)
        return
    storage_key = "_billing_result_notice_once"
    seen = session.get(storage_key)
    if not isinstance(seen, dict):
        seen = {}
    dedupe_key = f"{oid}:{result_key}"
    if dedupe_key in seen:
        return
    seen[dedupe_key] = datetime.now(timezone.utc).isoformat()
    if len(seen) > 80:
        for old_key in list(seen.keys())[:20]:
            seen.pop(old_key, None)
    session[storage_key] = seen
    session.modified = True
    flash(message, category)


def _render_registration_launch(*, user_id: int, resume_token: str = ""):
    attempt = start_registration_attempt(user_pk=user_id)
    callback_state = _build_callback_state(
        str(attempt.order_id or ""),
        str(attempt.customer_key or ""),
        resume_token=resume_token or None,
    )
    success_url = url_for(
        "web_billing.register_success",
        attempt=attempt.order_id,
        state=callback_state,
        _external=True,
    )
    fail_url = url_for(
        "web_billing.register_fail",
        attempt=attempt.order_id,
        state=callback_state,
        _external=True,
    )
    raw_payload = build_registration_payload(
        customer_key=str(attempt.customer_key or ""),
        success_url=success_url,
        fail_url=fail_url,
    )
    toss_payload = {
        "provider": str(raw_payload.provider),
        "client_key": str(raw_payload.client_key),
        "customer_key": str(raw_payload.customer_key),
        "success_url": str(raw_payload.success_url),
        "fail_url": str(raw_payload.fail_url),
    }
    return render_template(
        "billing/register_start.html",
        attempt_id=int(attempt.id),
        order_id=str(attempt.order_id or ""),
        customer_key=str(attempt.customer_key or ""),
        toss_payload=toss_payload,
        auto_launch=True,
    )


@web_billing_bp.get("/register")
@login_required
def register_page():
    user_id = int(session.get("user_id"))
    ent = get_user_entitlements(user_id)
    resume_token = safe_str(request.args.get("resume"), max_len=180)
    pending_intent = None
    if resume_token:
        pending_intent = get_checkout_intent_by_resume_token(resume_token, user_pk=user_id)
        if not pending_intent:
            resume_token = ""
    return render_template(
        "billing/register.html",
        plan_label=plan_label_ko(ent.plan_code),
        plan_code=ent.plan_code,
        sync_interval_minutes=ent.sync_interval_minutes,
        resume_token=resume_token,
        pending_intent=pending_intent,
    )


@web_billing_bp.post("/register/start")
@login_required
def register_start():
    user_id = int(session.get("user_id"))
    resume_token = safe_str(request.form.get("resume"), max_len=180)
    if resume_token and not get_checkout_intent_by_resume_token(resume_token, user_pk=user_id):
        resume_token = ""

    try:
        return _render_registration_launch(user_id=user_id, resume_token=resume_token)
    except Exception as e:
        current_app.logger.warning(
            "[WARN][billing] registration_start_failed user=%s err=%s",
            user_id,
            type(e).__name__,
        )
        flash(_friendly_registration_error(e), "error")
        return redirect(url_for("web_billing.register_page"))


@web_billing_bp.post("/checkout/start")
@login_required
def checkout_start():
    user_id = int(session.get("user_id"))
    operation_type = safe_str(request.form.get("operation_type"), max_len=40).lower()
    target_plan_code = safe_str(request.form.get("target_plan"), max_len=16).lower()
    if not target_plan_code:
        target_plan_code = None
    addon_quantity = safe_str(request.form.get("addon_quantity"), max_len=12)
    qty_value = None
    if addon_quantity:
        try:
            qty_value = int(addon_quantity)
        except Exception:
            qty_value = None
    client_request_key = safe_str(request.form.get("client_request_key"), max_len=64)
    next_url = sanitize_next_url(request.form.get("next"), fallback=url_for("web_main.pricing"))

    try:
        started = start_checkout_intent(
            user_pk=user_id,
            operation_type=operation_type,
            target_plan_code=target_plan_code,
            addon_quantity=qty_value,
            idempotency_key=(client_request_key or None),
            return_to=next_url,
            commit=True,
        )
        intent = started.get("intent")
        resume_token = safe_str(getattr(intent, "resume_token", ""), max_len=180)
        if not resume_token:
            flash("결제 시작 정보를 만들지 못했어요. 다시 시도해 주세요.", "error")
            return redirect(next_url)
        if bool(started.get("requires_registration")):
            return _render_registration_launch(user_id=user_id, resume_token=resume_token)
        flash("결제 내용을 확인하고 진행해 주세요.", "info")
        return redirect(url_for("web_billing.checkout_confirm_page", intent=resume_token))
    except BillingCheckoutValidationError as e:
        flash(str(e), "error")
    except Exception as e:
        current_app.logger.warning(
            "[WARN][billing] checkout_start_failed user=%s op=%s err=%s",
            user_id,
            operation_type,
            type(e).__name__,
        )
        flash("결제를 시작하지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
    return redirect(next_url)


@web_billing_bp.get("/checkout/confirm")
@login_required
def checkout_confirm_page():
    user_id = int(session.get("user_id"))
    resume_token = safe_str(request.args.get("intent"), max_len=180)
    if not resume_token:
        flash("결제 확인 정보가 없어요. 다시 시작해 주세요.", "error")
        return redirect(url_for("web_main.pricing"))
    intent = get_checkout_intent_by_resume_token(resume_token, user_pk=user_id)
    if not intent:
        flash("결제 정보를 찾지 못했어요. 다시 시작해 주세요.", "error")
        return redirect(url_for("web_main.pricing"))
    is_expired = bool(getattr(intent, "expires_at", None) and intent.expires_at <= datetime.now(timezone.utc))
    active_method = resolve_checkout_billing_method(user_pk=user_id, intent=intent)
    if (not active_method) and (not is_expired):
        flash("결제수단 등록이 먼저 필요해요.", "warn")
        return redirect(url_for("web_billing.register_page", resume=resume_token))

    op = str(getattr(intent, "intent_type", "") or "").strip().lower()
    op_label = {
        "initial_subscription": "최초 구독 시작",
        "upgrade": "프로 업그레이드",
        "addon_proration": "추가 계좌 구매",
    }.get(op, "결제")
    target_plan = str(getattr(intent, "target_plan_code", "") or "").strip().lower()
    target_plan_label = plan_label_ko(target_plan) if target_plan else "-"

    return render_template(
        "billing/checkout_confirm.html",
        intent=intent,
        resume_token=resume_token,
        operation_label=op_label,
        target_plan_label=target_plan_label,
        is_expired=is_expired,
        has_active_billing_method=bool(active_method),
    )


@web_billing_bp.get("/checkout/processing")
@login_required
def checkout_processing_page():
    user_id = int(session.get("user_id"))
    resume_token = safe_str(request.args.get("intent"), max_len=180)
    if not resume_token:
        flash("결제 연결 정보를 찾지 못했어요. 다시 시작해 주세요.", "error")
        return redirect(url_for("web_main.pricing"))

    intent = get_checkout_intent_by_resume_token(resume_token, user_pk=user_id)
    if not intent:
        flash("결제 정보를 찾지 못했어요. 다시 시작해 주세요.", "error")
        return redirect(url_for("web_main.pricing"))

    status = str(getattr(intent, "status", "") or "").strip().lower()
    is_expired = bool(getattr(intent, "expires_at", None) and intent.expires_at <= datetime.now(timezone.utc))

    if status == "registration_required":
        flash("결제수단 등록이 먼저 필요해요.", "warn")
        return redirect(url_for("web_billing.register_page", resume=resume_token))

    if is_expired and status not in {"completed", "canceled", "abandoned"}:
        flash("결제 요청이 만료되었어요. 다시 시작해 주세요.", "error")
        return redirect(url_for("web_main.pricing"))

    if status == "ready_for_charge":
        return render_template(
            "billing/processing.html",
            intent=intent,
            resume_token=resume_token,
            auto_submit=True,
        )

    if status in {"charge_started", "charge_in_progress", "awaiting_reconcile", "completed"}:
        attempt = (
            PaymentAttempt.query.filter_by(checkout_intent_id=int(intent.id))
            .order_by(PaymentAttempt.id.desc())
            .first()
        )
        order_id = safe_str(getattr(attempt, "order_id", ""), max_len=80)
        payment_key = safe_str(getattr(attempt, "payment_key", ""), max_len=160)
        if order_id:
            return redirect(
                url_for(
                    "web_billing.payment_success",
                    order_id=order_id,
                    paymentKey=payment_key or None,
                )
            )
        return render_template(
            "billing/processing.html",
            intent=intent,
            resume_token=resume_token,
            auto_submit=False,
            pending_message="결제 상태를 확인하고 있어요. 잠시 후 다시 확인해 주세요.",
        )

    if status in {"failed", "abandoned", "canceled"}:
        flash("이 결제는 다시 시작이 필요해요. 요금제 화면에서 다시 시도해 주세요.", "warn")
        return redirect(url_for("web_main.pricing"))

    flash("결제 상태를 확인할 수 없어요. 다시 시작해 주세요.", "error")
    return redirect(url_for("web_main.pricing"))


@web_billing_bp.post("/checkout/confirm")
@login_required
def checkout_confirm_submit():
    user_id = int(session.get("user_id"))
    resume_token = safe_str(request.form.get("intent"), max_len=180)
    if not resume_token:
        flash("결제 확인 정보가 없어요. 다시 시작해 주세요.", "error")
        return redirect(url_for("web_main.pricing"))

    idem_raw = safe_str(request.form.get("client_request_key"), max_len=80)
    idempotency_key = idem_raw or None
    try:
        result = confirm_checkout_intent_charge(
            user_pk=user_id,
            resume_token=resume_token,
            idempotency_key=idempotency_key,
            commit=True,
        )
    except BillingCheckoutValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("web_billing.checkout_confirm_page", intent=resume_token))
    except Exception as e:
        current_app.logger.warning(
            "[WARN][billing] checkout_confirm_failed user=%s err=%s",
            user_id,
            type(e).__name__,
        )
        flash("결제 요청을 진행하지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_billing.checkout_confirm_page", intent=resume_token))

    order_id = safe_str(result.get("order_id"), max_len=80)
    payment_key = safe_str(result.get("payment_key"), max_len=160)
    if not order_id:
        flash("결제 진행 결과를 확인하지 못했어요. 결제 내역에서 다시 확인해 주세요.", "warn")
        return redirect(url_for("web_main.pricing"))

    if bool(result.get("already_completed")):
        flash("이미 완료된 결제예요. 상태를 다시 확인했어요.", "info")
    elif bool(result.get("already_started")):
        flash("이미 진행 중인 결제예요. 최신 상태를 보여드릴게요.", "info")
    else:
        flash("결제를 확인하고 있어요. 잠시만 기다려 주세요.", "info")

    return redirect(
        url_for(
            "web_billing.payment_success",
            order_id=order_id,
            paymentKey=payment_key or None,
        )
    )


@web_billing_bp.get("/payment/success")
def payment_success():
    order_id = safe_str(request.args.get("order_id"), max_len=80)
    payment_key = safe_str(request.args.get("paymentKey"), max_len=160)
    attempt = _load_payment_attempt_for_result(order_id=order_id, payment_key=payment_key)

    attempt_status = str(getattr(attempt, "status", "") or "").strip().lower()
    is_reconciled = attempt_status == "reconciled"
    is_failed = attempt_status in {"failed", "canceled"}
    needs_review = attempt_status in {"charge_started", "authorized", "reconcile_needed"}
    operation_label = _attempt_operation_label(str(getattr(attempt, "attempt_type", "") or ""))
    fail_message = str(getattr(attempt, "fail_message_norm", "") or "").strip()
    intent = None
    if attempt and int(getattr(attempt, "checkout_intent_id", 0) or 0) > 0:
        intent = get_checkout_intent(int(attempt.checkout_intent_id))
    return_to = _resolve_return_to_for_intent(intent)

    if attempt and is_reconciled:
        _flash_billing_result_once(
            order_id=(order_id or str(getattr(attempt, "order_id", "") or "")),
            result_key="success",
            message=f"{operation_label}이(가) 완료되었어요.",
            category="success",
        )
        return redirect(return_to)
    if attempt and is_failed:
        fail_text = fail_message or "결제가 완료되지 않았어요. 결제수단 상태를 확인한 뒤 다시 시도해 주세요."
        _flash_billing_result_once(
            order_id=(order_id or str(getattr(attempt, "order_id", "") or "")),
            result_key="failed",
            message=fail_text,
            category="error",
        )
        return redirect(return_to)

    return render_template(
        "billing/payment_success.html",
        order_id=(order_id or str(getattr(attempt, "order_id", "") or "")),
        payment_key=(payment_key or str(getattr(attempt, "payment_key", "") or "")),
        attempt=attempt,
        operation_label=operation_label,
        attempt_status=attempt_status,
        is_reconciled=is_reconciled,
        is_failed=is_failed,
        needs_review=needs_review,
    )


@web_billing_bp.get("/payment/fail")
def payment_fail():
    order_id = safe_str(request.args.get("order_id"), max_len=80)
    payment_key = safe_str(request.args.get("paymentKey"), max_len=160)
    fail_code = safe_str(request.args.get("code"), max_len=80)
    fail_message = safe_str(request.args.get("message"), max_len=255)
    attempt = _load_payment_attempt_for_result(order_id=order_id, payment_key=payment_key)

    if attempt and not fail_message:
        fail_message = safe_str(getattr(attempt, "fail_message_norm", ""), max_len=255)
    if attempt and not fail_code:
        fail_code = safe_str(getattr(attempt, "fail_code", ""), max_len=80)
    if attempt:
        intent = None
        if int(getattr(attempt, "checkout_intent_id", 0) or 0) > 0:
            intent = get_checkout_intent(int(attempt.checkout_intent_id))
        return_to = _resolve_return_to_for_intent(intent)
        fail_text = fail_message or "결제가 완료되지 않았어요. 잠시 후 다시 시도해 주세요."
        _flash_billing_result_once(
            order_id=(order_id or str(getattr(attempt, "order_id", "") or "")),
            result_key="failed",
            message=fail_text,
            category="error",
        )
        return redirect(return_to)

    return render_template(
        "billing/payment_fail.html",
        order_id=(order_id or str(getattr(attempt, "order_id", "") or "")),
        payment_key=(payment_key or str(getattr(attempt, "payment_key", "") or "")),
        fail_code=fail_code,
        fail_message=fail_message,
        attempt=attempt,
        operation_label=_attempt_operation_label(str(getattr(attempt, "attempt_type", "") or "")),
    )


@web_billing_bp.get("/register/success")
def register_success():
    attempt_order_id = safe_str(request.args.get("attempt"), max_len=80)
    auth_key = safe_str(request.args.get("authKey"), max_len=255)
    customer_key = safe_str(request.args.get("customerKey"), max_len=180)
    callback_state = safe_str(request.args.get("state"), max_len=500)
    state_payload = _load_callback_state(callback_state)
    is_logged_in = bool(session.get("user_id"))

    if not attempt_order_id:
        if is_logged_in:
            flash("등록 시도 정보가 없어요. 다시 시도해 주세요.", "error")
            return redirect(url_for("web_billing.register_page"))
        return render_template("billing/fail.html", order_id="", is_logged_in=False)
    if not auth_key:
        if is_logged_in:
            flash("등록 확인 정보가 누락되었어요. 다시 시도해 주세요.", "error")
            return redirect(url_for("web_billing.register_page"))
        return render_template("billing/fail.html", order_id=attempt_order_id, is_logged_in=False)
    if not _verify_callback_state(state_payload, order_id=attempt_order_id, customer_key=customer_key):
        current_app.logger.warning(
            "[WARN][billing] registration_callback_state_mismatch order=%s",
            mask_sensitive_numbers(attempt_order_id)[:80],
        )
        if is_logged_in:
            flash("등록 확인 토큰이 올바르지 않아요. 다시 시도해 주세요.", "error")
            return redirect(url_for("web_billing.register_page"))
        return render_template("billing/fail.html", order_id=attempt_order_id, is_logged_in=False)

    try:
        key_version = get_active_billing_key_version()
        result = complete_registration_success_by_order(
            order_id=attempt_order_id,
            auth_key=auth_key,
            customer_key=customer_key,
            exchange_auth_key_fn=issue_billing_key,
            key_cipher=build_billing_key_cipher_for_version(key_version),
            encryption_key_version=key_version,
        )
        if is_logged_in:
            if result.get("already_completed"):
                flash("이미 등록된 결제수단이에요.", "success")
            else:
                flash("결제수단 등록이 완료되었어요.", "success")
    except Exception as e:
        current_app.logger.warning(
            "[WARN][billing] registration_success_failed order=%s err=%s",
            mask_sensitive_numbers(attempt_order_id)[:80],
            type(e).__name__,
        )
        if is_logged_in:
            flash(_friendly_registration_error(e), "error")
            return redirect(url_for("web_billing.register_page"))
        return render_template("billing/fail.html", order_id=attempt_order_id, is_logged_in=False)

    resumed_checkout = None
    resume_token = safe_str((state_payload or {}).get("resume"), max_len=180)
    if resume_token:
        try:
            resumed_checkout = resume_checkout_intent_after_registration(
                user_pk=int(result.get("user_pk") or 0),
                resume_token=resume_token,
                billing_method_id=int(result.get("billing_method_id") or 0),
                commit=True,
            )
            if is_logged_in and bool((resumed_checkout or {}).get("resumed")):
                flash("등록된 결제수단으로 결제를 이어서 진행할 수 있어요.", "info")
        except Exception as e:
            current_app.logger.warning(
                "[WARN][billing] resume_checkout_after_registration_failed order=%s err=%s",
                mask_sensitive_numbers(attempt_order_id)[:80],
                type(e).__name__,
            )

    if resume_token and is_logged_in:
        resume_status = str((resumed_checkout or {}).get("status") or "").strip().lower()
        resume_reason = str((resumed_checkout or {}).get("reason") or "").strip().lower()
        should_continue = resume_status in {
            "ready_for_charge",
            "charge_started",
            "charge_in_progress",
            "awaiting_reconcile",
            "completed",
        } or resume_reason in {
            "already_ready_for_charge",
            "charge_already_started",
            "already_finalized_or_started",
        }
        if not should_continue:
            intent = get_checkout_intent_by_resume_token(resume_token, user_pk=int(result.get("user_pk") or 0))
            intent_status = str(getattr(intent, "status", "") or "").strip().lower()
            should_continue = intent_status in {
                "ready_for_charge",
                "charge_started",
                "charge_in_progress",
                "awaiting_reconcile",
                "completed",
            }
        if should_continue:
            return redirect(url_for("web_billing.checkout_processing_page", intent=resume_token))

    return render_template(
        "billing/success.html",
        order_id=attempt_order_id,
        is_logged_in=is_logged_in,
        resume_token=(resume_token or ""),
        resumed_checkout=resumed_checkout or {},
    )


@web_billing_bp.get("/register/fail")
def register_fail():
    attempt_order_id = safe_str(request.args.get("attempt"), max_len=80)
    fail_code = safe_str(request.args.get("code"), max_len=80)
    fail_message = safe_str(request.args.get("message"), max_len=400)
    callback_state = safe_str(request.args.get("state"), max_len=500)
    state_payload = _load_callback_state(callback_state)
    customer_key = safe_str(request.args.get("customerKey"), max_len=180)
    is_logged_in = bool(session.get("user_id"))

    if attempt_order_id and _verify_callback_state(state_payload, order_id=attempt_order_id, customer_key=customer_key):
        try:
            mark_registration_failed_by_order(
                order_id=attempt_order_id,
                fail_code=fail_code,
                fail_message=fail_message,
            )
        except Exception as e:
            current_app.logger.warning(
                "[WARN][billing] registration_fail_record_failed order=%s err=%s",
                mask_sensitive_numbers(attempt_order_id)[:80],
                type(e).__name__,
            )

    if is_logged_in:
        flash(
            _friendly_registration_fail_notice(
                fail_code=fail_code,
                fail_message=fail_message,
            ),
            "error",
        )
    return render_template(
        "billing/fail.html",
        order_id=attempt_order_id,
        is_logged_in=is_logged_in,
    )

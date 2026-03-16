from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from services.billing.reconcile import BillingReconcileNotFound
from services.billing.service import ingest_payment_event, reconcile_payment_from_event
from services.sensitive_mask import mask_sensitive_numbers


api_billing_bp = Blueprint("api_billing", __name__, url_prefix="/api/billing")


def _pick_transmission_id() -> str | None:
    for header in (
        "Tosspayments-Webhook-Id",
        "X-Toss-Transmission-Id",
        "X-Request-Id",
        "Idempotency-Key",
    ):
        raw = str(request.headers.get(header) or "").strip()
        if raw:
            return raw
    return None


@api_billing_bp.post("/webhook")
def billing_webhook_receive():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False, "message": "JSON 형식만 지원해요."}), 400
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "웹훅 데이터 형식이 올바르지 않아요."}), 400

    try:
        ingest_result = ingest_payment_event(
            payload=payload,
            transmission_id=_pick_transmission_id(),
        )
        reconcile_result = None
        if bool(ingest_result.get("needs_reconcile")):
            try:
                reconcile_result = reconcile_payment_from_event(
                    payment_event_id=int(ingest_result.get("payment_event_id") or 0),
                    apply_projection=True,
                )
            except BillingReconcileNotFound:
                reconcile_result = None
            except Exception as reconcile_err:
                current_app.logger.warning(
                    "[WARN][billing] webhook_reconcile_deferred id=%s err=%s",
                    int(ingest_result.get("payment_event_id") or 0),
                    type(reconcile_err).__name__,
                )
                reconcile_result = None
        return jsonify(
            {
                "ok": True,
                "status": str((reconcile_result or {}).get("status_after") or ingest_result.get("status") or "received"),
                "duplicate": bool(ingest_result.get("duplicate")),
                "reconciled": bool((reconcile_result or {}).get("reconciled")),
            }
        ), 200
    except Exception as e:
        event_hint = mask_sensitive_numbers(str(payload.get("eventType") or payload.get("type") or ""))
        current_app.logger.error(
            "[ERR][billing] webhook_store_failed type=%s err=%s",
            event_hint[:64],
            type(e).__name__,
        )
        # 5xx 대신 503을 명시해 상위 시스템이 재시도 가능한 장애로 인식하도록 한다.
        return jsonify({"ok": False, "message": "웹훅 처리에 실패했어요. 잠시 후 다시 시도해 주세요."}), 503

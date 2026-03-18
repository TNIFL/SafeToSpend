from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, jsonify, request, session
from flask import current_app

from core.auth import login_required
from core.extensions import db
from domain.models import BankAccountLink, EvidenceItem, ExpenseLabel, Transaction
from services.evidence_vault import default_retention_until, store_evidence_file
from services.receipt_modal import (
    MAX_RECEIPT_MODAL_FILES,
    create_receipt_job,
    find_receipt_job_item,
    get_receipt_job,
    get_receipt_job_snapshot,
    kick_receipt_worker,
    list_recent_receipt_jobs,
    mark_receipt_job_item_created,
    mark_receipt_job_result,
    open_receipt_job_file,
    parse_receipt_confirm_item,
    update_receipt_job_item_draft,
)

web_receipt_modal_bp = Blueprint("web_receipt_modal", __name__, url_prefix="/dashboard/receipt-modal")

_BANK_CODE_NAME = {
    "0003": "IBK기업",
    "0004": "KB국민",
    "0011": "NH농협",
    "0020": "우리",
    "0081": "하나",
    "0088": "신한",
    "0090": "카카오뱅크",
    "0092": "토스뱅크",
}


def _uid() -> int:
    return int(session["user_id"])


def _mask_account(num: str) -> str:
    n = (num or "").strip()
    if len(n) <= 4:
        return n
    return f"****{n[-4:]}"


def _account_label(link: BankAccountLink) -> str:
    if link.alias:
        return link.alias
    bank = _BANK_CODE_NAME.get((link.bank_code or "").strip(), f"은행({link.bank_code})")
    return f"{bank} {_mask_account(link.account_number)}"


def _active_account_options(user_pk: int) -> list[dict]:
    rows = (
        BankAccountLink.query.filter(
            BankAccountLink.user_pk == user_pk,
            BankAccountLink.is_active.is_(True),
        )
        .order_by(BankAccountLink.bank_code.asc(), BankAccountLink.account_number.asc())
        .all()
    )
    return [
        {
            "id": int(link.id),
            "label": _account_label(link),
        }
        for link in rows
    ]


def _job_payload(user_pk: int, snapshot: dict) -> dict:
    return {
        "ok": True,
        "job": snapshot,
        "accounts": _active_account_options(user_pk),
        "max_files": MAX_RECEIPT_MODAL_FILES,
    }


def _history_payload(user_pk: int) -> dict:
    return {
        "ok": True,
        "jobs": list_recent_receipt_jobs(user_pk),
    }


def _evidence_defaults_from_usage(usage: str) -> tuple[str, str]:
    if usage == "business":
        return "required", "missing"
    if usage == "personal":
        return "not_needed", "not_needed"
    return "maybe", "missing"


def _compose_transaction_memo(
    *,
    memo: str | None,
    payment_item: str | None,
    payment_method: str | None,
    selected_account_label: str | None,
) -> str | None:
    parts: list[str] = []
    if selected_account_label:
        parts.append(f"[선택 계좌: {selected_account_label}]")
    if payment_item:
        parts.append(f"[결제 항목: {payment_item}]")
    if payment_method:
        parts.append(f"[결제 수단: {payment_method}]")
    if memo:
        parts.append(memo)
    value = " ".join(part.strip() for part in parts if part and part.strip()).strip()
    return value or None


@web_receipt_modal_bp.post("/start")
@login_required
def start() -> tuple[object, int] | object:
    user_pk = _uid()
    files = [file for file in request.files.getlist("files") if file and file.filename]

    try:
        snapshot = create_receipt_job(user_pk, files)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    kick_receipt_worker(current_app._get_current_object())
    return jsonify(_job_payload(user_pk, snapshot))


@web_receipt_modal_bp.get("/history")
@login_required
def history() -> object:
    kick_receipt_worker(current_app._get_current_object())
    return jsonify(_history_payload(_uid()))


@web_receipt_modal_bp.get("/jobs/<job_id>")
@login_required
def job_status(job_id: str) -> tuple[object, int] | object:
    user_pk = _uid()
    kick_receipt_worker(current_app._get_current_object())
    try:
        snapshot = get_receipt_job_snapshot(user_pk, job_id)
    except KeyError:
        return jsonify({"ok": False, "error": "진행 중인 영수증 작업을 찾지 못했습니다."}), 404

    return jsonify(_job_payload(user_pk, snapshot))


@web_receipt_modal_bp.post("/jobs/<job_id>/items/<item_id>")
@login_required
def save_item(job_id: str, item_id: str) -> tuple[object, int] | object:
    user_pk = _uid()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "수정 데이터 형식이 올바르지 않습니다."}), 400

    try:
        item_snapshot = update_receipt_job_item_draft(user_pk, job_id, item_id, payload)
        snapshot = get_receipt_job_snapshot(user_pk, job_id)
    except KeyError:
        return jsonify({"ok": False, "error": "수정할 영수증 항목을 찾지 못했습니다."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "item": item_snapshot, "job": snapshot})


@web_receipt_modal_bp.post("/jobs/<job_id>/create")
@login_required
def create(job_id: str) -> tuple[object, int] | object:
    user_pk = _uid()
    try:
        job = get_receipt_job(user_pk, job_id)
    except KeyError:
        return jsonify({"ok": False, "error": "진행 중인 영수증 작업을 찾지 못했습니다."}), 404

    snapshot = get_receipt_job_snapshot(user_pk, job_id)
    if not snapshot["is_complete"]:
        return jsonify({"ok": False, "error": "파싱이 아직 진행 중입니다. 잠시 후 다시 시도해 주세요."}), 400

    raw_items = request.form.get("items_json") or "[]"
    try:
        items = json.loads(raw_items)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "확인 데이터 형식이 올바르지 않습니다."}), 400

    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "생성할 영수증 항목을 다시 확인해 주세요."}), 400

    account_link = None
    account_link_id = (request.form.get("bank_account_link_id") or "").strip()
    if account_link_id:
        try:
            account_link = BankAccountLink.query.filter_by(
                id=int(account_link_id),
                user_pk=user_pk,
                is_active=True,
            ).first()
        except ValueError:
            account_link = None
        if account_link is None:
            return jsonify({"ok": False, "error": "선택한 계좌를 다시 확인해 주세요."}), 400

    created: list[dict] = []
    failed: list[dict] = []
    selected_account_label = _account_label(account_link) if account_link else None

    for raw_item in items:
        try:
            confirmed = parse_receipt_confirm_item(raw_item if isinstance(raw_item, dict) else {})
            item_id = confirmed["item_id"]
            if not item_id:
                raise ValueError("영수증 항목 식별자가 없습니다.")

            job_item = find_receipt_job_item(job, item_id)
            if job_item is None:
                raise ValueError("선택한 영수증 항목을 다시 불러와 주세요.")
            if job_item.status == "created":
                raise ValueError("이미 생성된 영수증 항목입니다.")
            if job_item.status != "ready":
                raise ValueError("파싱이 완료된 영수증만 생성할 수 있습니다.")

            occurred_at = confirmed["occurred_at"]
            amount_krw = int(confirmed["amount_krw"])
            usage = str(confirmed["usage"])
            counterparty = confirmed["counterparty"]
            memo = _compose_transaction_memo(
                memo=confirmed["memo"],
                payment_item=confirmed["payment_item"],
                payment_method=confirmed["payment_method"],
                selected_account_label=selected_account_label,
            )

            external_hash = hashlib.sha256(
                f"receipt-modal:{user_pk}:{uuid4().hex}:{job_item.original_filename}".encode("utf-8")
            ).hexdigest()

            tx = Transaction(
                user_pk=user_pk,
                import_job_id=None,
                occurred_at=occurred_at,
                direction="out",
                amount_krw=amount_krw,
                counterparty=counterparty,
                memo=memo,
                source="receipt_modal",
                external_hash=external_hash,
            )
            db.session.add(tx)
            db.session.flush()

            requirement, default_status = _evidence_defaults_from_usage(usage)
            confidence = 100 if usage != "unknown" else 0
            decided_at = datetime.now(timezone.utc) if usage != "unknown" else None

            db.session.add(
                ExpenseLabel(
                    user_pk=user_pk,
                    transaction_id=int(tx.id),
                    status=usage,
                    confidence=confidence,
                    labeled_by="user",
                    decided_at=decided_at,
                    note="영수증 모달에서 생성",
                )
            )

            ev = EvidenceItem(
                user_pk=user_pk,
                transaction_id=int(tx.id),
                requirement=requirement,
                status=default_status,
                note="영수증 모달 업로드",
            )
            db.session.add(ev)
            db.session.flush()

            file_storage = open_receipt_job_file(job_item)
            try:
                stored = store_evidence_file(
                    user_pk=user_pk,
                    tx_id=int(tx.id),
                    month_key=occurred_at.strftime("%Y-%m"),
                    file=file_storage,
                )
            finally:
                file_storage.stream.close()

            ev.file_key = stored.file_key
            ev.original_filename = stored.original_filename
            ev.mime_type = stored.mime_type
            ev.size_bytes = stored.size_bytes
            ev.sha256 = stored.sha256
            ev.uploaded_at = datetime.now(timezone.utc)
            ev.deleted_at = None
            ev.retention_until = default_retention_until()
            ev.status = "attached"

            db.session.commit()
            mark_receipt_job_item_created(job, item_id, int(tx.id))
            created.append(
                {
                    "item_id": item_id,
                    "transaction_id": int(tx.id),
                    "filename": job_item.original_filename,
                    "counterparty": tx.counterparty,
                    "amount_krw": int(tx.amount_krw),
                }
            )
        except ValueError as exc:
            db.session.rollback()
            failed.append(
                {
                    "item_id": str((raw_item or {}).get("item_id") or ""),
                    "filename": str((raw_item or {}).get("filename") or "영수증"),
                    "error": str(exc),
                }
            )
        except Exception:
            db.session.rollback()
            failed.append(
                {
                    "item_id": str((raw_item or {}).get("item_id") or ""),
                    "filename": str((raw_item or {}).get("filename") or "영수증"),
                    "error": "거래 생성 중 문제가 발생했습니다.",
                }
            )

    result = {
        "ok": bool(created),
        "created_count": len(created),
        "failed_count": len(failed),
        "selected_account_label": selected_account_label,
        "created": created,
        "failed": failed,
    }
    mark_receipt_job_result(job, result)
    result["job"] = get_receipt_job_snapshot(user_pk, job_id)
    return jsonify(result)

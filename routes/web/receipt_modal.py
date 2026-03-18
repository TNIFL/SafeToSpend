from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, jsonify, request, session

from core.auth import login_required
from core.extensions import db
from domain.models import BankAccountLink, EvidenceItem, ExpenseLabel, Transaction
from services.evidence_vault import default_retention_until, store_evidence_file
from services.receipt_modal import (
    MAX_RECEIPT_MODAL_FILES,
    build_receipt_preview,
    parse_receipt_confirm_item,
    validate_receipt_image,
    validate_receipt_modal_files,
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


def _evidence_defaults_from_usage(usage: str) -> tuple[str, str]:
    if usage == "business":
        return "required", "missing"
    if usage == "personal":
        return "not_needed", "not_needed"
    return "maybe", "missing"


@web_receipt_modal_bp.post("/preview")
@login_required
def preview() -> tuple[object, int] | object:
    user_pk = _uid()
    files = [file for file in request.files.getlist("files") if file and file.filename]

    try:
        validate_receipt_modal_files(files)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    items: list[dict] = []
    for index, file in enumerate(files):
        try:
            items.append(build_receipt_preview(file, client_index=index))
        except ValueError as exc:
            items.append(
                {
                    "client_index": index,
                    "filename": file.filename or f"receipt-{index + 1}",
                    "status": "error",
                    "error": str(exc),
                }
            )

    return jsonify(
        {
            "ok": any(item.get("status") == "ready" for item in items),
            "max_files": MAX_RECEIPT_MODAL_FILES,
            "items": items,
            "accounts": _active_account_options(user_pk),
        }
    )


@web_receipt_modal_bp.post("/create")
@login_required
def create() -> tuple[object, int] | object:
    user_pk = _uid()
    files = [file for file in request.files.getlist("files") if file and file.filename]

    try:
        validate_receipt_modal_files(files)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    raw_items = request.form.get("items_json") or "[]"
    try:
        items = json.loads(raw_items)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "확인 데이터 형식이 올바르지 않습니다."}), 400

    if not isinstance(items, list) or len(items) != len(files):
        return jsonify({"ok": False, "error": "업로드한 파일과 확인 데이터가 맞지 않습니다."}), 400

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

    for index, (file, raw_item) in enumerate(zip(files, items)):
        try:
            filename, mime_type, _size_bytes = validate_receipt_image(file)
            confirmed = parse_receipt_confirm_item(raw_item if isinstance(raw_item, dict) else {})
            occurred_at = confirmed["occurred_at"]
            amount_krw = int(confirmed["amount_krw"])
            usage = str(confirmed["usage"])
            counterparty = confirmed["counterparty"]
            memo = confirmed["memo"]

            if selected_account_label:
                account_note = f"[선택 계좌: {selected_account_label}]"
                memo = f"{account_note} {memo}" if memo else account_note

            external_hash = hashlib.sha256(
                f"receipt-modal:{user_pk}:{uuid4().hex}:{filename}".encode("utf-8")
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

            stored = store_evidence_file(
                user_pk=user_pk,
                tx_id=int(tx.id),
                month_key=occurred_at.strftime("%Y-%m"),
                file=file,
            )

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
            created.append(
                {
                    "client_index": index,
                    "transaction_id": int(tx.id),
                    "filename": filename,
                    "counterparty": tx.counterparty,
                    "amount_krw": int(tx.amount_krw),
                }
            )
        except ValueError as exc:
            db.session.rollback()
            failed.append(
                {
                    "client_index": index,
                    "filename": file.filename or f"receipt-{index + 1}",
                    "error": str(exc),
                }
            )
        except Exception:
            db.session.rollback()
            failed.append(
                {
                    "client_index": index,
                    "filename": file.filename or f"receipt-{index + 1}",
                    "error": "거래 생성 중 문제가 발생했습니다.",
                }
            )

    return jsonify(
        {
            "ok": bool(created),
            "created_count": len(created),
            "failed_count": len(failed),
            "created": created,
            "failed": failed,
            "selected_account_label": selected_account_label,
        }
    )

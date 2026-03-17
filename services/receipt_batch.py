from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy import func, inspect, select

from core.extensions import db
from core.time import utcnow
from domain.models import EvidenceItem, ReceiptBatch, ReceiptItem
from services.evidence_vault import evidence_abs_path
from services.receipt_parser import parse_receipt_from_file, parse_receipt_from_text
from services.security_audit import audit_event
from services.sensitive_mask import mask_sensitive_numbers


ITEM_STATUS_UPLOADED = "uploaded"
ITEM_STATUS_PROCESSING = "processing"
ITEM_STATUS_DONE = "done"
ITEM_STATUS_FAILED = "failed"

BATCH_STATUS_QUEUED = "queued"
BATCH_STATUS_PROCESSING = "processing"
BATCH_STATUS_DONE = "done"
BATCH_STATUS_DONE_WITH_ERRORS = "done_with_errors"

ERROR_CODE_FORMAT = "format"
ERROR_CODE_MISSING_FILE = "missing_file"
ERROR_CODE_TEMPORARY = "temporary"
ERROR_CODE_PARSE = "parse"
ERROR_CODE_PAUSED = "paused"
ERROR_CODE_DUPLICATE = "duplicate"
ERROR_CODE_UNKNOWN = "unknown"

RECEIPT_RETRY_META_KEY = "__retry_meta"
RECEIPT_RETRY_LIMIT = 3
RECEIPT_RETRY_BACKOFF_SECONDS = (0, 30, 120, 600)
RECEIPT_NON_RETRYABLE_CODES = {
    ERROR_CODE_FORMAT,
    ERROR_CODE_MISSING_FILE,
    ERROR_CODE_DUPLICATE,
}


def _log_receipt_progress(*, status: str, file_name: str, level: str = "INFO", detail: str = "") -> None:
    name = str(file_name or "이름 없는 파일").strip() or "이름 없는 파일"
    extra = f", {detail}" if detail else ""
    line = f"[{level}][영수증으로 거래 추가][{status}] : {name}{extra}"
    if has_app_context():
        if level == "ERROR":
            current_app.logger.error(line)
        else:
            current_app.logger.info(line)
        try:
            s = str(status or "").strip()
            if s == "실패":
                audit_event("receipt_parse_failed", outcome="denied", detail=(detail or file_name))
            elif s == "완료":
                audit_event("receipt_parse_done", outcome="ok", detail=file_name)
            elif s == "실행 중":
                audit_event("receipt_parse_start", outcome="ok", detail=file_name)
        except Exception:
            pass
        return
    # 워커 등 앱 컨텍스트가 없는 상황 fallback
    print(line)


def normalize_receipt_error(raw_message: str | None) -> tuple[str, str]:
    text = str(raw_message or "").strip()
    low = text.lower()

    if not text:
        return ERROR_CODE_UNKNOWN, "영수증 분석에 실패했어요."
    if ("pillow-heif" in low) or ("heic/heif 변환 라이브러리" in text):
        return ERROR_CODE_FORMAT, "HEIC 사진은 아직 바로 처리되지 않을 수 있어요. JPG/PNG로 바꿔 다시 올려주세요."
    if "openai_api_key" in low:
        return ERROR_CODE_TEMPORARY, "영수증 분석 설정이 아직 준비되지 않았어요(개발용 설정 필요). 잠시 후 다시 시도해주세요."
    if ("허용되지 않는 파일 형식" in text) or ("지원 형식이 아니에요" in text and "이미지/pdf" in low):
        return ERROR_CODE_FORMAT, "지원 형식이 아니에요. 이미지/PDF 파일만 올려주세요."
    if ("파일 정보를 찾지 못했어요" in text) or ("업로드 파일을 찾지 못했어요" in text):
        return ERROR_CODE_MISSING_FILE, "업로드 파일을 찾지 못했어요. 다시 올려주세요."
    if ("rate limit" in low) or ("too many requests" in low) or ("timeout" in low) or ("timed out" in low):
        return ERROR_CODE_TEMPORARY, "일시적으로 처리 지연이 있어요. 잠시 후 다시 시도해주세요."
    if ("openai 오류" in text) or ("분석 실패" in text) or ("image data" in low):
        return ERROR_CODE_PARSE, "사진을 읽기 어려워 분석하지 못했어요. 더 선명한 파일로 다시 시도해주세요."
    if ("사용자가 중단" in text) or ("중단했어요" in text):
        return ERROR_CODE_PAUSED, "사용자가 중단했어요. 필요하면 항목별로 다시 시도할 수 있어요."
    if ("이미 처리 중이거나 완료된 같은 영수증" in text) or ("이미 등록된 영수증" in text):
        return ERROR_CODE_DUPLICATE, "같은 영수증이 이미 처리되었어요. 기존 거래에서 확인해 주세요."

    short = text[:220] + ("…" if len(text) > 220 else "")
    return ERROR_CODE_UNKNOWN, short


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw or default)
    except Exception:
        return int(default)


def _parse_dt(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _retry_meta_from_item(item: ReceiptItem | None) -> dict[str, Any]:
    parsed = to_jsonable_parsed(getattr(item, "parsed_json", None))
    meta = parsed.get(RECEIPT_RETRY_META_KEY) if isinstance(parsed, dict) else {}
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def _write_retry_meta(item: ReceiptItem, meta: dict[str, Any], *, parsed_payload: dict[str, Any] | None = None) -> None:
    payload = dict(parsed_payload or to_jsonable_parsed(item.parsed_json))
    payload[RECEIPT_RETRY_META_KEY] = dict(meta)
    item.parsed_json = payload


def _retry_backoff_seconds(attempt_count: int) -> int:
    idx = max(0, min(len(RECEIPT_RETRY_BACKOFF_SECONDS) - 1, int(attempt_count)))
    return int(RECEIPT_RETRY_BACKOFF_SECONDS[idx])


def get_receipt_retry_info(item: ReceiptItem) -> dict[str, Any]:
    meta = _retry_meta_from_item(item)
    attempt_count = max(0, _safe_int(meta.get("attempt_count"), 0))
    dead_letter = bool(meta.get("dead_letter"))
    next_retry_at = _parse_dt(meta.get("next_retry_at"))
    code = str(meta.get("error_code") or "").strip() or normalize_receipt_error(item.error_message or "")[0]
    if code in RECEIPT_NON_RETRYABLE_CODES:
        dead_letter = True
    wait_seconds = 0
    if isinstance(next_retry_at, datetime):
        wait_seconds = int(max(0, (next_retry_at - utcnow()).total_seconds()))
    return {
        "attempt_count": attempt_count,
        "dead_letter": bool(dead_letter),
        "next_retry_at": next_retry_at,
        "wait_seconds": wait_seconds,
        "error_code": code,
    }


def can_retry_receipt_item(item: ReceiptItem) -> tuple[bool, str]:
    if item.status != ITEM_STATUS_FAILED:
        return False, "not_failed"
    if not str(item.file_key or "").strip():
        return False, "missing_file"

    info = get_receipt_retry_info(item)
    code = str(info.get("error_code") or "")
    if code in RECEIPT_NON_RETRYABLE_CODES:
        return False, f"non_retryable:{code}"
    if bool(info.get("dead_letter")):
        return False, "dead_letter"
    wait_seconds = int(info.get("wait_seconds") or 0)
    if wait_seconds > 0:
        return False, f"backoff:{wait_seconds}"
    if int(info.get("attempt_count") or 0) >= int(RECEIPT_RETRY_LIMIT):
        return False, "retry_limit"
    return True, "ok"


def retry_block_message(reason: str) -> str:
    code = str(reason or "").strip().lower()
    if code.startswith("non_retryable:duplicate"):
        return "중복으로 감지된 항목은 다시 시도할 수 없어요. 기존 거래와 중복인지 먼저 확인해 주세요."
    if code.startswith("non_retryable:format"):
        return "파일 형식 문제로 실패했어요. JPG/PNG/PDF 파일로 다시 올려주세요."
    if code.startswith("non_retryable:missing_file"):
        return "업로드 파일을 찾지 못해 다시 시도할 수 없어요. 파일을 다시 올려주세요."
    if code.startswith("backoff:"):
        try:
            wait_seconds = max(1, int(code.split(":", 1)[1]))
        except Exception:
            wait_seconds = 30
        return f"잠시 후 다시 시도해 주세요. ({wait_seconds}초 후 가능)"
    if code in {"dead_letter", "retry_limit"}:
        return "여러 번 실패한 항목이에요. 파일을 다시 올려 새 항목으로 처리해 주세요."
    if code == "missing_file":
        return "업로드 파일이 없어 재시도할 수 없어요."
    if code == "not_failed":
        return "실패한 항목만 다시 시도할 수 있어요."
    return "지금은 다시 시도할 수 없어요. 잠시 후 다시 확인해 주세요."


def mark_receipt_item_failed(item: ReceiptItem, raw_message: str | None) -> tuple[str, str]:
    code, friendly = normalize_receipt_error(raw_message or "")
    meta = _retry_meta_from_item(item)
    attempts_before = max(0, _safe_int(meta.get("attempt_count"), 0))
    attempts_after = attempts_before + 1
    dead_letter = bool(code in RECEIPT_NON_RETRYABLE_CODES or attempts_after >= int(RECEIPT_RETRY_LIMIT))
    backoff_seconds = 0 if dead_letter else _retry_backoff_seconds(attempts_after)
    next_retry_at = (utcnow() + timedelta(seconds=backoff_seconds)) if backoff_seconds > 0 else None
    next_retry_text = next_retry_at.isoformat(timespec="seconds") if isinstance(next_retry_at, datetime) else ""

    meta.update(
        {
            "attempt_count": int(attempts_after),
            "error_code": str(code),
            "dead_letter": bool(dead_letter),
            "next_retry_at": next_retry_text,
            "last_failed_at": utcnow().isoformat(timespec="seconds"),
        }
    )
    _write_retry_meta(item, meta)
    item.status = ITEM_STATUS_FAILED
    item.error_message = str(friendly or "영수증 분석에 실패했어요.")[:500]
    item.updated_at = utcnow()
    return code, item.error_message


def reset_receipt_item_for_retry(item: ReceiptItem) -> None:
    item.status = ITEM_STATUS_UPLOADED
    item.error_message = ""
    meta = _retry_meta_from_item(item)
    if meta:
        meta["next_retry_at"] = ""
        meta["dead_letter"] = False
        _write_retry_meta(item, meta)
    item.updated_at = utcnow()


def mark_receipt_item_paused(item: ReceiptItem, message: str | None = None) -> None:
    msg = str(message or "사용자가 중단했어요. 필요하면 다시 시도해주세요.").strip()
    meta = _retry_meta_from_item(item)
    meta["attempt_count"] = 0
    meta["error_code"] = ERROR_CODE_PAUSED
    meta["dead_letter"] = False
    meta["next_retry_at"] = ""
    meta["last_failed_at"] = utcnow().isoformat(timespec="seconds")
    _write_retry_meta(item, meta)
    item.status = ITEM_STATUS_FAILED
    item.error_message = msg[:500]
    item.updated_at = utcnow()


def compact_receipt_parsed(parsed: dict | None) -> dict:
    src = parsed if isinstance(parsed, dict) else {}
    if not src:
        return {}

    def _s(key: str, max_len: int = 120) -> str:
        val = src.get(key)
        if val is None:
            return ""
        text = mask_sensitive_numbers(str(val)).strip()
        if not text:
            return ""
        return text[:max_len]

    out = {
        "merchant": _s("merchant", 120),
        "paid_at": _s("paid_at", 25),
        "total_krw": _s("total_krw", 24),
    }
    for k in ("vat_krw", "currency", "payment_method", "business_no", "card_tail", "approval_no"):
        v = _s(k, 60)
        if v:
            out[k] = v
    return out


def build_draft_payload_from_item(item: ReceiptItem) -> dict[str, Any]:
    item_error = str(item.error_message or "").strip()
    duplicate_suspected = "중복" in item_error
    return {
        "month_key": "",
        "file_key": item.file_key or "",
        "original_filename": item.original_filename or "",
        "mime_type": item.mime_type or "",
        "size_bytes": int(item.size_bytes or 0),
        "sha256": item.sha256 or "",
        "receipt_type": item.receipt_type or "paper",
        "draft_ok": item.status == ITEM_STATUS_DONE,
        "draft_provider": "worker",
        "draft_error": item_error,
        "duplicate_suspected": bool(duplicate_suspected),
        "duplicate_hint": (item_error[:200] if duplicate_suspected else ""),
        "parsed": compact_receipt_parsed(item.parsed_json if isinstance(item.parsed_json, dict) else {}),
    }


def recompute_batch_counts(batch_id: int) -> ReceiptBatch | None:
    batch = db.session.get(ReceiptBatch, int(batch_id))
    if not batch:
        return None

    rows = (
        db.session.query(ReceiptItem.status, func.count(ReceiptItem.id))
        .filter(ReceiptItem.batch_id == batch.id)
        .group_by(ReceiptItem.status)
        .all()
    )
    status_counts = {str(status): int(cnt or 0) for status, cnt in rows}

    total = int(sum(status_counts.values()))
    done = int(status_counts.get(ITEM_STATUS_DONE, 0))
    failed = int(status_counts.get(ITEM_STATUS_FAILED, 0))
    processing = int(status_counts.get(ITEM_STATUS_PROCESSING, 0))
    uploaded = int(status_counts.get(ITEM_STATUS_UPLOADED, 0))

    batch.total_count = total
    batch.done_count = done
    batch.failed_count = failed

    if processing > 0:
        batch.status = BATCH_STATUS_PROCESSING
    elif uploaded > 0:
        batch.status = BATCH_STATUS_QUEUED
    elif total > 0 and (done + failed) >= total:
        batch.status = BATCH_STATUS_DONE_WITH_ERRORS if failed > 0 else BATCH_STATUS_DONE
    else:
        batch.status = BATCH_STATUS_QUEUED
    batch.updated_at = utcnow()
    return batch


def claim_next_uploaded_item() -> ReceiptItem | None:
    stmt = (
        select(ReceiptItem)
        .where(ReceiptItem.status == ITEM_STATUS_UPLOADED)
        .order_by(ReceiptItem.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    item = db.session.execute(stmt).scalars().first()
    if not item:
        return None
    item.status = ITEM_STATUS_PROCESSING
    item.error_message = None
    item.updated_at = utcnow()
    return item


def claim_next_uploaded_item_for_batch(*, user_pk: int, batch_id: int) -> ReceiptItem | None:
    """특정 사용자/배치에 대해 uploaded 항목 1건을 안전하게 선점한다."""
    try:
        uid = int(user_pk)
        bid = int(batch_id)
    except Exception:
        return None
    if uid <= 0 or bid <= 0:
        return None

    stmt = (
        select(ReceiptItem)
        .where(
            ReceiptItem.status == ITEM_STATUS_UPLOADED,
            ReceiptItem.user_pk == uid,
            ReceiptItem.batch_id == bid,
        )
        .order_by(ReceiptItem.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    item = db.session.execute(stmt).scalars().first()
    if not item:
        return None
    item.status = ITEM_STATUS_PROCESSING
    item.error_message = None
    item.updated_at = utcnow()
    return item


def requeue_stale_processing_items(*, max_age_minutes: int = 15, limit: int = 100) -> int:
    """
    워커 중단 등으로 processing 상태에서 오래 멈춘 항목을 uploaded로 복구.
    """
    cutoff = utcnow() - timedelta(minutes=max(1, int(max_age_minutes)))
    stale_items = (
        ReceiptItem.query.filter(
            ReceiptItem.status == ITEM_STATUS_PROCESSING,
            ReceiptItem.updated_at <= cutoff,
        )
        .order_by(ReceiptItem.updated_at.asc(), ReceiptItem.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    if not stale_items:
        return 0

    touched_batch_ids: set[int] = set()
    now = utcnow()
    for item in stale_items:
        item.status = ITEM_STATUS_UPLOADED
        msg = (item.error_message or "").strip()
        if not msg:
            msg = "이전 작업이 중단되어 다시 시도해요."
        item.error_message = msg[:500]
        item.updated_at = now
        if item.batch_id:
            touched_batch_ids.add(int(item.batch_id))

    for batch_id in touched_batch_ids:
        recompute_batch_counts(batch_id)
    return len(stale_items)


def process_receipt_item(item_id: int) -> tuple[bool, str | None]:
    item = db.session.get(ReceiptItem, int(item_id))
    if not item:
        return False, "대상 항목을 찾지 못했어요."
    if item.status == ITEM_STATUS_DONE:
        return True, None
    file_name = (item.original_filename or f"item-{int(item.id)}").strip()
    if not item.file_key:
        _ = mark_receipt_item_failed(item, "파일 정보를 찾지 못했어요.")
        _log_receipt_progress(status="실패", file_name=file_name, level="ERROR", detail=item.error_message)
        return False, item.error_message

    # 같은 파일 해시가 이미 처리 완료된 경우 재처리하지 않는다.
    sha_value = str(item.sha256 or "").strip()
    if sha_value:
        duplicate_exists = (
            db.session.query(EvidenceItem.id)
            .filter(EvidenceItem.user_pk == item.user_pk)
            .filter(EvidenceItem.deleted_at.is_(None))
            .filter(EvidenceItem.sha256 == sha_value)
            .first()
            is not None
        )
        if not duplicate_exists:
            duplicate_exists = (
                db.session.query(ReceiptItem.id)
                .filter(ReceiptItem.user_pk == item.user_pk)
                .filter(ReceiptItem.id != item.id)
                .filter(ReceiptItem.sha256 == sha_value)
                .filter(ReceiptItem.status == ITEM_STATUS_DONE)
                .first()
                is not None
            )
        if duplicate_exists:
            _ = mark_receipt_item_failed(item, "이미 처리 중이거나 완료된 같은 영수증이 있어요. 기존 거래를 먼저 확인해 주세요.")
            _log_receipt_progress(status="실패", file_name=file_name, level="ERROR", detail=item.error_message)
            return False, item.error_message

    try:
        abs_path = evidence_abs_path(item.file_key)
    except Exception:
        _ = mark_receipt_item_failed(item, "업로드 파일을 찾지 못했어요.")
        _log_receipt_progress(status="실패", file_name=file_name, level="ERROR", detail=item.error_message)
        return False, item.error_message

    try:
        mime = (item.mime_type or "").strip()
        if mime.startswith("text/") or abs_path.suffix.lower() == ".txt":
            txt = abs_path.read_text(encoding="utf-8", errors="ignore")
            draft = parse_receipt_from_text(text=txt)
        else:
            draft = parse_receipt_from_file(abs_path=abs_path, mime_type=mime)
    except Exception as e:
        _ = mark_receipt_item_failed(item, f"분석 실패: {e}")
        _log_receipt_progress(status="실패", file_name=file_name, level="ERROR", detail=item.error_message or "")
        return False, item.error_message

    parsed = compact_receipt_parsed(getattr(draft, "parsed", {}) or {})
    if bool(getattr(draft, "ok", False)):
        item.status = ITEM_STATUS_DONE
        item.error_message = ""
        _write_retry_meta(
            item,
            {
                "attempt_count": 0,
                "error_code": "",
                "dead_letter": False,
                "next_retry_at": "",
                "last_success_at": utcnow().isoformat(timespec="seconds"),
            },
            parsed_payload=parsed,
        )
        _log_receipt_progress(status="완료", file_name=file_name)
    else:
        _ = mark_receipt_item_failed(item, getattr(draft, "error", "") or "영수증 분석에 실패했어요.")
        _log_receipt_progress(status="실패", file_name=file_name, level="ERROR", detail=item.error_message or "")
        # 실패 시에도 핵심 파싱값은 남겨서 재확인 UX를 유지한다.
        _write_retry_meta(item, _retry_meta_from_item(item), parsed_payload=parsed)
    if item.status == ITEM_STATUS_DONE:
        # 성공 시에는 parsed payload가 _write_retry_meta에서 이미 반영된다.
        pass
    item.updated_at = utcnow()
    return item.status == ITEM_STATUS_DONE, (item.error_message or None)


def iso_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat(timespec="seconds")


def to_jsonable_parsed(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            obj = json.loads(value)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return {}
    return {}


def batch_tables_ready() -> bool:
    try:
        insp = inspect(db.engine)
        return bool(insp.has_table("receipt_batches") and insp.has_table("receipt_items"))
    except Exception:
        return False


def get_user_processing_summary(user_pk: int) -> dict[str, Any]:
    try:
        uid = int(user_pk)
    except Exception:
        uid = 0

    out = {"in_progress_count": 0, "batch_id": 0, "month_key": ""}
    if uid <= 0:
        return out
    if not batch_tables_ready():
        return out

    in_progress_count = (
        db.session.query(func.count(ReceiptItem.id))
        .filter(
            ReceiptItem.user_pk == uid,
            ReceiptItem.status.in_((ITEM_STATUS_UPLOADED, ITEM_STATUS_PROCESSING)),
        )
        .scalar()
        or 0
    )
    in_progress_count = int(in_progress_count or 0)
    if in_progress_count <= 0:
        return out

    row = (
        db.session.query(ReceiptBatch.id, ReceiptBatch.month_key)
        .select_from(ReceiptBatch)
        .join(ReceiptItem, ReceiptItem.batch_id == ReceiptBatch.id)
        .filter(
            ReceiptBatch.user_pk == uid,
            ReceiptItem.status.in_((ITEM_STATUS_UPLOADED, ITEM_STATUS_PROCESSING)),
        )
        .order_by(ReceiptBatch.updated_at.desc(), ReceiptBatch.id.desc())
        .first()
    )
    batch_id = int(row[0]) if row and row[0] else 0
    month_key = str((row[1] if row and row[1] else "")).strip()
    return {"in_progress_count": in_progress_count, "batch_id": batch_id, "month_key": month_key}

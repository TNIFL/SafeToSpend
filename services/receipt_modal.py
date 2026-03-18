from __future__ import annotations

import mimetypes
import re
import shutil
import tempfile
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage

MAX_RECEIPT_MODAL_FILES = 50
ALLOWED_RECEIPT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
ALLOWED_RECEIPT_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
_RECEIPT_JOB_TTL_SECONDS = 6 * 60 * 60
_DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})[._-]?(?P<m>\d{2})[._-]?(?P<d>\d{2})"),
    re.compile(r"(?P<y>\d{2})[._-]?(?P<m>\d{2})[._-]?(?P<d>\d{2})"),
]
_TIME_PATTERN = re.compile(r"(?<!\d)(?P<h>\d{1,2})[:시]?(?P<m>\d{2})(?!\d)")
_AMOUNT_PATTERNS = [
    re.compile(r"(?P<amount>\d[\d,]{2,})\s*(?:원|krw)", re.IGNORECASE),
    re.compile(r"(?:amt|amount)[-_ ]*(?P<amount>\d[\d,]{2,})", re.IGNORECASE),
]
_CARD_PATTERN = re.compile(r"(?:card|카드)[-_ ]*(?P<digits>\d{4})", re.IGNORECASE)
_ACCOUNT_PATTERN = re.compile(r"(?:account|acct|계좌)[-_ ]*(?P<digits>\d{4})", re.IGNORECASE)
_ITEM_PATTERN = re.compile(r"(?:item|품목|항목)[-_ ]*(?P<value>[A-Za-z0-9가-힣 _-]{2,})", re.IGNORECASE)
_NOISE_TOKENS = {
    "receipt",
    "receipts",
    "영수증",
    "card",
    "image",
    "img",
    "photo",
    "scan",
    "upload",
    "uploaded",
    "heic",
    "jpg",
    "jpeg",
    "png",
    "webp",
}
_RECEIPT_JOBS: dict[str, "ReceiptModalJob"] = {}
_RECEIPT_JOBS_LOCK = threading.RLock()


@dataclass
class ReceiptModalJobItem:
    item_id: str
    client_index: int
    filename: str
    mime_type: str
    size_bytes: int
    stored_path: str | None
    status: str = "queued"
    error: str | None = None
    occurred_on: str | None = None
    occurred_time: str | None = None
    amount_krw: int | None = None
    counterparty: str | None = None
    payment_item: str | None = None
    payment_method: str | None = None
    memo: str | None = None
    usage: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    created_transaction_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "client_index": self.client_index,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "error": self.error,
            "occurred_on": self.occurred_on,
            "occurred_time": self.occurred_time,
            "amount_krw": self.amount_krw,
            "counterparty": self.counterparty,
            "payment_item": self.payment_item,
            "payment_method": self.payment_method,
            "memo": self.memo,
            "usage": self.usage,
            "warnings": list(self.warnings),
            "created_transaction_id": self.created_transaction_id,
        }


@dataclass
class ReceiptModalJob:
    job_id: str
    user_pk: int
    created_at_ts: float
    updated_at_ts: float
    storage_dir: str
    status: str = "queued"
    items: list[ReceiptModalJobItem] = field(default_factory=list)
    created_count: int = 0
    failed_count: int = 0
    last_result: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        processing_count = sum(1 for item in self.items if item.status in {"queued", "processing"})
        ready_count = sum(1 for item in self.items if item.status == "ready")
        error_count = sum(1 for item in self.items if item.status == "error")
        created_count = sum(1 for item in self.items if item.status == "created")
        is_complete = processing_count == 0
        return {
            "job_id": self.job_id,
            "status": self.status,
            "items": [item.to_dict() for item in self.items],
            "ready_count": ready_count,
            "error_count": error_count,
            "processing_count": processing_count,
            "created_count": created_count,
            "is_complete": is_complete,
            "created_at": int(self.created_at_ts),
            "updated_at": int(self.updated_at_ts),
            "last_result": self.last_result,
        }


def _guess_mime(filename: str | None, fallback: str = "application/octet-stream") -> str:
    if not filename:
        return fallback
    mt, _ = mimetypes.guess_type(filename)
    return mt or fallback


def _measure_file_size(file: FileStorage) -> int:
    stream = file.stream
    pos = stream.tell()
    stream.seek(0, 2)
    size = int(stream.tell() or 0)
    stream.seek(pos)
    return size


def validate_receipt_modal_files(files: list[FileStorage]) -> None:
    if not files:
        raise ValueError("업로드할 영수증 이미지를 선택해 주세요.")
    if len(files) > MAX_RECEIPT_MODAL_FILES:
        raise ValueError(f"한 번에 최대 {MAX_RECEIPT_MODAL_FILES}개까지 올릴 수 있습니다.")


def validate_receipt_image(file: FileStorage) -> tuple[str, str, int]:
    if not file or not file.filename:
        raise ValueError("파일이 없습니다.")

    filename = Path(file.filename).name.strip() or "receipt"
    ext = Path(filename).suffix.lower()
    mime = (file.mimetype or "").strip() or _guess_mime(filename)

    if ext not in ALLOWED_RECEIPT_IMAGE_EXTS:
        raise ValueError("영수증 이미지는 jpg, jpeg, png, webp, heic, heif만 업로드할 수 있습니다.")
    if (
        mime not in ALLOWED_RECEIPT_IMAGE_MIMES
        and not mime.startswith("image/")
        and mime != "application/octet-stream"
    ):
        raise ValueError("이미지 파일만 업로드할 수 있습니다.")

    size_bytes = _measure_file_size(file)
    file.stream.seek(0)
    return filename, mime, size_bytes


def _guess_date_from_text(text: str) -> str | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        y = int(match.group("y"))
        if y < 100:
            y += 2000
        m = int(match.group("m"))
        d = int(match.group("d"))
        try:
            return date(y, m, d).isoformat()
        except ValueError:
            continue
    return None


def _guess_time_from_text(text: str) -> str | None:
    match = _TIME_PATTERN.search(text)
    if not match:
        return None
    h = int(match.group("h"))
    m = int(match.group("m"))
    if h > 23 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


def _guess_amount_from_text(text: str) -> int | None:
    for pattern in _AMOUNT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group("amount").replace(",", "")
        try:
            amount = int(raw)
        except ValueError:
            continue
        if amount > 0:
            return amount
    return None


def _guess_counterparty_from_text(stem: str) -> str | None:
    cleaned = stem
    for pattern in _DATE_PATTERNS + _AMOUNT_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _TIME_PATTERN.sub(" ", cleaned)
    cleaned = _CARD_PATTERN.sub(" ", cleaned)
    cleaned = _ACCOUNT_PATTERN.sub(" ", cleaned)
    cleaned = _ITEM_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"[._-]+", " ", cleaned)
    tokens = [token.strip() for token in cleaned.split() if token.strip()]
    kept: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower in _NOISE_TOKENS:
            continue
        if token.isdigit():
            continue
        kept.append(token)
    if not kept:
        return None
    value = " ".join(kept).strip()
    return value[:80] if value else None


def _guess_payment_item(stem: str) -> str | None:
    match = _ITEM_PATTERN.search(stem)
    if not match:
        return None
    value = match.group("value").strip(" _-")
    return value[:80] if value else None


def _guess_payment_method(stem: str) -> str | None:
    card_match = _CARD_PATTERN.search(stem)
    if card_match:
        return f"카드 ****{card_match.group('digits')}"
    account_match = _ACCOUNT_PATTERN.search(stem)
    if account_match:
        return f"계좌 ****{account_match.group('digits')}"
    return None


def _build_receipt_fields(filename: str) -> dict[str, Any]:
    stem = Path(filename).stem
    occurred_on = _guess_date_from_text(stem)
    occurred_time = _guess_time_from_text(stem)
    amount_krw = _guess_amount_from_text(stem)
    counterparty = _guess_counterparty_from_text(stem)
    payment_item = _guess_payment_item(stem)
    payment_method = _guess_payment_method(stem)
    memo = None

    warnings: list[str] = []
    if amount_krw is None:
        warnings.append("결제 금액은 직접 확인이 필요합니다.")
    if not counterparty:
        warnings.append("매장 명은 직접 확인이 필요합니다.")
    if not occurred_on:
        warnings.append("날짜는 직접 확인이 필요합니다.")
    if not occurred_time:
        warnings.append("시간은 직접 확인이 필요합니다.")
    if not payment_item:
        warnings.append("결제 항목은 알 수 없는 상태일 수 있습니다.")
    if not payment_method:
        warnings.append("카드/계좌 정보는 확인 가능한 범위만 표시합니다.")

    return {
        "occurred_on": occurred_on,
        "occurred_time": occurred_time,
        "amount_krw": amount_krw,
        "counterparty": counterparty,
        "payment_item": payment_item,
        "payment_method": payment_method,
        "memo": memo,
        "usage": "unknown",
        "warnings": warnings,
    }


def build_receipt_preview(file: FileStorage, *, client_index: int) -> dict[str, Any]:
    filename, mime_type, size_bytes = validate_receipt_image(file)
    fields = _build_receipt_fields(filename)
    file.stream.seek(0)
    return {
        "client_index": client_index,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "status": "ready",
        **fields,
    }


def parse_receipt_confirm_item(raw: dict[str, Any]) -> dict[str, Any]:
    occurred_on = str(raw.get("occurred_on") or "").strip()
    occurred_time = str(raw.get("occurred_time") or "").strip()
    if not occurred_on:
        raise ValueError("날짜를 확인해 주세요.")
    if not occurred_time:
        raise ValueError("시간을 확인해 주세요.")

    try:
        occurred_at = datetime.strptime(f"{occurred_on} {occurred_time}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError("날짜 또는 시간 형식을 확인해 주세요.") from exc

    amount_raw = str(raw.get("amount_krw") or "").strip().replace(",", "")
    try:
        amount_krw = int(amount_raw)
    except ValueError as exc:
        raise ValueError("금액을 숫자로 입력해 주세요.") from exc
    if amount_krw <= 0:
        raise ValueError("금액은 0원보다 커야 합니다.")

    usage = str(raw.get("usage") or "unknown").strip()
    if usage not in ("business", "personal", "unknown"):
        raise ValueError("업무용 여부를 다시 확인해 주세요.")

    return {
        "item_id": str(raw.get("item_id") or "").strip(),
        "occurred_at": occurred_at,
        "amount_krw": amount_krw,
        "usage": usage,
        "counterparty": str(raw.get("counterparty") or "").strip() or None,
        "payment_item": str(raw.get("payment_item") or "").strip() or None,
        "payment_method": str(raw.get("payment_method") or "").strip() or None,
        "memo": str(raw.get("memo") or "").strip() or None,
    }


def _job_storage_root() -> Path:
    configured = current_app.config.get("RECEIPT_MODAL_JOB_DIR")
    if configured:
        root = Path(configured)
    else:
        evidence_dir = current_app.config.get("EVIDENCE_UPLOAD_DIR")
        if evidence_dir:
            root = Path(evidence_dir) / "_receipt_modal_jobs"
        else:
            root = Path(tempfile.gettempdir()) / "safetospend_receipt_modal_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _parse_delay_seconds() -> float:
    try:
        raw = current_app.config.get("RECEIPT_MODAL_PARSE_DELAY_MS", 0)
    except RuntimeError:
        raw = 0
    try:
        value = max(0, int(raw or 0))
    except (TypeError, ValueError):
        value = 0
    return value / 1000.0


def _cleanup_stale_jobs() -> None:
    now = time.time()
    stale_ids: list[str] = []
    with _RECEIPT_JOBS_LOCK:
        for job_id, job in list(_RECEIPT_JOBS.items()):
            if now - job.updated_at_ts <= _RECEIPT_JOB_TTL_SECONDS:
                continue
            stale_ids.append(job_id)
            _RECEIPT_JOBS.pop(job_id, None)
    for job_id in stale_ids:
        storage_dir = _job_storage_root() / job_id
        if storage_dir.exists():
            shutil.rmtree(storage_dir, ignore_errors=True)


def _set_job_item_state(job_id: str, item_id: str, **changes: Any) -> None:
    with _RECEIPT_JOBS_LOCK:
        job = _RECEIPT_JOBS.get(job_id)
        if not job:
            return
        for item in job.items:
            if item.item_id != item_id:
                continue
            for key, value in changes.items():
                setattr(item, key, value)
            job.updated_at_ts = time.time()
            break


def _set_job_status(job_id: str, status: str) -> None:
    with _RECEIPT_JOBS_LOCK:
        job = _RECEIPT_JOBS.get(job_id)
        if not job:
            return
        job.status = status
        job.updated_at_ts = time.time()


def _run_receipt_job(job_id: str, parse_delay_seconds: float) -> None:
    _set_job_status(job_id, "processing")
    with _RECEIPT_JOBS_LOCK:
        job = _RECEIPT_JOBS.get(job_id)
        items = list(job.items) if job else []

    for item in items:
        if item.status != "queued":
            continue
        _set_job_item_state(job_id, item.item_id, status="processing", error=None)
        if parse_delay_seconds:
            time.sleep(parse_delay_seconds)
        try:
            fields = _build_receipt_fields(item.filename)
            _set_job_item_state(
                job_id,
                item.item_id,
                status="ready",
                error=None,
                occurred_on=fields["occurred_on"],
                occurred_time=fields["occurred_time"],
                amount_krw=fields["amount_krw"],
                counterparty=fields["counterparty"],
                payment_item=fields["payment_item"],
                payment_method=fields["payment_method"],
                memo=fields["memo"],
                usage=fields["usage"],
                warnings=fields["warnings"],
            )
        except Exception:
            _set_job_item_state(
                job_id,
                item.item_id,
                status="error",
                error="파싱 중 문제가 발생했습니다.",
                warnings=[],
            )

    with _RECEIPT_JOBS_LOCK:
        job = _RECEIPT_JOBS.get(job_id)
        if not job:
            return
        job.status = "ready" if any(item.status == "ready" for item in job.items) else "failed"
        job.updated_at_ts = time.time()


def create_receipt_job(user_pk: int, files: list[FileStorage]) -> dict[str, Any]:
    validate_receipt_modal_files(files)
    _cleanup_stale_jobs()

    job_id = uuid4().hex
    storage_dir = _job_storage_root() / job_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    job = ReceiptModalJob(
        job_id=job_id,
        user_pk=user_pk,
        created_at_ts=now,
        updated_at_ts=now,
        storage_dir=str(storage_dir),
    )

    valid_count = 0
    for index, file in enumerate(files):
        try:
            filename, mime_type, size_bytes = validate_receipt_image(file)
            ext = Path(filename).suffix.lower() or ".img"
            item_id = uuid4().hex
            stored_path = storage_dir / f"{index:03d}-{item_id}{ext}"
            file.stream.seek(0)
            file.save(stored_path)
            file.stream.seek(0)
            job.items.append(
                ReceiptModalJobItem(
                    item_id=item_id,
                    client_index=index,
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    stored_path=str(stored_path),
                )
            )
            valid_count += 1
        except ValueError as exc:
            job.items.append(
                ReceiptModalJobItem(
                    item_id=uuid4().hex,
                    client_index=index,
                    filename=Path(file.filename or f"receipt-{index + 1}").name,
                    mime_type=(file.mimetype or "").strip() or _guess_mime(file.filename),
                    size_bytes=0,
                    stored_path=None,
                    status="error",
                    error=str(exc),
                )
            )

    with _RECEIPT_JOBS_LOCK:
        _RECEIPT_JOBS[job_id] = job

    if valid_count:
        parse_delay_seconds = _parse_delay_seconds()
        thread = threading.Thread(
            target=_run_receipt_job,
            args=(job_id, parse_delay_seconds),
            daemon=True,
            name=f"receipt-modal-{job_id[:8]}",
        )
        thread.start()
    else:
        _set_job_status(job_id, "failed")

    return job.snapshot()


def get_receipt_job_snapshot(user_pk: int, job_id: str) -> dict[str, Any]:
    _cleanup_stale_jobs()
    with _RECEIPT_JOBS_LOCK:
        job = _RECEIPT_JOBS.get(job_id)
        if not job or job.user_pk != user_pk:
            raise KeyError(job_id)
        job.updated_at_ts = time.time()
        return job.snapshot()


def get_receipt_job(user_pk: int, job_id: str) -> ReceiptModalJob:
    _cleanup_stale_jobs()
    with _RECEIPT_JOBS_LOCK:
        job = _RECEIPT_JOBS.get(job_id)
        if not job or job.user_pk != user_pk:
            raise KeyError(job_id)
        job.updated_at_ts = time.time()
        return job


def find_receipt_job_item(job: ReceiptModalJob, item_id: str) -> ReceiptModalJobItem | None:
    for item in job.items:
        if item.item_id == item_id:
            return item
    return None


def mark_receipt_job_result(job: ReceiptModalJob, result: dict[str, Any]) -> None:
    stored_result = deepcopy({key: value for key, value in result.items() if key != "job"})
    with _RECEIPT_JOBS_LOCK:
        job.last_result = stored_result
        job.created_count = int(stored_result.get("created_count") or 0)
        job.failed_count = int(stored_result.get("failed_count") or 0)
        job.status = "created" if job.failed_count == 0 else "created_partial"
        job.updated_at_ts = time.time()


def mark_receipt_job_item_created(job: ReceiptModalJob, item_id: str, transaction_id: int) -> None:
    with _RECEIPT_JOBS_LOCK:
        for item in job.items:
            if item.item_id != item_id:
                continue
            item.status = "created"
            item.created_transaction_id = transaction_id
            job.updated_at_ts = time.time()
            return


def open_receipt_job_file(item: ReceiptModalJobItem) -> FileStorage:
    if not item.stored_path:
        raise ValueError("파일이 없어 다시 업로드가 필요합니다.")
    path = Path(item.stored_path)
    if not path.exists():
        raise ValueError("임시 파일을 찾을 수 없어 다시 업로드해 주세요.")
    stream = path.open("rb")
    return FileStorage(stream=stream, filename=item.filename, name="file", content_type=item.mime_type)

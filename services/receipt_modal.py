from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
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
SUPPORTED_OPENAI_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
RECEIPT_OPENAI_TIMEOUT_SECONDS = 90
RECEIPT_OPENAI_MAX_IMAGE_DIMENSION = 1800
_RECEIPT_JOB_TTL_SECONDS = 6 * 60 * 60
_DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})[./-]?(?P<m>\d{2})[./-]?(?P<d>\d{2})"),
    re.compile(r"(?P<y>\d{2})[./-]?(?P<m>\d{2})[./-]?(?P<d>\d{2})"),
]
_TIME_PATTERN = re.compile(r"(?<!\d)(?P<h>\d{1,2})[:시]?(?P<m>\d{2})(?!\d)")
_UNKNOWN_TOKENS = {"", "unknown", "알수없음", "알 수 없음", "없음", "null", "none", "n/a", "미상"}
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
        "counterparty": _normalize_optional_text(raw.get("counterparty"), max_length=80),
        "payment_item": _normalize_optional_text(raw.get("payment_item"), max_length=120),
        "payment_method": _normalize_payment_method(raw.get("payment_method")),
        "memo": _normalize_optional_text(raw.get("memo"), max_length=200),
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


def _openai_model() -> str:
    return (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()


def _openai_api_key() -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY 환경변수가 없어 영수증 파싱을 시작할 수 없습니다.")
    return api_key


def _openai_endpoint() -> str:
    return (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/") + "/responses"


def _normalize_optional_text(value: Any, *, max_length: int = 120) -> str | None:
    text = str(value or "").strip()
    if text.lower() in _UNKNOWN_TOKENS or text in _UNKNOWN_TOKENS:
        return None
    return text[:max_length] if text else None


def _normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.lower() in _UNKNOWN_TOKENS or text in _UNKNOWN_TOKENS:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        year = int(match.group("y"))
        if year < 100:
            year += 2000
        month = int(match.group("m"))
        day = int(match.group("d"))
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_time(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.lower() in _UNKNOWN_TOKENS or text in _UNKNOWN_TOKENS:
        return None
    match = _TIME_PATTERN.search(text)
    if not match:
        return None
    hour = int(match.group("h"))
    minute = int(match.group("m"))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _normalize_amount(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = int(value)
        return amount if amount > 0 else None
    text = str(value).strip()
    if text.lower() in _UNKNOWN_TOKENS or text in _UNKNOWN_TOKENS:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    amount = int(digits)
    return amount if amount > 0 else None


def _normalize_payment_method(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.lower() in _UNKNOWN_TOKENS or text in _UNKNOWN_TOKENS:
        return None
    digits = re.sub(r"\D", "", text)
    suffix = digits[-4:] if len(digits) >= 4 else digits
    lower = text.lower()
    if any(token in lower for token in ("card", "카드")):
        return f"카드 ****{suffix}" if suffix else "카드"
    if any(token in lower for token in ("account", "acct", "계좌")):
        return f"계좌 ****{suffix}" if suffix else "계좌"
    if suffix:
        return f"****{suffix}"
    return text[:80] if text else None


def _default_warnings_from_fields(fields: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if fields.get("amount_krw") is None:
        warnings.append("결제 금액은 직접 확인이 필요합니다.")
    if not fields.get("counterparty"):
        warnings.append("매장 명은 직접 확인이 필요합니다.")
    if not fields.get("occurred_on"):
        warnings.append("날짜는 직접 확인이 필요합니다.")
    if not fields.get("occurred_time"):
        warnings.append("시간은 직접 확인이 필요합니다.")
    if not fields.get("payment_item"):
        warnings.append("결제 항목은 알 수 없는 상태일 수 있습니다.")
    if not fields.get("payment_method"):
        warnings.append("카드/계좌 정보는 확인 가능한 범위만 표시합니다.")
    return warnings


def _extract_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts: list[str] = []
    for output in payload.get("output", []) or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
                continue
            if isinstance(text, dict):
                value = text.get("value")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                    continue
            value = content.get("value")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    if parts:
        return "\n".join(parts)
    raise ValueError("OpenAI 응답에서 텍스트를 찾지 못했습니다.")


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(text[start : end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("OpenAI 응답이 JSON 형식이 아닙니다.")


def _prepare_image_data_url(item: ReceiptModalJobItem) -> str:
    path = Path(item.stored_path or "")
    if not path.exists():
        raise ValueError("임시 이미지 파일을 찾지 못했습니다.")

    ext = path.suffix.lower()
    mime = item.mime_type or _guess_mime(item.filename)
    raw = path.read_bytes()

    if ext in {".heic", ".heif"} or mime in {"image/heic", "image/heif"}:
        from pillow_heif import register_heif_opener
        from PIL import Image, ImageOps

        register_heif_opener()
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((RECEIPT_OPENAI_MAX_IMAGE_DIMENSION, RECEIPT_OPENAI_MAX_IMAGE_DIMENSION))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90, optimize=True)
            raw = buffer.getvalue()
            mime = "image/jpeg"
    elif mime not in SUPPORTED_OPENAI_IMAGE_MIMES:
        from PIL import Image, ImageOps

        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((RECEIPT_OPENAI_MAX_IMAGE_DIMENSION, RECEIPT_OPENAI_MAX_IMAGE_DIMENSION))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90, optimize=True)
            raw = buffer.getvalue()
            mime = "image/jpeg"

    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _call_openai_receipt_parser(item: ReceiptModalJobItem) -> dict[str, Any]:
    api_key = _openai_api_key()
    model = _openai_model()
    image_data_url = _prepare_image_data_url(item)
    prompt = (
        "당신은 한국 영수증 이미지를 읽어 구조화된 값을 추출하는 도우미다. "
        "보이는 값만 추출하고, 확실하지 않으면 null로 둬라. JSON만 반환해라.\n"
        "필드:\n"
        "- counterparty: 매장 명 또는 상호\n"
        "- occurred_on: YYYY-MM-DD 형식 날짜\n"
        "- occurred_time: HH:MM 형식 시간\n"
        "- amount_krw: 최종 결제 금액 정수\n"
        "- payment_item: 결제 항목 또는 품목\n"
        "- payment_method: 카드 또는 계좌 정보. 전체 번호를 쓰지 말고 보이는 범위만 부분 마스킹해서 예: 카드 ****1234, 계좌 ****5678\n"
        "- memo: 짧은 메모. 불필요하면 null\n"
        "- warnings: 불확실하거나 직접 확인이 필요한 점을 한국어 문자열 배열로 반환\n"
        f"참고 파일명: {item.filename}"
    )
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_data_url},
                ],
            }
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 500,
    }
    response = requests.post(
        _openai_endpoint(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=RECEIPT_OPENAI_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = None
        error_message = None
        if isinstance(error_payload, dict):
            error_message = ((error_payload.get("error") or {}).get("message")) if isinstance(error_payload.get("error"), dict) else None
        raise ValueError(error_message or f"OpenAI 응답 오류({response.status_code})")

    result_payload = response.json()
    text = _extract_output_text(result_payload)
    parsed = _json_from_text(text)
    fields = {
        "occurred_on": _normalize_date(parsed.get("occurred_on")),
        "occurred_time": _normalize_time(parsed.get("occurred_time")),
        "amount_krw": _normalize_amount(parsed.get("amount_krw")),
        "counterparty": _normalize_optional_text(parsed.get("counterparty"), max_length=80),
        "payment_item": _normalize_optional_text(parsed.get("payment_item"), max_length=120),
        "payment_method": _normalize_payment_method(parsed.get("payment_method")),
        "memo": _normalize_optional_text(parsed.get("memo"), max_length=200),
        "usage": "unknown",
    }
    warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
    normalized_warnings = []
    for warning in warnings:
        text_value = _normalize_optional_text(warning, max_length=200)
        if text_value:
            normalized_warnings.append(text_value)
    for warning in _default_warnings_from_fields(fields):
        if warning not in normalized_warnings:
            normalized_warnings.append(warning)
    fields["warnings"] = normalized_warnings
    return fields


def _parse_receipt_file_with_openai(item: ReceiptModalJobItem) -> dict[str, Any]:
    return _call_openai_receipt_parser(item)


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
            fields = _parse_receipt_file_with_openai(item)
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
                usage=fields.get("usage", "unknown"),
                warnings=fields.get("warnings", []),
            )
        except Exception as exc:
            _set_job_item_state(
                job_id,
                item.item_id,
                status="error",
                error=str(exc) or "파싱 중 문제가 발생했습니다.",
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

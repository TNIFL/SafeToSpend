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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from flask import current_app
from PIL import UnidentifiedImageError
from sqlalchemy import and_, or_
from werkzeug.datastructures import FileStorage

from core.extensions import db
from core.time import utcnow
from domain.models import ReceiptModalJobItemRecord, ReceiptModalJobRecord

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
_EMBEDDED_WORKER_LOCK = threading.Lock()
_EMBEDDED_WORKER_THREAD: threading.Thread | None = None


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


def _normalize_warning_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        text = _normalize_optional_text(value, max_length=200)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


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


def parse_receipt_draft_update(raw: dict[str, Any]) -> dict[str, Any]:
    usage = str(raw.get("usage") or "unknown").strip()
    if usage not in ("business", "personal", "unknown"):
        usage = "unknown"

    occurred_on_raw = str(raw.get("occurred_on") or "").strip()
    occurred_time_raw = str(raw.get("occurred_time") or "").strip()
    amount_raw = str(raw.get("amount_krw") or "").strip()

    normalized = {
        "occurred_on": _normalize_date(occurred_on_raw) if occurred_on_raw else None,
        "occurred_time": _normalize_time(occurred_time_raw) if occurred_time_raw else None,
        "amount_krw": _normalize_amount(amount_raw) if amount_raw else None,
        "counterparty": _normalize_optional_text(raw.get("counterparty"), max_length=80),
        "payment_item": _normalize_optional_text(raw.get("payment_item"), max_length=120),
        "payment_method": _normalize_payment_method(raw.get("payment_method")),
        "memo": _normalize_optional_text(raw.get("memo"), max_length=200),
        "usage": usage,
    }
    return normalized


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


def _openai_model() -> str:
    return (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()


def _openai_api_key() -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY 환경변수가 없어 영수증 파싱을 시작할 수 없습니다.")
    return api_key


def _openai_endpoint() -> str:
    return (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/") + "/responses"


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
    try:
        response = requests.post(
            _openai_endpoint(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=RECEIPT_OPENAI_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise ValueError("OpenAI 요청 시간이 초과되었습니다.") from exc
    except requests.ConnectionError as exc:
        raise ValueError("OpenAI 서버에 연결하지 못했습니다.") from exc
    except requests.RequestException as exc:
        raise ValueError("OpenAI 요청 전송 중 문제가 발생했습니다.") from exc
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = None
        error_message = None
        if isinstance(error_payload, dict):
            error_message = ((error_payload.get("error") or {}).get("message")) if isinstance(error_payload.get("error"), dict) else None
        if response.status_code == 400:
            raise ValueError(error_message or "OpenAI가 이미지를 해석하지 못했거나 요청 형식이 올바르지 않습니다.")
        if response.status_code == 401:
            raise ValueError(error_message or "OpenAI API 인증에 실패했습니다.")
        if response.status_code == 403:
            raise ValueError(error_message or "현재 모델 접근 권한이 없습니다.")
        if response.status_code == 404:
            raise ValueError(error_message or "설정된 OpenAI 모델을 찾지 못했습니다.")
        if response.status_code == 413:
            raise ValueError(error_message or "업로드한 이미지가 너무 커서 OpenAI가 처리하지 못했습니다.")
        if response.status_code == 429:
            raise ValueError(error_message or "OpenAI 요청 한도를 초과했습니다.")
        if response.status_code >= 500:
            raise ValueError(error_message or "OpenAI 응답 오류(서버 측 문제)")
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
    normalized_warnings = _normalize_warning_list(parsed.get("warnings"))
    for warning in _default_warnings_from_fields(fields):
        if warning not in normalized_warnings:
            normalized_warnings.append(warning)
    fields["warnings"] = normalized_warnings
    return fields


def _parse_receipt_file_with_openai(item: ReceiptModalJobItem) -> dict[str, Any]:
    return _call_openai_receipt_parser(item)


def _classify_receipt_parse_failure(exc: Exception, item: ReceiptModalJobItemRecord) -> tuple[str, list[str]]:
    message = str(exc or "").strip()
    lower = message.lower()
    filename = item.original_filename

    if "openai_api_key" in lower or "api 인증" in lower or "invalid_api_key" in lower or "authentication" in lower:
        return (
            "OpenAI 인증 설정을 확인해 주세요.",
            [
                "현재 서버에서 OPENAI_API_KEY를 사용해 영수증 파싱을 호출하지 못했습니다.",
                "환경변수 값이 비어 있거나 잘못되었을 가능성이 큽니다.",
                "관리자에게 API 키와 모델 설정을 다시 확인해 달라고 요청해 주세요.",
            ],
        )
    if "모델을 찾지 못했습니다" in message or "model" in lower and "not found" in lower:
        return (
            "영수증 파싱 모델 설정이 올바르지 않습니다.",
            [
                "현재 서버가 설정한 OPENAI_MODEL 값을 사용할 수 없습니다.",
                "모델 이름 오타나 접근 권한 문제일 수 있습니다.",
                "관리자에게 모델 설정을 다시 확인해 달라고 요청해 주세요.",
            ],
        )
    if "권한" in message or "permission" in lower or "forbidden" in lower:
        return (
            "현재 계정으로는 파싱 모델을 사용할 수 없습니다.",
            [
                "OpenAI 프로젝트 또는 조직 권한 때문에 요청이 거부되었습니다.",
                "관리자에게 모델 권한과 프로젝트 설정을 확인해 달라고 요청해 주세요.",
            ],
        )
    if "요청 한도" in message or "rate limit" in lower or "quota" in lower or "429" in lower:
        return (
            "지금은 파싱 요청이 많아 처리가 지연되고 있습니다.",
            [
                "OpenAI 요청 한도 또는 분당 처리량 제한에 걸렸습니다.",
                "잠시 후 다시 시도하면 정상 처리될 수 있습니다.",
                "대량 업로드는 나눠서 올리면 더 안정적입니다.",
            ],
        )
    if "시간이 초과" in message or "timeout" in lower or "timed out" in lower:
        return (
            "영수증 확인 시간이 초과되었습니다.",
            [
                "이미지 수가 많거나 파일이 커서 파싱 응답이 늦어졌습니다.",
                "같은 파일을 다시 시도하거나 업로드 묶음을 줄여 보세요.",
            ],
        )
    if "연결하지 못했습니다" in message or "connection" in lower:
        return (
            "파싱 서버와 통신하지 못했습니다.",
            [
                "일시적인 네트워크 문제이거나 OpenAI 연결이 불안정한 상태입니다.",
                "잠시 후 다시 시도해 주세요.",
            ],
        )
    if "응답에서 텍스트" in message or "json 형식" in message:
        return (
            "파싱 응답 형식을 읽지 못했습니다.",
            [
                "영수증 인식 결과는 왔지만 서버가 기대한 형식으로 정리되지 않았습니다.",
                "다시 시도하거나 다른 각도의 이미지로 업로드해 주세요.",
            ],
        )
    if "임시 이미지 파일" in message or "임시 파일" in message or "파일이 없어" in message:
        return (
            "업로드한 영수증 원본 파일을 찾지 못했습니다.",
            [
                "서버 임시 저장소에서 파일을 읽지 못해 파싱을 계속할 수 없었습니다.",
                "같은 영수증을 다시 업로드해 주세요.",
            ],
        )
    if "이미지 파일만" in message or "jpg, jpeg, png, webp, heic, heif" in message:
        return (
            "지원하지 않는 파일 형식입니다.",
            [
                f"{filename} 파일은 현재 지원하는 영수증 이미지 형식으로 처리되지 않았습니다.",
                "jpg, jpeg, png, webp, heic, heif 형식으로 다시 업로드해 주세요.",
            ],
        )
    if isinstance(exc, UnidentifiedImageError) or "cannot identify image file" in lower or "identify image" in lower:
        return (
            "이미지 파일을 열 수 없습니다.",
            [
                f"{filename} 파일이 손상되었거나 이미지 형식이 올바르지 않을 수 있습니다.",
                "다른 뷰어에서 열리는지 먼저 확인한 뒤 다시 업로드해 주세요.",
            ],
        )
    if "heic" in lower or "heif" in lower or "pillow_heif" in lower:
        return (
            "HEIC 이미지를 변환하지 못했습니다.",
            [
                f"{filename} 파일을 JPEG로 변환하는 중 문제가 발생했습니다.",
                "같은 이미지를 JPG 또는 PNG로 변환해서 다시 올리면 더 안정적입니다.",
            ],
        )
    if "too large" in lower or "너무 커서" in message:
        return (
            "이미지 크기가 너무 커서 처리하지 못했습니다.",
            [
                f"{filename} 파일 크기 또는 해상도가 너무 커서 파싱이 중단되었습니다.",
                "이미지를 줄이거나 여러 묶음으로 나눠 업로드해 주세요.",
            ],
        )
    return (
        "영수증을 해석하는 중 문제가 발생했습니다.",
        [
            f"{filename} 파일에서 읽을 수 있는 정보를 충분히 찾지 못했거나 응답 처리 중 오류가 발생했습니다.",
            "사진이 흐리거나 일부가 잘린 경우 다시 촬영한 뒤 업로드해 주세요.",
            "같은 문제가 반복되면 다른 형식(JPG/PNG)으로 다시 올려 보세요.",
        ],
    )


def _item_record_to_parser_item(item: ReceiptModalJobItemRecord) -> ReceiptModalJobItem:
    return ReceiptModalJobItem(
        item_id=item.id,
        client_index=item.client_index,
        filename=item.original_filename,
        mime_type=item.mime_type,
        size_bytes=item.size_bytes,
        stored_path=item.stored_path,
        status=item.status,
        error=item.error,
        occurred_on=item.occurred_on,
        occurred_time=item.occurred_time,
        amount_krw=item.amount_krw,
        counterparty=item.counterparty,
        payment_item=item.payment_item,
        payment_method=item.payment_method,
        memo=item.memo,
        usage=item.usage,
        warnings=list(item.warnings_json or []),
        created_transaction_id=item.created_transaction_id,
    )


def _item_snapshot(item: ReceiptModalJobItemRecord) -> dict[str, Any]:
    return {
        "item_id": item.id,
        "client_index": item.client_index,
        "filename": item.original_filename,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "status": item.status,
        "error": item.error,
        "occurred_on": item.occurred_on,
        "occurred_time": item.occurred_time,
        "amount_krw": item.amount_krw,
        "counterparty": item.counterparty,
        "payment_item": item.payment_item,
        "payment_method": item.payment_method,
        "memo": item.memo,
        "usage": item.usage,
        "warnings": list(item.warnings_json or []),
        "created_transaction_id": item.created_transaction_id,
    }


def _job_snapshot(job: ReceiptModalJobRecord) -> dict[str, Any]:
    items = (
        ReceiptModalJobItemRecord.query.filter_by(job_id=job.id)
        .order_by(ReceiptModalJobItemRecord.client_index.asc())
        .all()
    )
    processing_count = sum(1 for item in items if item.status in {"queued", "processing"})
    ready_count = sum(1 for item in items if item.status == "ready")
    error_count = sum(1 for item in items if item.status == "error")
    created_count = sum(1 for item in items if item.status == "created")
    return {
        "job_id": job.id,
        "status": job.status,
        "items": [_item_snapshot(item) for item in items],
        "ready_count": ready_count,
        "error_count": error_count,
        "processing_count": processing_count,
        "created_count": created_count,
        "is_complete": processing_count == 0,
        "created_at": int(job.created_at.timestamp()) if job.created_at else 0,
        "updated_at": int(job.updated_at.timestamp()) if job.updated_at else 0,
        "last_result": deepcopy(job.last_result_json) if job.last_result_json else None,
    }


def _job_history_summary(job: ReceiptModalJobRecord) -> dict[str, Any]:
    snapshot = _job_snapshot(job)
    first_item = snapshot["items"][0] if snapshot["items"] else None
    return {
        "job_id": job.id,
        "status": job.status,
        "is_complete": snapshot["is_complete"],
        "item_count": len(snapshot["items"]),
        "ready_count": snapshot["ready_count"],
        "error_count": snapshot["error_count"],
        "processing_count": snapshot["processing_count"],
        "created_count": snapshot["created_count"],
        "created_at": snapshot["created_at"],
        "updated_at": snapshot["updated_at"],
        "first_filename": first_item["filename"] if first_item else None,
        "has_result": bool(snapshot["last_result"]),
        "last_result": snapshot["last_result"],
    }


def create_receipt_job(user_pk: int, files: list[FileStorage]) -> dict[str, Any]:
    validate_receipt_modal_files(files)

    job_id = uuid4().hex
    storage_dir = _job_storage_root() / job_id
    storage_dir.mkdir(parents=True, exist_ok=True)

    job = ReceiptModalJobRecord(
        id=job_id,
        user_pk=user_pk,
        status="queued",
        storage_dir=str(storage_dir),
    )
    db.session.add(job)
    db.session.flush()

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
            db.session.add(
                ReceiptModalJobItemRecord(
                    id=item_id,
                    job_id=job.id,
                    user_pk=user_pk,
                    client_index=index,
                    original_filename=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    stored_path=str(stored_path),
                    status="queued",
                    usage="unknown",
                    warnings_json=[],
                )
            )
            valid_count += 1
        except ValueError as exc:
            db.session.add(
                ReceiptModalJobItemRecord(
                    id=uuid4().hex,
                    job_id=job.id,
                    user_pk=user_pk,
                    client_index=index,
                    original_filename=Path(file.filename or f"receipt-{index + 1}").name,
                    mime_type=(file.mimetype or "").strip() or _guess_mime(file.filename),
                    size_bytes=0,
                    stored_path=None,
                    status="error",
                    error=str(exc),
                    usage="unknown",
                    warnings_json=[],
                )
            )

    if valid_count == 0:
        job.status = "failed"
        job.failed_count = len(files)

    db.session.commit()
    return _job_snapshot(job)


def list_recent_receipt_jobs(user_pk: int, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = (
        ReceiptModalJobRecord.query.filter_by(user_pk=user_pk)
        .order_by(ReceiptModalJobRecord.updated_at.desc(), ReceiptModalJobRecord.created_at.desc())
        .limit(max(1, int(limit or 8)))
        .all()
    )
    return [_job_history_summary(row) for row in rows]


def get_receipt_job(user_pk: int, job_id: str) -> ReceiptModalJobRecord:
    job = ReceiptModalJobRecord.query.filter_by(id=job_id, user_pk=user_pk).first()
    if not job:
        raise KeyError(job_id)
    return job


def get_receipt_job_snapshot(user_pk: int, job_id: str) -> dict[str, Any]:
    return _job_snapshot(get_receipt_job(user_pk, job_id))


def find_receipt_job_item(job: ReceiptModalJobRecord, item_id: str) -> ReceiptModalJobItemRecord | None:
    return ReceiptModalJobItemRecord.query.filter_by(job_id=job.id, id=item_id).first()


def update_receipt_job_item_draft(user_pk: int, job_id: str, item_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    job = get_receipt_job(user_pk, job_id)
    item = find_receipt_job_item(job, item_id)
    if item is None:
        raise KeyError(item_id)
    if item.status not in {"ready", "created"}:
        raise ValueError("파싱이 끝난 영수증만 수정할 수 있습니다.")

    normalized = parse_receipt_draft_update(raw)
    item.occurred_on = normalized["occurred_on"]
    item.occurred_time = normalized["occurred_time"]
    item.amount_krw = normalized["amount_krw"]
    item.counterparty = normalized["counterparty"]
    item.payment_item = normalized["payment_item"]
    item.payment_method = normalized["payment_method"]
    item.memo = normalized["memo"]
    item.usage = normalized["usage"]
    item.updated_at = utcnow()
    job.updated_at = utcnow()
    db.session.commit()
    return _item_snapshot(item)


def mark_receipt_job_result(job: ReceiptModalJobRecord, result: dict[str, Any]) -> None:
    stored_result = deepcopy({key: value for key, value in result.items() if key != "job"})
    job.last_result_json = stored_result
    job.created_count = int(stored_result.get("created_count") or 0)
    job.failed_count = int(stored_result.get("failed_count") or 0)
    job.status = "created" if job.failed_count == 0 else "created_partial"
    job.updated_at = utcnow()
    db.session.commit()


def mark_receipt_job_item_created(job: ReceiptModalJobRecord, item_id: str, transaction_id: int) -> None:
    item = find_receipt_job_item(job, item_id)
    if item is None:
        return
    item.status = "created"
    item.created_transaction_id = transaction_id
    item.updated_at = utcnow()
    job.updated_at = utcnow()
    db.session.commit()


def open_receipt_job_file(item: ReceiptModalJobItemRecord | ReceiptModalJobItem) -> FileStorage:
    stored_path = getattr(item, "stored_path", None)
    filename = getattr(item, "original_filename", None) or getattr(item, "filename", None)
    mime_type = getattr(item, "mime_type", None)
    if not stored_path:
        raise ValueError("파일이 없어 다시 업로드가 필요합니다.")
    path = Path(str(stored_path))
    if not path.exists():
        raise ValueError("임시 파일을 찾을 수 없어 다시 업로드해 주세요.")
    stream = path.open("rb")
    return FileStorage(stream=stream, filename=filename, name="file", content_type=mime_type)


def _refresh_job_counters(job: ReceiptModalJobRecord) -> None:
    items = ReceiptModalJobItemRecord.query.filter_by(job_id=job.id).all()
    pending_count = sum(1 for item in items if item.status in {"queued", "processing"})
    ready_or_created_count = sum(1 for item in items if item.status in {"ready", "created"})
    error_count = sum(1 for item in items if item.status == "error")
    created_count = sum(1 for item in items if item.status == "created")

    job.created_count = created_count
    job.failed_count = error_count
    if pending_count > 0:
        job.status = "processing"
    elif ready_or_created_count > 0:
        if job.last_result_json:
            job.status = "created" if job.failed_count == 0 else "created_partial"
        else:
            job.status = "ready"
    else:
        job.status = "failed"
    job.updated_at = utcnow()


def _worker_stale_before() -> datetime:
    seconds = int(current_app.config.get("RECEIPT_MODAL_WORKER_STALE_SECONDS", 300) or 300)
    return datetime.now() - timedelta(seconds=max(60, seconds))


def _claim_next_job(worker_id: str) -> str | None:
    stale_before = _worker_stale_before()
    query = (
        ReceiptModalJobRecord.query.filter(
            or_(
                ReceiptModalJobRecord.status == "queued",
                and_(
                    ReceiptModalJobRecord.status == "processing",
                    or_(
                        ReceiptModalJobRecord.worker_heartbeat_at.is_(None),
                        ReceiptModalJobRecord.worker_heartbeat_at < stale_before,
                    ),
                ),
            )
        )
        .order_by(ReceiptModalJobRecord.created_at.asc())
        .with_for_update(skip_locked=True)
    )
    job = query.first()
    if not job:
        db.session.rollback()
        return None

    job.status = "processing"
    job.worker_id = worker_id
    job.worker_claimed_at = utcnow()
    job.worker_heartbeat_at = utcnow()
    job.parse_attempts = int(job.parse_attempts or 0) + 1
    job.updated_at = utcnow()
    db.session.commit()
    return job.id


def _process_claimed_job(job_id: str, worker_id: str) -> None:
    job = ReceiptModalJobRecord.query.filter_by(id=job_id).first()
    if not job:
        return

    items = (
        ReceiptModalJobItemRecord.query.filter_by(job_id=job.id)
        .order_by(ReceiptModalJobItemRecord.client_index.asc())
        .all()
    )
    parse_delay_seconds = _parse_delay_seconds()

    for item in items:
        if item.status not in {"queued", "processing"}:
            continue
        item.status = "processing"
        item.error = None
        job.worker_heartbeat_at = utcnow()
        job.updated_at = utcnow()
        db.session.commit()

        if parse_delay_seconds:
            time.sleep(parse_delay_seconds)

        try:
            fields = _parse_receipt_file_with_openai(_item_record_to_parser_item(item))
            item.status = "ready"
            item.error = None
            item.occurred_on = fields.get("occurred_on")
            item.occurred_time = fields.get("occurred_time")
            item.amount_krw = fields.get("amount_krw")
            item.counterparty = fields.get("counterparty")
            item.payment_item = fields.get("payment_item")
            item.payment_method = fields.get("payment_method")
            item.memo = fields.get("memo")
            item.usage = fields.get("usage") or "unknown"
            item.warnings_json = list(fields.get("warnings") or [])
            item.updated_at = utcnow()
        except Exception as exc:
            error_title, error_details = _classify_receipt_parse_failure(exc, item)
            item.status = "error"
            item.error = error_title
            item.warnings_json = error_details
            item.updated_at = utcnow()
        finally:
            job.worker_heartbeat_at = utcnow()
            _refresh_job_counters(job)
            db.session.commit()


def process_receipt_queue_once(worker_id: str) -> bool:
    job_id = _claim_next_job(worker_id)
    if not job_id:
        return False
    try:
        _process_claimed_job(job_id, worker_id)
    finally:
        db.session.remove()
    return True


def _embedded_worker_main(app) -> None:
    worker_id = f"embedded-{os.getpid()}-{uuid4().hex[:8]}"
    idle_cycles = 0
    try:
        while idle_cycles < 3:
            with app.app_context():
                did_work = process_receipt_queue_once(worker_id)
            if did_work:
                idle_cycles = 0
                continue
            idle_cycles += 1
            time.sleep(float(app.config.get("RECEIPT_MODAL_WORKER_IDLE_SECONDS", 1.0) or 1.0))
    finally:
        global _EMBEDDED_WORKER_THREAD
        with _EMBEDDED_WORKER_LOCK:
            _EMBEDDED_WORKER_THREAD = None


def kick_receipt_worker(app) -> None:
    if not app.config.get("RECEIPT_MODAL_ENABLE_EMBEDDED_WORKER", True):
        return
    global _EMBEDDED_WORKER_THREAD
    with _EMBEDDED_WORKER_LOCK:
        if _EMBEDDED_WORKER_THREAD and _EMBEDDED_WORKER_THREAD.is_alive():
            return
        _EMBEDDED_WORKER_THREAD = threading.Thread(
            target=_embedded_worker_main,
            args=(app,),
            daemon=True,
            name="receipt-modal-embedded-worker",
        )
        _EMBEDDED_WORKER_THREAD.start()


def run_receipt_worker(app, *, once: bool = False, limit: int = 100, idle_seconds: float = 1.0) -> int:
    processed = 0
    worker_id = f"cli-{os.getpid()}-{uuid4().hex[:8]}"
    while True:
        with app.app_context():
            did_work = process_receipt_queue_once(worker_id)
        if did_work:
            processed += 1
            if once or processed >= max(1, int(limit or 1)):
                return processed
            continue
        if once:
            return processed
        time.sleep(max(0.2, float(idle_seconds or 1.0)))

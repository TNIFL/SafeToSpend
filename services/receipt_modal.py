from __future__ import annotations

import mimetypes
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from werkzeug.datastructures import FileStorage
MAX_RECEIPT_MODAL_FILES = 50
ALLOWED_RECEIPT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_RECEIPT_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}

_DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})[._-]?(?P<m>\d{2})[._-]?(?P<d>\d{2})"),
    re.compile(r"(?P<y>\d{2})[._-]?(?P<m>\d{2})[._-]?(?P<d>\d{2})"),
]
_TIME_PATTERN = re.compile(r"(?<!\d)(?P<h>\d{1,2})[:시]?(?P<m>\d{2})(?!\d)")
_AMOUNT_PATTERNS = [
    re.compile(r"(?P<amount>\d[\d,]{2,})\s*(?:원|krw)", re.IGNORECASE),
    re.compile(r"(?:amt|amount)[-_ ]*(?P<amount>\d[\d,]{2,})", re.IGNORECASE),
]
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
        raise ValueError("영수증 이미지는 jpg, jpeg, png, webp만 업로드할 수 있습니다.")
    if mime not in ALLOWED_RECEIPT_IMAGE_MIMES and not mime.startswith("image/"):
        raise ValueError("이미지 파일만 업로드할 수 있습니다.")

    size_bytes = _measure_file_size(file)
    file.stream.seek(0)
    return filename, mime, size_bytes


def _guess_date_from_filename(text: str) -> str | None:
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


def _guess_time_from_filename(text: str) -> str | None:
    match = _TIME_PATTERN.search(text)
    if not match:
        return None
    h = int(match.group("h"))
    m = int(match.group("m"))
    if h > 23 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


def _guess_amount_from_filename(text: str) -> int | None:
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


def _guess_counterparty_from_filename(stem: str) -> str | None:
    cleaned = stem
    for pattern in _DATE_PATTERNS + _AMOUNT_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _TIME_PATTERN.sub(" ", cleaned)
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


def build_receipt_preview(file: FileStorage, *, client_index: int) -> dict[str, Any]:
    filename, mime_type, size_bytes = validate_receipt_image(file)
    stem = Path(filename).stem
    occurred_on = _guess_date_from_filename(stem) or date.today().isoformat()
    occurred_time = _guess_time_from_filename(stem) or "12:00"
    amount_krw = _guess_amount_from_filename(stem)
    counterparty = _guess_counterparty_from_filename(stem) or ""
    memo = ""

    warnings: list[str] = []
    if amount_krw is None:
        warnings.append("금액은 파일명에서 확실히 읽지 못해 직접 확인이 필요합니다.")
    if not counterparty:
        warnings.append("가맹점/상호는 직접 확인해 주세요.")
    if occurred_on == date.today().isoformat():
        warnings.append("사용일시는 기본값이 들어갈 수 있으니 실제 영수증과 비교해 주세요.")

    file.stream.seek(0)
    return {
        "client_index": client_index,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "occurred_on": occurred_on,
        "occurred_time": occurred_time,
        "amount_krw": amount_krw,
        "counterparty": counterparty,
        "memo": memo,
        "usage": "unknown",
        "status": "ready",
        "warnings": warnings,
    }


def parse_receipt_confirm_item(raw: dict[str, Any]) -> dict[str, Any]:
    occurred_on = str(raw.get("occurred_on") or "").strip()
    occurred_time = str(raw.get("occurred_time") or "12:00").strip() or "12:00"
    if not occurred_on:
        raise ValueError("사용일자를 확인해 주세요.")

    try:
        occurred_at = datetime.strptime(f"{occurred_on} {occurred_time}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError("사용일시 형식을 확인해 주세요.") from exc

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

    counterparty = str(raw.get("counterparty") or "").strip() or None
    memo = str(raw.get("memo") or "").strip() or None

    return {
        "occurred_at": occurred_at,
        "amount_krw": amount_krw,
        "usage": usage,
        "counterparty": counterparty,
        "memo": memo,
    }

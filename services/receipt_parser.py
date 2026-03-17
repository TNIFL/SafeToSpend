# services/receipt_parser.py
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from services.input_sanitize import safe_str
from services.llm_safe import extract_receipt_json

# Optional deps (HEIC/HEIF 지원)
try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore

try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    _HAS_HEIF = True
except Exception:  # pragma: no cover
    _HAS_HEIF = False


@dataclass(frozen=True)
class ReceiptDraft:
    ok: bool
    provider: str
    parsed: dict[str, Any]
    raw_text: str | None = None
    error: str | None = None


def _data_url(abs_path: Path, mime_type: str) -> str:
    ext = abs_path.suffix.lower()
    mt = (mime_type or "").lower().strip()

    # HEIC/HEIF → JPEG 변환
    if ext in (".heic", ".heif") or mt in ("image/heic", "image/heif"):
        if Image is None or not _HAS_HEIF:
            raise ValueError("HEIC/HEIF 변환 라이브러리(pillow-heif)가 필요합니다.")
        img = Image.open(abs_path)
        rgb = img.convert("RGB")
        buf = BytesIO()
        rgb.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    b = abs_path.read_bytes()
    b64 = base64.b64encode(b).decode("utf-8")

    if ext == ".png":
        mt = "image/png"
    elif ext in (".jpg", ".jpeg"):
        mt = "image/jpeg"
    elif ext == ".gif":
        mt = "image/gif"
    elif ext == ".webp":
        mt = "image/webp"
    else:
        mt = mt or "image/jpeg"

    return f"data:{mt};base64,{b64}"


def parse_receipt_from_file(*, abs_path: Path, mime_type: str) -> ReceiptDraft:
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    ext = abs_path.suffix.lower()
    mt = (mime_type or "").lower().strip()
    is_pdf = ext == ".pdf" or mt == "application/pdf"

    if is_pdf:
        try:
            raw = abs_path.read_bytes()
        except Exception as e:
            return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error=f"PDF 파싱 실패: {e}")
        if not raw:
            return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error="PDF 파싱 실패: 파일이 비어있습니다.")
        if not raw.startswith(b"%PDF"):
            return ReceiptDraft(
                ok=False,
                provider=f"openai:{model}",
                parsed={},
                error="PDF 파싱 실패: 파일 형식을 확인할 수 없어요.",
            )
        data_b64 = base64.b64encode(raw).decode("utf-8")
        ok, parsed, err, _meta = extract_receipt_json(
            model=model,
            receipt_file_base64=data_b64,
            receipt_file_mime="application/pdf",
            receipt_file_name=(abs_path.name or "receipt.pdf"),
        )
        if not ok:
            return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error=f"PDF 파싱 실패: {err}")
        return ReceiptDraft(ok=True, provider=f"openai:{model}", parsed=parsed)

    try:
        data_url = _data_url(abs_path, mime_type)
    except Exception as e:
        return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error=str(e))

    ok, parsed, err, _meta = extract_receipt_json(
        model=model,
        receipt_image_data_url=data_url,
    )
    if not ok:
        return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error=err)
    return ReceiptDraft(ok=True, provider=f"openai:{model}", parsed=parsed)


def parse_receipt_from_text(*, text: str) -> ReceiptDraft:
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    txt = safe_str(text, max_len=20_000, allow_newline=True)
    if not txt:
        return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error="텍스트가 비어있습니다.")

    ok, parsed, err, _meta = extract_receipt_json(
        model=model,
        receipt_text=txt,
    )
    if not ok:
        return ReceiptDraft(ok=False, provider=f"openai:{model}", parsed={}, error=err)
    return ReceiptDraft(ok=True, provider=f"openai:{model}", parsed=parsed)

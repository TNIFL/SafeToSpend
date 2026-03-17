from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POPBILL_GUIDE_DOC_URL = "https://developers.popbill.com/guide/easyfinbank/introduction/regist-bank-account"
POPBILL_GUIDE_SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "data" / "reference" / "popbill_bank_guides.json"

# /routes/web/bank.py와 동일한 사용자 표시 기준을 공유한다.
POPBILL_BANK_CATALOG: tuple[tuple[str, str], ...] = (
    ("0003", "IBK기업"),
    ("0004", "KB국민"),
    ("0011", "NH농협"),
    ("0020", "우리"),
    ("0023", "SC제일"),
    ("0027", "씨티"),
    ("0031", "iM뱅크(대구)"),
    ("0032", "부산"),
    ("0034", "광주"),
    ("0035", "제주"),
    ("0037", "전북"),
    ("0039", "경남"),
    ("0081", "하나"),
    ("0088", "신한"),
    ("0090", "카카오뱅크"),
    ("0092", "토스뱅크"),
)

_CATALOG_MAP = {code: name for code, name in POPBILL_BANK_CATALOG}
_DEFAULT_STEPS = [
    "은행 앱/웹 로그인",
    "뱅킹관리 또는 편의서비스 메뉴 이동",
    "빠른조회(스피드조회/즉시조회) 등록 후 저장",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_short_text(value: Any, *, max_len: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_len:
        return text[:max_len].rstrip() + "…"
    return text


def _normalize_steps(value: Any) -> list[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            text = _as_short_text(item, max_len=80)
            if text:
                out.append(text)
            if len(out) >= 5:
                break
        return out
    return []


def _normalize_required_fields(value: Any) -> list[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            text = _as_short_text(item, max_len=60)
            if text:
                out.append(text)
            if len(out) >= 6:
                break
        return out
    return []


def _default_entry(bank_code: str, bank_name: str) -> dict[str, Any]:
    return {
        "bank_code": str(bank_code),
        "bank_name": str(bank_name),
        "quick_service_name": "빠른조회 서비스",
        "intro_message": "은행 사이트에서 빠른조회 등록 후 다시 연결해 주세요.",
        "path_steps": list(_DEFAULT_STEPS),
        "required_fields": [],
        "official_doc_url": POPBILL_GUIDE_DOC_URL,
    }


def build_default_snapshot(*, notes: list[str] | None = None) -> dict[str, Any]:
    bank_rows = [_default_entry(code, name) for code, name in POPBILL_BANK_CATALOG]
    safe_notes = list(notes or [])
    return {
        "updated_at": _now_iso(),
        "official_doc_url": POPBILL_GUIDE_DOC_URL,
        "banks": bank_rows,
        "notes": safe_notes,
        "meta": {
            "source_fetch_ok": False,
            "parsed_bank_count": 0,
            "total_bank_count": len(POPBILL_BANK_CATALOG),
            "fallback_bank_count": len(POPBILL_BANK_CATALOG),
            "structure_changed": True,
            "last_run_status": "fallback",
        },
    }


def normalize_snapshot(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return build_default_snapshot(notes=["가이드 스냅샷이 없어 기본 안내를 사용했어요."])

    raw_rows = payload.get("banks")
    out_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    if isinstance(raw_rows, list):
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            bank_code = _as_short_text(raw.get("bank_code"), max_len=8)
            if (not bank_code) or (len(bank_code) != 4) or (not bank_code.isdigit()):
                continue
            if bank_code in seen:
                continue
            seen.add(bank_code)
            bank_name = _as_short_text(raw.get("bank_name"), max_len=40) or _CATALOG_MAP.get(bank_code, f"은행({bank_code})")
            row = _default_entry(bank_code, bank_name)
            row["quick_service_name"] = _as_short_text(raw.get("quick_service_name"), max_len=40) or row["quick_service_name"]
            row["intro_message"] = _as_short_text(raw.get("intro_message"), max_len=160) or row["intro_message"]
            steps = _normalize_steps(raw.get("path_steps"))
            if steps:
                row["path_steps"] = steps
            required_fields = _normalize_required_fields(raw.get("required_fields"))
            if required_fields:
                row["required_fields"] = required_fields
            doc_url = _as_short_text(raw.get("official_doc_url"), max_len=300)
            if doc_url.startswith("http://") or doc_url.startswith("https://"):
                row["official_doc_url"] = doc_url
            out_rows.append(row)

    for code, name in POPBILL_BANK_CATALOG:
        if code in seen:
            continue
        out_rows.append(_default_entry(code, name))

    out_rows.sort(key=lambda x: (x.get("bank_name") or "", x.get("bank_code") or ""))
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    safe_notes = [_as_short_text(x, max_len=200) for x in notes if _as_short_text(x, max_len=200)]
    updated_at = _as_short_text(payload.get("updated_at"), max_len=64) or _now_iso()
    official_doc_url = _as_short_text(payload.get("official_doc_url"), max_len=300)
    if not (official_doc_url.startswith("http://") or official_doc_url.startswith("https://")):
        official_doc_url = POPBILL_GUIDE_DOC_URL

    raw_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    parsed_bank_count = 0
    try:
        parsed_bank_count = int(raw_meta.get("parsed_bank_count") or 0)
    except Exception:
        parsed_bank_count = 0
    total_bank_count = len(POPBILL_BANK_CATALOG)
    fallback_bank_count = max(0, total_bank_count - min(total_bank_count, parsed_bank_count))
    source_fetch_ok = bool(raw_meta.get("source_fetch_ok"))
    structure_changed = bool(raw_meta.get("structure_changed"))
    if parsed_bank_count <= 0:
        structure_changed = True
    last_run_status = str(raw_meta.get("last_run_status") or "").strip().lower()
    if last_run_status not in {"ok", "partial", "fallback"}:
        if parsed_bank_count <= 0:
            last_run_status = "fallback"
        elif fallback_bank_count > 0:
            last_run_status = "partial"
        else:
            last_run_status = "ok"

    return {
        "updated_at": updated_at,
        "official_doc_url": official_doc_url,
        "banks": out_rows,
        "notes": safe_notes,
        "meta": {
            "source_fetch_ok": source_fetch_ok,
            "parsed_bank_count": int(max(0, parsed_bank_count)),
            "total_bank_count": int(total_bank_count),
            "fallback_bank_count": int(max(0, fallback_bank_count)),
            "structure_changed": bool(structure_changed),
            "last_run_status": last_run_status,
        },
    }


def load_popbill_bank_guides(*, snapshot_path: Path | None = None) -> dict[str, Any]:
    path = snapshot_path or POPBILL_GUIDE_SNAPSHOT_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return build_default_snapshot(notes=["가이드 파일이 없어 기본 안내를 사용했어요."])
    except Exception:
        return build_default_snapshot(notes=["가이드 파일을 읽지 못해 기본 안내를 사용했어요."])
    return normalize_snapshot(raw if isinstance(raw, dict) else None)

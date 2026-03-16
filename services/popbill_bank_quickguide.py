from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POPBILL_QUICKGUIDE_DOC_URL = "https://developers.popbill.com/guide/easyfinbank/introduction/regist-bank-account"
POPBILL_QUICKGUIDE_PATH = Path(__file__).resolve().parents[1] / "data" / "reference" / "bank_quick_guide_ko.json"

# 팝빌 빠른조회 가이드 노출 은행(2026-03 기준)
OFFICIAL_BANKS: tuple[tuple[str, str], ...] = (
    ("0002", "산업은행"),
    ("0003", "기업은행"),
    ("0004", "국민은행"),
    ("0007", "수협은행"),
    ("0011", "농협은행"),
    ("0020", "우리은행"),
    ("0023", "SC제일은행"),
    ("0027", "씨티은행"),
    ("0031", "아이엠뱅크"),
    ("0032", "부산은행"),
    ("0034", "광주은행"),
    ("0035", "제주은행"),
    ("0037", "전북은행"),
    ("0039", "경남은행"),
    ("0045", "새마을금고"),
    ("0048", "신협중앙회"),
    ("0071", "우체국"),
    ("0081", "하나은행"),
    ("0088", "신한은행"),
)

_CATALOG = {code: name for code, name in OFFICIAL_BANKS}
_DEFAULT_STEPS = [
    "은행 홈페이지 또는 앱 접속",
    "로그인 후 조회/편의서비스 메뉴 이동",
    "빠른조회(또는 유사 서비스) 신청",
]
_DEFAULT_NOTICE = "이 은행은 먼저 빠른조회 등록이 필요해요."


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, *, max_len: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_len].strip()


def _clean_steps(value: Any) -> list[str]:
    out: list[str] = []
    raw_list = value if isinstance(value, list) else []
    for raw in raw_list:
        txt = _clean_text(raw, max_len=160)
        if not txt:
            continue
        out.append(txt)
        if len(out) >= 8:
            break
    return out


def _default_row(bank_code: str, bank_name: str) -> dict[str, Any]:
    return {
        "bank_code": bank_code,
        "bank_name": bank_name,
        "service_name": "빠른조회 서비스",
        "homepage_url": POPBILL_QUICKGUIDE_DOC_URL,
        "intro_notice": _DEFAULT_NOTICE,
        "corporate_steps": list(_DEFAULT_STEPS),
        "personal_steps": list(_DEFAULT_STEPS),
    }


def _default_payload(note: str | None = None) -> dict[str, Any]:
    notes = [note] if note else []
    return {
        "updated_at": _now_iso(),
        "official_doc_url": POPBILL_QUICKGUIDE_DOC_URL,
        "banks": [_default_row(code, name) for code, name in OFFICIAL_BANKS],
        "notes": notes,
    }


def _normalize(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _default_payload("가이드 데이터를 찾지 못해 기본 안내를 사용해요.")

    rows = payload.get("banks") if isinstance(payload.get("banks"), list) else []
    parsed: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        code = _clean_text(item.get("bank_code"), max_len=8)
        if code not in _CATALOG:
            continue

        row = _default_row(code, _CATALOG[code])
        row["bank_name"] = _clean_text(item.get("bank_name"), max_len=40) or row["bank_name"]
        row["service_name"] = _clean_text(item.get("service_name"), max_len=60) or row["service_name"]

        homepage_url = _clean_text(item.get("homepage_url"), max_len=300)
        if homepage_url.startswith("http://") or homepage_url.startswith("https://"):
            row["homepage_url"] = homepage_url

        row["intro_notice"] = _clean_text(item.get("intro_notice"), max_len=180) or row["intro_notice"]

        corp_steps = _clean_steps(item.get("corporate_steps"))
        personal_steps = _clean_steps(item.get("personal_steps"))
        if not corp_steps and personal_steps:
            corp_steps = list(personal_steps)
        if not personal_steps and corp_steps:
            personal_steps = list(corp_steps)
        if corp_steps:
            row["corporate_steps"] = corp_steps
        if personal_steps:
            row["personal_steps"] = personal_steps

        extra_note = _clean_text(item.get("extra_note"), max_len=200)
        if extra_note:
            row["extra_note"] = extra_note

        parsed[code] = row

    out = [parsed.get(code) or _default_row(code, name) for code, name in OFFICIAL_BANKS]
    updated_at = _clean_text(payload.get("updated_at"), max_len=64) or _now_iso()
    doc_url = _clean_text(payload.get("official_doc_url"), max_len=300)
    if not (doc_url.startswith("http://") or doc_url.startswith("https://")):
        doc_url = POPBILL_QUICKGUIDE_DOC_URL

    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    safe_notes = [_clean_text(x, max_len=120) for x in notes if _clean_text(x, max_len=120)]

    return {
        "updated_at": updated_at,
        "official_doc_url": doc_url,
        "banks": out,
        "notes": safe_notes,
    }


def load_popbill_bank_quickguide(*, path: Path | None = None) -> dict[str, Any]:
    file_path = path or POPBILL_QUICKGUIDE_PATH
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_payload("가이드 파일이 없어 기본 안내를 사용해요.")
    except Exception:
        return _default_payload("가이드 파일을 읽지 못해 기본 안내를 사용해요.")
    return _normalize(raw if isinstance(raw, dict) else None)

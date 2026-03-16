from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from services.popbill_bank_guides import (
    POPBILL_BANK_CATALOG,
    POPBILL_GUIDE_DOC_URL,
    POPBILL_GUIDE_SNAPSHOT_PATH,
    build_default_snapshot,
    normalize_snapshot,
)


_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_CODE_RE = re.compile(r"\b(\d{4})\b")
_STEP_SPLIT_RE = re.compile(r"\s*(?:→|->|>|/)\s*")
_CANDIDATE_FIELD_KEYS = (
    "아이디",
    "비밀번호",
    "계좌번호",
    "OTP",
    "보안카드",
    "공동인증서",
    "인증서",
    "생년월일",
    "사업자번호",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return _WS_RE.sub(" ", text).strip()


def _detect_quick_service_name(text: str) -> str:
    low = (text or "").lower()
    if "스피드조회" in text:
        return "스피드조회 서비스"
    if "즉시조회" in text:
        return "즉시조회 서비스"
    if "빠른조회" in text:
        return "빠른조회 서비스"
    if "quick" in low:
        return "빠른조회 서비스"
    return "빠른조회 서비스"


def _extract_steps(text: str) -> list[str]:
    if not text:
        return []
    step_match = re.search(r"(?:경로|메뉴)\s*[:：]\s*([^\n]{8,220})", text)
    base = step_match.group(1).strip() if step_match else ""
    if not base:
        return []
    parts = [p.strip(" .,-") for p in _STEP_SPLIT_RE.split(base) if p.strip(" .,-")]
    out = []
    for part in parts:
        if part and part not in out:
            out.append(part[:80])
        if len(out) >= 5:
            break
    return out


def _extract_required_fields(text: str) -> list[str]:
    out: list[str] = []
    for key in _CANDIDATE_FIELD_KEYS:
        if key in text and key not in out:
            out.append(key)
    return out[:6]


def _extract_bank_name(text: str, bank_code: str, fallback_name: str) -> str:
    cleaned = text.replace(bank_code, " ")
    cleaned = re.sub(r"(빠른조회|스피드조회|즉시조회|서비스|신청|등록).*", "", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip(" -|:,()")
    if cleaned and len(cleaned) <= 40:
        return cleaned
    return fallback_name


def _parse_rows_from_html(html: str, *, notes: list[str]) -> dict[str, dict[str, Any]]:
    catalog = {code: name for code, name in POPBILL_BANK_CATALOG}
    out: dict[str, dict[str, Any]] = {}
    for row_html in _ROW_RE.findall(html or ""):
        text = _strip_html(row_html)
        if not text:
            continue
        code_m = _CODE_RE.search(text)
        if not code_m:
            continue
        code = code_m.group(1)
        if code not in catalog:
            continue
        bank_name = _extract_bank_name(text, code, catalog[code])
        entry = {
            "bank_code": code,
            "bank_name": bank_name,
            "quick_service_name": _detect_quick_service_name(text),
            "intro_message": "은행 사이트에서 빠른조회 등록 후 다시 연결해 주세요.",
            "path_steps": _extract_steps(text),
            "required_fields": _extract_required_fields(text),
            "official_doc_url": POPBILL_GUIDE_DOC_URL,
        }
        out[code] = entry

    missing_codes = [code for code, _name in POPBILL_BANK_CATALOG if code not in out]
    if missing_codes:
        notes.append(
            f"일부 은행 상세 추출 누락: {len(missing_codes)}개"
        )
    return out


def run_refresh(*, timeout: int = 12, output_path: Path | None = None) -> int:
    path = output_path or POPBILL_GUIDE_SNAPSHOT_PATH
    notes: list[str] = []
    raw_rows: list[dict[str, Any]] = []
    source_fetch_ok = False
    fetch_error_name = ""
    structure_changed = False

    try:
        res = requests.get(POPBILL_GUIDE_DOC_URL, timeout=max(3, int(timeout)))
        res.raise_for_status()
        source_fetch_ok = True
        parsed = _parse_rows_from_html(res.text, notes=notes)
        raw_rows = list(parsed.values())
    except Exception as exc:
        fetch_error_name = str(type(exc).__name__)
        notes.append(f"문서 수집 실패: {fetch_error_name}")
        structure_changed = True

    if not raw_rows:
        payload = build_default_snapshot(notes=notes or ["문서 파싱에 실패해 기본 안내를 사용했어요."])
    else:
        total_count = len(POPBILL_BANK_CATALOG)
        parsed_count = len(raw_rows)
        fallback_count = max(0, total_count - parsed_count)
        if parsed_count <= 0:
            structure_changed = True
        last_run_status = "ok"
        if fallback_count > 0:
            last_run_status = "partial"
        payload = {
            "updated_at": _now_iso(),
            "official_doc_url": POPBILL_GUIDE_DOC_URL,
            "banks": raw_rows,
            "notes": notes,
            "meta": {
                "source_fetch_ok": source_fetch_ok,
                "fetch_error": fetch_error_name,
                "parsed_bank_count": parsed_count,
                "total_bank_count": total_count,
                "fallback_bank_count": fallback_count,
                "structure_changed": structure_changed,
                "last_run_status": last_run_status,
            },
        }
        payload = normalize_snapshot(payload)

    if "meta" not in payload:
        payload["meta"] = {}
    payload["meta"]["source_fetch_ok"] = bool(payload["meta"].get("source_fetch_ok", source_fetch_ok))
    payload["meta"]["fetch_error"] = str(payload["meta"].get("fetch_error") or fetch_error_name)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[refresh-popbill-bank-guides]")
    print(f"- output: {path}")
    print(f"- banks: {len(payload.get('banks') or [])}")
    print(f"- notes: {len(payload.get('notes') or [])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="팝빌 은행별 빠른조회 가이드 스냅샷 갱신")
    parser.add_argument("--timeout", type=int, default=12, help="HTTP timeout(초)")
    parser.add_argument(
        "--output",
        type=str,
        default=str(POPBILL_GUIDE_SNAPSHOT_PATH),
        help="스냅샷 저장 경로",
    )
    args = parser.parse_args()
    return run_refresh(timeout=max(3, int(args.timeout)), output_path=Path(args.output).resolve())


if __name__ == "__main__":
    raise SystemExit(main())

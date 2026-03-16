#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.official_refs.source_policy import official_domains_list

DEFAULT_CONFIG_PATH = ROOT / "data" / "reference_watch" / "targets.json"
LEGACY_CONFIG_PATH = ROOT / "data" / "reference_watchdog_targets.json"
DEFAULT_STATE_PATH = ROOT / "data" / "reference_watch" / "status.json"
LEGACY_STATE_PATH = ROOT / "data" / "reference_watchdog_state.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _normalize_text(text: str, max_chars: int = 160_000) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= max_chars:
        return s
    return s[:max_chars]


def _focus_text(raw_text: str, patterns: list[str]) -> tuple[str, int, list[str], list[str]]:
    if not patterns:
        return _normalize_text(raw_text), 0, [], []
    chunks: list[str] = []
    hits = 0
    matched_patterns: list[str] = []
    missing_patterns: list[str] = []
    for pattern in patterns:
        try:
            regex = re.compile(pattern, flags=re.IGNORECASE | re.DOTALL)
        except re.error:
            missing_patterns.append(pattern)
            continue
        match = regex.search(raw_text)
        if not match:
            missing_patterns.append(pattern)
            continue
        hits += 1
        matched_patterns.append(pattern)
        start = max(0, int(match.start()) - 220)
        end = min(len(raw_text), int(match.end()) + 220)
        chunks.append(raw_text[start:end])
    if chunks:
        return _normalize_text("\n".join(chunks)), hits, matched_patterns, missing_patterns
    return _normalize_text(raw_text), 0, matched_patterns, missing_patterns


def _keyword_match_lists(raw_text: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    text = str(raw_text or "").lower()
    if not text:
        return [], [str(kw) for kw in (keywords or [])]
    matched: list[str] = []
    missing: list[str] = []
    for kw in keywords:
        token = str(kw or "").strip()
        if not token:
            continue
        if token.lower() in text:
            matched.append(token)
        else:
            missing.append(token)
    return matched, missing


def _keyword_hits(raw_text: str, keywords: list[str]) -> int:
    text = str(raw_text or "").lower()
    if not text:
        return 0
    hit = 0
    for kw in keywords:
        token = str(kw or "").strip().lower()
        if token and token in text:
            hit += 1
    return hit


def _looks_unreadable_page(raw_text: str) -> bool:
    text = str(raw_text or "").lower()
    if not text:
        return True
    markers = (
        "access denied",
        "forbidden",
        "captcha",
        "are you human",
        "cloudflare",
        "robot",
        "비정상적인 접근",
        "접근이 제한",
        "서비스 이용이 제한",
    )
    return any(marker in text for marker in markers)


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()


def _safe_targets(config_payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_targets = config_payload.get("targets")
    if not isinstance(raw_targets, list):
        return []
    global_allowed_domains_raw = config_payload.get("allowed_domains")
    global_allowed_domains = (
        [str(x).strip().lower() for x in global_allowed_domains_raw if str(x).strip()]
        if isinstance(global_allowed_domains_raw, list)
        else [str(item).strip().lower() for item in official_domains_list() if str(item).strip()]
    )
    targets: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in raw_targets:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        url = str(row.get("url") or "").strip()
        if (not key) or (not url) or (key in seen_keys):
            continue
        seen_keys.add(key)
        patterns = row.get("patterns")
        keywords = row.get("keywords")
        row_allowed_domains_raw = row.get("allowed_domains")
        row_allowed_domains = (
            [str(x).strip().lower() for x in row_allowed_domains_raw if str(x).strip()]
            if isinstance(row_allowed_domains_raw, list)
            else list(global_allowed_domains)
        )
        targets.append(
            {
                "key": key,
                "url": url,
                "patterns": [str(x) for x in patterns] if isinstance(patterns, list) else [],
                "keywords": [str(x) for x in keywords] if isinstance(keywords, list) else [],
                "timeout": int(row.get("timeout") or 8),
                "allowed_domains": row_allowed_domains,
            }
        )
    return targets


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw or default)
    except Exception:
        return int(default)


def _resolve_default_config_path() -> Path:
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return LEGACY_CONFIG_PATH


def _resolve_default_state_path() -> Path:
    if DEFAULT_STATE_PATH.exists():
        return DEFAULT_STATE_PATH
    return DEFAULT_STATE_PATH


def _is_allowed_domain(url: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    try:
        host = str(urlparse(str(url)).hostname or "").strip().lower()
    except Exception:
        host = ""
    if not host:
        return False
    for domain in allowed_domains:
        token = str(domain or "").strip().lower()
        if (not token) or (token.startswith(".")):
            continue
        if host == token or host.endswith(f".{token}"):
            return True
    return False


def run_watchdog(*, config_path: Path, state_path: Path, timeout: int, strict: bool = False) -> tuple[dict[str, Any], int]:
    config_payload = _load_json(config_path, fallback={})
    if not isinstance(config_payload, dict):
        config_payload = {}
    targets = _safe_targets(config_payload)

    prev_state = _load_json(state_path, fallback={})
    if not isinstance(prev_state, dict):
        prev_state = {}
    prev_targets = prev_state.get("targets") if isinstance(prev_state.get("targets"), dict) else {}

    session = requests.Session()
    headers = {
        "User-Agent": "SafeToSpend-ReferenceWatchdog/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    now = _utc_now_iso()
    result_targets: dict[str, dict[str, Any]] = {}
    changed_count = 0
    failing_count = 0
    max_failure_streak = 0

    for target in targets:
        key = str(target["key"])
        url = str(target["url"])
        patterns = list(target.get("patterns") or [])
        keywords = list(target.get("keywords") or [])
        allowed_domains = [str(x).strip().lower() for x in (target.get("allowed_domains") or []) if str(x).strip()]
        effective_timeout = max(2, int(target.get("timeout") or timeout))
        previous = prev_targets.get(key) if isinstance(prev_targets, dict) else {}
        previous_hash = str((previous or {}).get("content_hash") or "")
        previous_failure_streak = _to_int((previous or {}).get("failure_streak"), 0)
        previous_last_ok_at = str((previous or {}).get("last_ok_at") or "")
        previous_last_changed_at = str((previous or {}).get("last_changed_at") or "")

        row: dict[str, Any] = {
            "key": key,
            "url": url,
            "checked_at": now,
            "status_code": None,
            "ok": False,
            "changed": False,
            "failing": False,
            "failure_reason": "",
            "content_hash": previous_hash,
            "previous_hash": previous_hash,
            "pattern_hit_count": 0,
            "matched_patterns": [],
            "missing_patterns": [],
            "allowed_domains": allowed_domains,
            "keyword_hit_count": 0,
            "matched_keywords": [],
            "missing_keywords": [],
            "notes": [],
            "focus_preview": "",
        }

        parsed = urlparse(url)
        scheme = str(parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            row["failing"] = True
            row["failure_reason"] = "invalid_url_scheme"
        elif not str(parsed.netloc or "").strip():
            row["failing"] = True
            row["failure_reason"] = "invalid_url_host"
        elif not _is_allowed_domain(url, allowed_domains):
            row["failing"] = True
            row["failure_reason"] = "domain_not_allowed"
        else:
            try:
                response = session.get(url, timeout=effective_timeout, headers=headers)
                row["status_code"] = int(response.status_code or 0)
                response.raise_for_status()
                raw_text = str(response.text or "")
                focus_text, pattern_hits, matched_patterns, missing_patterns = _focus_text(raw_text, patterns)
                keyword_hits = _keyword_hits(raw_text, keywords)
                matched_keywords, missing_keywords = _keyword_match_lists(raw_text, keywords)

                row["pattern_hit_count"] = int(pattern_hits)
                row["keyword_hit_count"] = int(keyword_hits)
                row["matched_patterns"] = matched_patterns
                row["missing_patterns"] = missing_patterns
                row["matched_keywords"] = matched_keywords
                row["missing_keywords"] = missing_keywords
                row["focus_preview"] = str(focus_text[:220] or "")

                unreadable = _looks_unreadable_page(raw_text)
                if unreadable:
                    row["failing"] = True
                    row["failure_reason"] = "unreadable_page"
                elif keywords and keyword_hits <= 0:
                    row["failing"] = True
                    row["failure_reason"] = "keyword_not_found"

                digest = _content_hash(focus_text)
                row["content_hash"] = digest
                row["ok"] = not bool(row["failing"])
                if previous_hash and digest and previous_hash != digest:
                    row["changed"] = True
            except Exception as exc:
                row["failing"] = True
                row["ok"] = False
                row["failure_reason"] = type(exc).__name__

        notes: list[str] = []
        previous_missing_patterns = [
            str(item)
            for item in ((previous or {}).get("missing_patterns") or [])
            if str(item).strip()
        ]
        current_missing_patterns = [
            str(item)
            for item in (row.get("missing_patterns") or [])
            if str(item).strip()
        ]
        prev_set = set(previous_missing_patterns)
        curr_set = set(current_missing_patterns)
        new_missing = sorted(curr_set - prev_set)
        resolved_missing = sorted(prev_set - curr_set)

        if row["changed"]:
            notes.append("핵심 구간 텍스트 해시가 변경됐어요.")
        if new_missing:
            notes.append(f"새로 누락된 패턴: {', '.join(new_missing[:3])}")
        if resolved_missing:
            notes.append(f"복구된 패턴: {', '.join(resolved_missing[:3])}")
        if current_missing_patterns:
            notes.append(f"현재 누락 패턴 수: {len(current_missing_patterns)}")
        if row.get("failure_reason"):
            notes.append(f"실패 사유: {row.get('failure_reason')}")
        row["notes"] = notes

        if row["changed"]:
            changed_count += 1
            row["last_changed_at"] = now
        else:
            row["last_changed_at"] = previous_last_changed_at

        if row["failing"]:
            failing_count += 1
            row["failure_streak"] = int(min(9999, previous_failure_streak + 1))
            row["last_ok_at"] = previous_last_ok_at
        else:
            row["failure_streak"] = 0
            row["last_ok_at"] = now

        max_failure_streak = max(max_failure_streak, _to_int(row.get("failure_streak"), 0))

        result_targets[key] = row

    checked_count = len(result_targets)
    summary = {
        "checked_count": int(checked_count),
        "changed_count": int(changed_count),
        "failing_count": int(failing_count),
        "max_failure_streak": int(max_failure_streak),
        "ok": bool((changed_count == 0) and (failing_count == 0)),
        "strict": bool(strict),
    }

    notes: list[str] = []
    if changed_count > 0:
        notes.append("공식 페이지 핵심 구간 변경 감지")
    if failing_count > 0:
        notes.append("공식 페이지 조회/파싱 실패 감지")
    if not notes:
        notes.append("정상")

    payload = {
        # Phase 1 canonical fields
        "last_checked_at": now,
        "changed": bool(changed_count > 0),
        "failing": bool(failing_count > 0),
        "fail_streak": int(max_failure_streak),
        "checked_count": int(checked_count),
        "changed_count": int(changed_count),
        "failing_count": int(failing_count),
        "notes": notes,
        # Legacy compatibility fields
        "updated_at": now,
        "config_path": str(config_path),
        "targets": result_targets,
        "summary": summary,
    }
    _write_json(state_path, payload)

    print("[reference-watchdog]")
    print(f"checked={checked_count} changed={changed_count} failing={failing_count} strict={int(bool(strict))}")
    for key in sorted(result_targets.keys()):
        row = result_targets[key]
        marker = "OK"
        if row.get("failing"):
            marker = "FAIL"
        elif row.get("changed"):
            marker = "ALERT"
        reason = str(row.get("failure_reason") or "")
        suffix = f" reason={reason}" if reason else ""
        print(
            f"- {marker} key={key} status={int(row.get('status_code') or 0)} "
            f"changed={int(bool(row.get('changed')))} hash={str(row.get('content_hash') or '')[:12]}{suffix}"
        )

    if strict and (changed_count > 0 or failing_count > 0):
        return payload, 1
    return payload, 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="공식 참조 페이지 변화 감지 watchdog")
    p.add_argument("--config", default=str(_resolve_default_config_path()), help="감시 대상 설정 파일(JSON)")
    p.add_argument("--state", default=str(_resolve_default_state_path()), help="상태 출력 파일(JSON)")
    p.add_argument("--timeout", type=int, default=8, help="기본 timeout(초)")
    p.add_argument("--strict", action="store_true", help="변화/실패가 있으면 종료코드 1")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    _payload, code = run_watchdog(
        config_path=Path(str(args.config)).resolve(),
        state_path=Path(str(args.state)).resolve(),
        timeout=max(2, int(args.timeout or 8)),
        strict=bool(args.strict),
    )
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())

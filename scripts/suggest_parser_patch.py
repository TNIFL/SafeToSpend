#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_PATH = ROOT / "data" / "reference_watch" / "status.json"
REPORT_DIR = ROOT / "reports"


def _load_json(path: Path, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        if not path.exists():
            return dict(fallback or {})
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return dict(fallback or {})
    return dict(fallback or {})


def _extract_number_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", str(text or ""))
    uniq: list[str] = []
    for token in tokens:
        cleaned = token.strip()
        if not cleaned or cleaned in uniq:
            continue
        uniq.append(cleaned)
        if len(uniq) >= 8:
            break
    return uniq


def _number_to_pattern(token: str) -> str:
    t = str(token or "").strip()
    if not t:
        return ""
    if "." in t:
        parts = t.split(".", 1)
        left = re.escape(parts[0].replace(",", ""))
        right = re.escape(parts[1])
        return rf"{left}(?:\\.|,){right}"
    compact = re.escape(t.replace(",", ""))
    return rf"{compact}"


def _suggest_patterns(preview: str, keywords: list[str], missing_patterns: list[str]) -> list[str]:
    suggestions: list[str] = []
    for pat in missing_patterns:
        if pat and pat not in suggestions:
            suggestions.append(pat)
        if len(suggestions) >= 3:
            return suggestions

    nums = _extract_number_tokens(preview)
    for kw in keywords[:3]:
        token = str(kw or "").strip()
        if not token:
            continue
        suggestions.append(rf"{re.escape(token)}[^0-9]{{0,20}}([0-9]{{1,3}}(?:[\\.,][0-9]{{1,4}})?)")
        if len(suggestions) >= 3:
            return suggestions

    for num in nums:
        pat = _number_to_pattern(num)
        if pat and pat not in suggestions:
            suggestions.append(pat)
        if len(suggestions) >= 3:
            break
    return suggestions[:3]


def _map_target_to_config_key(target_key: str) -> str:
    key = str(target_key or "")
    if "asset" in key:
        if "home" in key:
            return "configs/parsers/assets_datasets.json :: datasets.home.keywords"
        return "configs/parsers/assets_datasets.json :: datasets.vehicle.keywords"
    if "ltc" in key:
        return "configs/parsers/nhis_rates.json :: patterns.ltc_ratio / patterns.ltc_optional"
    if "income" in key:
        return "configs/parsers/nhis_rates.json :: keywords.income_rule"
    if "health" in key or "rate" in key:
        return "configs/parsers/nhis_rates.json :: patterns.health_rate"
    return "configs/parsers/nhis_rates.json :: keywords"


def run_suggest(*, status_path: Path) -> tuple[int, Path | None]:
    payload = _load_json(status_path, fallback={})
    targets = payload.get("targets") if isinstance(payload.get("targets"), dict) else {}
    if not targets:
        print("no targets in status file")
        return 1, None

    rows: list[dict[str, Any]] = []
    for key in sorted(targets.keys()):
        row = targets.get(key)
        if not isinstance(row, dict):
            continue
        changed = bool(row.get("changed"))
        failing = bool(row.get("failing"))
        if not (changed or failing):
            continue
        preview = str(row.get("focus_preview") or "")
        missing_patterns = [str(item) for item in list(row.get("missing_patterns") or []) if str(item).strip()]
        keywords = [str(item) for item in list(row.get("missing_keywords") or []) if str(item).strip()]
        if not keywords:
            keywords = [str(item) for item in list(row.get("matched_keywords") or []) if str(item).strip()]
        suggestions = _suggest_patterns(preview, keywords, missing_patterns)
        rows.append(
            {
                "key": key,
                "status": "failing" if failing else "changed",
                "failure_reason": str(row.get("failure_reason") or ""),
                "config_hint": _map_target_to_config_key(key),
                "suggestions": suggestions,
                "missing_patterns": missing_patterns,
                "missing_keywords": [str(item) for item in list(row.get("missing_keywords") or []) if str(item).strip()],
            }
        )

    if not rows:
        print("no changed/failing target rows")
        return 0, None

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"parser_patch_suggestion_{now}.md"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Parser Patch Suggestion")
    lines.append("")
    lines.append(f"- status_path: `{status_path}`")
    lines.append(f"- generated_at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append("- 주의: 자동 적용 금지, 사람이 검토 후 설정 파일만 수정하세요.")
    lines.append("")
    for row in rows:
        lines.append(f"## {row['key']} ({row['status']})")
        if row["failure_reason"]:
            lines.append(f"- failure_reason: `{row['failure_reason']}`")
        if row["missing_patterns"]:
            lines.append(f"- missing_patterns: `{', '.join(row['missing_patterns'][:4])}`")
        if row["missing_keywords"]:
            lines.append(f"- missing_keywords: `{', '.join(row['missing_keywords'][:4])}`")
        lines.append(f"- config_hint: `{row['config_hint']}`")
        lines.append("- 후보 패턴(최대 3개):")
        for idx, pat in enumerate(row["suggestions"], start=1):
            lines.append(f"  {idx}. `{pat}`")
        lines.append("")
        lines.append("```diff")
        lines.append(f"# {row['config_hint']}")
        for pat in row["suggestions"]:
            lines.append(f"+ {pat}")
        lines.append("```")
        lines.append("")

    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"[suggest-parser-patch] report={report_path}")
    return 0, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reference Watch 상태 기반 파서 설정 패치 초안 생성")
    parser.add_argument("--status", default=str(DEFAULT_STATUS_PATH), help="watchdog 상태 파일 경로")
    args = parser.parse_args(list(argv or sys.argv[1:]))
    code, _ = run_suggest(status_path=Path(str(args.status)).resolve())
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())

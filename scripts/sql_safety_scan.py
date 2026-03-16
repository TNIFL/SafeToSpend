from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = ("routes", "services", "core")
IGNORE_DIRS = {".git", ".venv", "__pycache__", "migrations", "data", "sample_data", "static", "templates"}
IGNORE_TOKEN = "sqlsafe: ignore"

# 문자열 SQL 조립 가능성이 높은 패턴만 잡는다.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "db.session.execute에 문자열 직접 전달",
        re.compile(r"db\.session\.execute\(\s*f?[\"']"),
    ),
    (
        "sqlalchemy text()에 f-string 사용",
        re.compile(r"\btext\(\s*f[\"']"),
    ),
    (
        "SELECT/INSERT/UPDATE/DELETE f-string 사용",
        re.compile(r"f[\"']\s*(?:SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE),
    ),
    (
        "SELECT/INSERT/UPDATE/DELETE + .format 사용",
        re.compile(r"[\"']\s*(?:SELECT|INSERT|UPDATE|DELETE)\b.*?\.format\(", re.IGNORECASE),
    ),
)


def _iter_python_files() -> list[Path]:
    out: list[Path] = []
    for base_name in TARGET_DIRS:
        base = ROOT / base_name
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel_parts = path.relative_to(ROOT).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            out.append(path)
    return sorted(out)


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        if IGNORE_TOKEN in line:
            continue
        for reason, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((lineno, reason, line.strip()))
    return findings


def main() -> int:
    files = _iter_python_files()
    all_findings: list[tuple[Path, int, str, str]] = []
    for path in files:
        for lineno, reason, line in _scan_file(path):
            all_findings.append((path, lineno, reason, line))

    if all_findings:
        print("SQL SAFETY SCAN: FAIL")
        for path, lineno, reason, line in all_findings:
            rel = path.relative_to(ROOT)
            print(f"- {rel}:{lineno} | {reason} | {line}")
        print("위 패턴은 ORM/바인딩 방식으로 바꿔 주세요.")
        return 1

    print("SQL SAFETY SCAN: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path("/tmp")

EXCLUDED_GLOBS = [
    ".git/**",
    ".env",
    ".env.*",
    ".venv/**",
    "venv/**",
    "env/**",
    "ENV/**",
    "node_modules/**",
    "uploads/**",
    "reports/**",
    "reports/rehearsals/*.dump",
    "*.dump",
    "*.sql",
    "*.sqlite3",
    "*.db",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".DS_Store",
    "tmp/**",
    "logs/**",
]


def _rel_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_excluded(rel_path: str) -> bool:
    if not rel_path:
        return True
    if rel_path == ".env.example":
        return False
    for pattern in EXCLUDED_GLOBS:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel_path == prefix or rel_path.startswith(prefix + "/"):
                return True
    return False


def _iter_bundle_files() -> tuple[list[Path], list[str]]:
    include_paths: list[Path] = []
    forbidden_matches: list[str] = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel_path = _rel_posix(path)
        if _is_excluded(rel_path):
            forbidden_matches.append(rel_path)
            continue
        include_paths.append(path)

    return include_paths, sorted(forbidden_matches)


def _verify_archive_contents(output_path: Path) -> list[str]:
    forbidden_in_archive: list[str] = []
    with zipfile.ZipFile(output_path, mode="r") as zf:
        for name in zf.namelist():
            rel_path = str(name or "").strip().lstrip("./")
            if _is_excluded(rel_path):
                forbidden_in_archive.append(rel_path)
    return sorted(forbidden_in_archive)


def _build_default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"SafeToSpend_code_{stamp}.zip"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="민감 런타임 산출물을 제외한 코드 전달용 ZIP 생성")
    parser.add_argument("--output", default="", help="생성할 ZIP 경로 (기본: /tmp/SafeToSpend_code_YYYYMMDD_HHMMSS.zip)")
    parser.add_argument("--dry-run", action="store_true", help="ZIP 생성 없이 포함/제외 요약만 출력")
    parser.add_argument("--fail-if-forbidden-found", action="store_true", help="금지 경로가 repo 안에 존재하면 즉시 실패")
    args = parser.parse_args(argv)

    include_paths, excluded_paths = _iter_bundle_files()
    output_path = Path(args.output).expanduser().resolve() if str(args.output or "").strip() else _build_default_output()

    summary = {
        "root": str(ROOT),
        "output": str(output_path),
        "include_count": len(include_paths),
        "excluded_count": len(excluded_paths),
        "excluded_samples": excluded_paths[:50],
        "excluded_rules": EXCLUDED_GLOBS,
    }

    if args.fail_if_forbidden_found and excluded_paths:
        print(json.dumps({**summary, "ok": False, "reason": "forbidden_runtime_paths_present"}, ensure_ascii=False, indent=2))
        return 2

    if args.dry_run:
        print(json.dumps({**summary, "ok": True, "mode": "dry-run"}, ensure_ascii=False, indent=2))
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if ROOT in output_path.parents:
        print(
            json.dumps(
                {
                    **summary,
                    "ok": False,
                    "reason": "output_path_inside_repo",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(include_paths):
            zf.write(path, arcname=_rel_posix(path))

    forbidden_in_archive = _verify_archive_contents(output_path)
    if forbidden_in_archive:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        print(
            json.dumps(
                {
                    **summary,
                    "ok": False,
                    "reason": "forbidden_paths_found_in_archive",
                    "archive_forbidden_paths": forbidden_in_archive[:50],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {**summary, "ok": True, "mode": "write", "archive_verified": True},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

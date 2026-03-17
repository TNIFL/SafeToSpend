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

from services.official_refs.registry import (  # noqa: E402
    ALLOWED_OFFICIAL_DOMAINS,
    OFFICIAL_REFERENCE_YEAR,
    REGISTRY_VERSION,
    get_official_reference_registry,
    get_registry_hash,
    get_verify_targets,
)
from services.reference.nhis_reference import get_nhis_reference_snapshot  # noqa: E402
from services.reference.tax_reference import get_tax_reference_snapshot  # noqa: E402

REPORT_DIR = ROOT / "reports"
SNAPSHOT_ROOT = ROOT / "data" / "official_snapshots"
MANIFEST_PATH = SNAPSHOT_ROOT / "manifest.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_allowed_domain(url: str) -> bool:
    try:
        host = str(urlparse(url).hostname or "").strip().lower()
    except Exception:
        return False
    if not host:
        return False
    for token in ALLOWED_OFFICIAL_DOMAINS:
        d = str(token or "").strip().lower()
        if not d:
            continue
        if host == d or host.endswith(f".{d}"):
            return True
    return False


def _read_text_from_bytes(content: bytes) -> str:
    if not content:
        return ""
    for encoding in ("utf-8", "euc-kr", "cp949", "latin1"):
        try:
            return content.decode(encoding, errors="ignore")
        except Exception:
            continue
    return ""


def _match_required_patterns(text: str, patterns: list[str]) -> list[str]:
    missing: list[str] = []
    source = str(text or "")
    for pattern in patterns:
        p = str(pattern or "").strip()
        if not p:
            continue
        try:
            ok = bool(re.search(p, source, flags=re.IGNORECASE | re.DOTALL))
        except re.error:
            ok = False
        if not ok:
            missing.append(p)
    return missing


def _value_expectation_errors(*, key: str, text: str, expected_values: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source = str(text or "")

    def _contains_number(value: Any) -> bool:
        token = str(value)
        compact = token.replace(",", "")
        return (token in source) or (compact in source.replace(",", ""))

    if key == "nhis_rate_and_point_value":
        if not _contains_number(719):
            errors.append("health_insurance_rate_token_missing(719)")
        point = expected_values.get("property_point_value")
        point_token = f"{float(point):.1f}" if point is not None else "211.5"
        if not (
            point_token in source
            or point_token.replace(".", ",") in source
            or point_token.replace(".", "") in source.replace(",", "")
        ):
            errors.append("property_point_value_token_missing")

    elif key == "nhis_floor_ceiling":
        floor = int(expected_values.get("premium_floor_health_only") or 0)
        cap = int(expected_values.get("premium_ceiling_health_only") or 0)
        if floor <= 0 or not _contains_number(floor):
            errors.append("premium_floor_token_missing")
        if cap <= 0 or not _contains_number(cap):
            errors.append("premium_ceiling_token_missing")

    elif key == "mohw_ltc_ratio":
        if "13.14" not in source and "13,14" not in source:
            errors.append("ltc_ratio_of_health_token_missing")
        if "0.9448" not in source and "0,9448" not in source:
            errors.append("ltc_rate_of_income_token_missing")

    elif key == "tax_income_rate_table":
        required = [14_000_000, 1_260_000, 65_940_000]
        for v in required:
            if not _contains_number(v):
                errors.append(f"tax_table_token_missing:{v}")

    elif key == "tax_local_income_ratio":
        if "10" not in source:
            errors.append("local_income_tax_ratio_token_missing")

    return errors


def _save_snapshot(rel_path: str, content: bytes) -> Path:
    path = ROOT / str(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _write_manifest(payload: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def _render_report(*, checked_at: str, registry: dict[str, Any], target_rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Official Reference Audit")
    lines.append("")
    lines.append(f"- checked_at: `{checked_at}`")
    lines.append(f"- registry_version: `{registry['meta']['registry_version']}`")
    lines.append(f"- target_year: `{registry['meta']['target_year']}`")
    lines.append(f"- status: **{'PASS' if summary['valid'] else 'FAIL'}**")
    lines.append(f"- mismatches: `{summary['mismatch_count']}`")
    lines.append(f"- network_errors: `{summary['network_error_count']}`")
    lines.append("")
    lines.append("## Target Results")
    lines.append("")
    lines.append("| key | status | http | changed | details |")
    lines.append("|---|---|---:|---:|---|")
    for row in target_rows:
        status = "PASS" if row.get("ok") else "FAIL"
        http = row.get("status_code") if row.get("status_code") is not None else "-"
        changed = "yes" if row.get("changed") else "no"
        detail_parts: list[str] = []
        if row.get("failure_reason"):
            detail_parts.append(str(row["failure_reason"]))
        if row.get("missing_patterns"):
            detail_parts.append(f"missing_patterns={len(row['missing_patterns'])}")
        if row.get("value_errors"):
            detail_parts.append(f"value_errors={len(row['value_errors'])}")
        if row.get("snapshot_path"):
            detail_parts.append(str(row["snapshot_path"]))
        lines.append(f"| {row.get('key')} | {status} | {http} | {changed} | {'; '.join(detail_parts) or '-'} |")

    lines.append("")
    lines.append("## Registry Snapshot")
    lines.append("")
    lines.append("### NHIS")
    nhis = registry["nhis"]
    lines.append(f"- health_insurance_rate: `{nhis['health_insurance_rate']}`")
    lines.append(f"- property_point_value: `{nhis['property_point_value']}`")
    lines.append(f"- ltc_ratio_of_health: `{nhis['ltc_ratio_of_health']}`")
    lines.append(f"- premium_floor_health_only: `{nhis['premium_floor_health_only']}`")
    lines.append(f"- premium_ceiling_health_only: `{nhis['premium_ceiling_health_only']}`")
    lines.append(f"- property_basic_deduction_krw: `{nhis['property_basic_deduction_krw']}`")
    lines.append("")
    lines.append("### TAX")
    tax = registry["tax"]
    lines.append(f"- local_income_tax_ratio: `{tax['local_income_tax_ratio']}`")
    lines.append(f"- bracket_count: `{len(tax['income_tax_brackets'])}`")
    lines.append("")

    failed_rows = [r for r in target_rows if not r.get("ok")]
    if failed_rows:
        lines.append("## Failures")
        for row in failed_rows:
            lines.append(f"- {row.get('key')}: {row.get('failure_reason') or 'validation_failed'}")
            if row.get("missing_patterns"):
                lines.append(f"  - missing_patterns: {', '.join(row['missing_patterns'])}")
            if row.get("value_errors"):
                lines.append(f"  - value_errors: {', '.join(row['value_errors'])}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def run_verify(*, target_year: int, timeout_sec: int, offline: bool = False) -> int:
    checked_at = _utc_now_iso()
    registry = get_official_reference_registry(target_year=target_year)
    registry_hash = get_registry_hash(target_year=target_year)
    targets = get_verify_targets(target_year=target_year)

    previous_manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        try:
            previous_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            previous_manifest = {}

    previous_by_key = previous_manifest.get("targets") if isinstance(previous_manifest.get("targets"), dict) else {}
    rows: list[dict[str, Any]] = []
    mismatch_count = 0
    network_error_count = 0

    session = requests.Session()
    headers = {
        "User-Agent": "SafeToSpend-OfficialRefVerifier/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for target in targets:
        key = str(target.get("key") or "")
        url = str(target.get("url") or "")
        snapshot_rel = str(target.get("snapshot_path") or "").strip()
        required_patterns = list(target.get("required_patterns") or [])
        expected_values = dict(target.get("expected_values") or {})

        previous = previous_by_key.get(key) if isinstance(previous_by_key, dict) else {}
        previous_hash = str((previous or {}).get("sha256") or "")

        row: dict[str, Any] = {
            "key": key,
            "url": url,
            "status_code": None,
            "ok": False,
            "changed": False,
            "failure_reason": "",
            "missing_patterns": [],
            "value_errors": [],
            "snapshot_path": snapshot_rel,
            "sha256": "",
        }

        if not key or not url:
            row["failure_reason"] = "target_missing_key_or_url"
            mismatch_count += 1
            rows.append(row)
            continue

        if not _is_allowed_domain(url):
            row["failure_reason"] = "domain_not_allowed"
            mismatch_count += 1
            rows.append(row)
            continue

        content: bytes = b""

        if offline:
            snap_abs = ROOT / snapshot_rel
            if not snap_abs.exists():
                row["failure_reason"] = "offline_snapshot_missing"
                mismatch_count += 1
                rows.append(row)
                continue
            content = snap_abs.read_bytes()
            row["status_code"] = "offline"
        else:
            try:
                response = session.get(url, timeout=timeout_sec, headers=headers)
                row["status_code"] = int(response.status_code or 0)
                response.raise_for_status()
                content = bytes(response.content or b"")
            except Exception as exc:
                row["failure_reason"] = f"network_error:{type(exc).__name__}"
                mismatch_count += 1
                network_error_count += 1
                rows.append(row)
                continue

            try:
                _save_snapshot(snapshot_rel, content)
            except Exception as exc:
                row["failure_reason"] = f"snapshot_write_failed:{type(exc).__name__}"
                mismatch_count += 1
                rows.append(row)
                continue

        digest = _sha256_bytes(content)
        row["sha256"] = digest
        row["changed"] = bool(previous_hash and previous_hash != digest)

        text = _read_text_from_bytes(content)
        missing_patterns = _match_required_patterns(text, required_patterns)
        if missing_patterns:
            row["missing_patterns"] = missing_patterns

        value_errors = _value_expectation_errors(key=key, text=text, expected_values=expected_values)
        if value_errors:
            row["value_errors"] = value_errors

        if missing_patterns or value_errors:
            row["failure_reason"] = "content_validation_failed"
            mismatch_count += 1
        else:
            row["ok"] = True

        rows.append(row)

    # Internal constants sanity (exact check)
    nhis_ref = get_nhis_reference_snapshot(target_year)
    tax_ref = get_tax_reference_snapshot(target_year)
    internal_errors: list[str] = []
    if float(nhis_ref.health_insurance_rate) != 0.0719:
        internal_errors.append("nhis.health_insurance_rate")
    if float(nhis_ref.property_point_value) != 211.5:
        internal_errors.append("nhis.property_point_value")
    if float(nhis_ref.ltc_ratio_of_health) != 0.1314:
        internal_errors.append("nhis.ltc_ratio_of_health")
    if int(nhis_ref.premium_floor_health_only) != 20_160:
        internal_errors.append("nhis.premium_floor_health_only")
    if int(nhis_ref.premium_ceiling_health_only) != 4_591_740:
        internal_errors.append("nhis.premium_ceiling_health_only")
    if float(tax_ref.local_income_tax_ratio) != 0.10:
        internal_errors.append("tax.local_income_tax_ratio")

    if internal_errors:
        mismatch_count += len(internal_errors)
        rows.append(
            {
                "key": "internal_registry_exact_check",
                "url": "local",
                "status_code": "local",
                "ok": False,
                "changed": False,
                "failure_reason": "internal_registry_mismatch",
                "missing_patterns": [],
                "value_errors": internal_errors,
                "snapshot_path": "-",
                "sha256": "",
            }
        )

    valid = mismatch_count == 0 and network_error_count == 0
    reason = "ok" if valid else ("network_failure" if network_error_count > 0 else "value_mismatch")

    targets_manifest: dict[str, Any] = {}
    for row in rows:
        targets_manifest[str(row.get("key") or "")] = {
            "url": row.get("url"),
            "sha256": row.get("sha256"),
            "status_code": row.get("status_code"),
            "ok": bool(row.get("ok")),
            "changed": bool(row.get("changed")),
            "failure_reason": row.get("failure_reason"),
            "snapshot_path": row.get("snapshot_path"),
        }

    manifest_payload = {
        "checked_at": checked_at,
        "valid": bool(valid),
        "reason": reason,
        "registry_version": REGISTRY_VERSION,
        "registry_hash": registry_hash,
        "mismatch_count": int(mismatch_count),
        "network_error_count": int(network_error_count),
        "targets": targets_manifest,
    }
    _write_manifest(manifest_payload)

    summary = {
        "valid": bool(valid),
        "mismatch_count": int(mismatch_count),
        "network_error_count": int(network_error_count),
    }
    report_text = _render_report(checked_at=checked_at, registry=registry, target_rows=rows, summary=summary)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_name = f"official_ref_audit_{datetime.now().strftime('%Y%m%d')}.md"
    report_path = REPORT_DIR / report_name
    report_path.write_text(report_text, encoding="utf-8")

    print(f"[official-ref-verify] report={report_path}")
    print(f"[official-ref-verify] manifest={MANIFEST_PATH}")
    print(f"[official-ref-verify] status={'PASS' if valid else 'FAIL'} mismatches={mismatch_count} network_errors={network_error_count}")

    return 0 if valid else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify official references and update manifest/report.")
    parser.add_argument("--target-year", type=int, default=OFFICIAL_REFERENCE_YEAR)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--offline", action="store_true", help="Use only existing snapshot files without network")
    args = parser.parse_args()

    return run_verify(target_year=int(args.target_year), timeout_sec=max(2, int(args.timeout)), offline=bool(args.offline))


if __name__ == "__main__":
    raise SystemExit(main())

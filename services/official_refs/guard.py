from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .registry import OFFICIAL_REFERENCE_YEAR, REGISTRY_VERSION, get_registry_hash
from services.nhis_rates import get_active_snapshot
from services.reference.nhis_reference import diff_runtime_snapshot_vs_reference

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "data" / "official_snapshots" / "manifest.json"
NHIS_PROPERTY_POINTS_PATH_2026 = ROOT / "data" / "nhis_property_points_2026.json"


def _load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        return {}
    return {}


def _extract_manifest_failure_reasons(manifest: dict[str, Any]) -> list[str]:
    out: list[str] = []
    targets = manifest.get("targets")
    if not isinstance(targets, dict):
        return out
    for value in targets.values():
        if not isinstance(value, dict):
            continue
        ok = bool(value.get("ok"))
        if ok:
            continue
        reason = str(value.get("failure_reason") or "").strip().lower()
        if reason:
            out.append(reason)
    return out


def _is_artifact_only_value_mismatch(manifest: dict[str, Any]) -> bool:
    if bool(manifest.get("valid")):
        return False
    reason = str(manifest.get("reason") or "").strip().lower()
    if reason != "value_mismatch":
        return False
    if int(manifest.get("network_error_count") or 0) != 0:
        return False
    failure_reasons = _extract_manifest_failure_reasons(manifest)
    if not failure_reasons:
        return False
    return all(r == "offline_snapshot_missing" for r in failure_reasons)


def get_official_guard_status(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = _load_manifest(path)
    expected_hash = get_registry_hash(target_year=OFFICIAL_REFERENCE_YEAR)

    base = {
        "valid": False,
        "reason": "not_verified",
        "message": "공식 기준 업데이트가 필요해요. 관리자에게 문의해 주세요.",
        "manifest_path": str(path),
        "registry_version": REGISTRY_VERSION,
        "expected_registry_hash": expected_hash,
        "actual_registry_hash": "",
        "last_checked_at": "",
        "mismatch_count": 0,
        "network_error_count": 0,
    }

    if not manifest:
        return base

    actual_hash = str(manifest.get("registry_hash") or "").strip()
    result_valid = bool(manifest.get("valid"))
    mismatch_count = int(manifest.get("mismatch_count") or 0)
    network_error_count = int(manifest.get("network_error_count") or 0)
    reason = str(manifest.get("reason") or "").strip() or "unknown"
    last_checked_at = str(manifest.get("checked_at") or "").strip()

    out = dict(base)
    out["actual_registry_hash"] = actual_hash
    out["last_checked_at"] = last_checked_at
    out["mismatch_count"] = mismatch_count
    out["network_error_count"] = network_error_count

    if actual_hash != expected_hash:
        out["reason"] = "registry_hash_mismatch"
        out["message"] = "공식 기준 검증 버전이 달라 숫자를 표시할 수 없어요. 관리자에게 문의해 주세요."
        return out

    if not result_valid:
        out["reason"] = reason or "validation_failed"
        if network_error_count > 0:
            out["message"] = "공식 기준 확인에 실패해 지금은 숫자를 보여드릴 수 없어요. 잠시 후 다시 시도해 주세요."
        elif mismatch_count > 0:
            out["message"] = "공식 기준 변경이 감지되어 숫자 표시를 잠시 중단했어요. 관리자 확인이 필요해요."
        else:
            out["message"] = "공식 기준 검증이 완료되지 않아 지금은 숫자를 보여드릴 수 없어요."
        return out

    out["valid"] = True
    out["reason"] = "ok"
    out["message"] = ""
    return out


def is_official_refs_valid(path: Path = MANIFEST_PATH) -> bool:
    return bool(get_official_guard_status(path).get("valid"))


def _snapshot_has_required_fields(snapshot: Any) -> bool:
    try:
        health_rate = float(getattr(snapshot, "health_insurance_rate", 0) or 0)
        ltc_ratio = float(getattr(snapshot, "long_term_care_ratio_of_health", 0) or 0)
        point_value = float(getattr(snapshot, "regional_point_value", 0) or 0)
        deduction = int(getattr(snapshot, "property_basic_deduction_krw", 0) or 0)
    except Exception:
        return False
    return bool(health_rate > 0 and ltc_ratio > 0 and point_value > 0 and deduction >= 0)


def _snapshot_guard_warnings(snapshot: Any) -> list[str]:
    warnings: list[str] = []
    sources_json = getattr(snapshot, "sources_json", None)
    if not isinstance(sources_json, dict):
        return warnings
    if bool(sources_json.get("format_drift_detected")):
        warnings.append("snapshot_format_drift_detected")
    bootstrap = sources_json.get("bootstrap")
    if isinstance(bootstrap, dict) and str(bootstrap.get("source") or "").strip().lower() == "built_in_default":
        warnings.append("snapshot_bootstrap_default")
    return warnings


def _load_property_points_table(path: Path) -> tuple[bool, str]:
    try:
        if not path.exists():
            return False, "property_points_table_missing"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False, "property_points_table_invalid_json"
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) <= 0:
            return False, "property_points_table_rows_missing"
        cleaned = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            upper = int(row.get("upper_krw") or 0)
            points = float(row.get("points") or 0.0)
            if upper > 0 and points >= 0:
                cleaned += 1
        if cleaned <= 0:
            return False, "property_points_table_rows_invalid"
        return True, "ok"
    except Exception:
        return False, "property_points_table_load_failed"


def check_nhis_ready(
    *,
    manifest_path: Path = MANIFEST_PATH,
    property_points_path: Path = NHIS_PROPERTY_POINTS_PATH_2026,
    guard_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = dict(guard_status or get_official_guard_status(manifest_path))
    manifest = _load_manifest(manifest_path)
    artifact_only_mismatch = _is_artifact_only_value_mismatch(manifest)
    base_message = "공식 기준 업데이트가 필요해요. 잠시 후 다시 시도해 주세요."
    out: dict[str, Any] = {
        "ready": False,
        "reason": str(status.get("reason") or "not_verified"),
        "message": str(status.get("message") or base_message),
        "manifest_valid": bool(status.get("valid")),
        "last_checked_at": str(status.get("last_checked_at") or ""),
        "snapshot_year": None,
        "property_points_table_path": str(property_points_path),
        "property_points_table_loaded": False,
        "guard_mode": "strict",
        "guard_warning": "",
        "guard_warnings": [],
        "snapshot_value_mismatches": [],
    }

    if not bool(status.get("valid")):
        if not artifact_only_mismatch:
            return out
        out["guard_mode"] = "degraded_artifact_only"
        out["guard_warning"] = "official_snapshot_artifact_missing"
        out["guard_warnings"] = ["official_snapshot_artifact_missing"]

    try:
        snapshot = get_active_snapshot()
    except Exception:
        out["reason"] = "snapshot_load_failed"
        out["message"] = "공식 기준 스냅샷을 확인하지 못해 계산할 수 없어요."
        return out
    if snapshot is None:
        out["reason"] = "snapshot_missing"
        out["message"] = "공식 기준 스냅샷이 없어 계산할 수 없어요. 관리자에게 문의해 주세요."
        return out

    try:
        out["snapshot_year"] = int(getattr(snapshot, "effective_year", 0) or 0)
    except Exception:
        out["snapshot_year"] = 0

    if getattr(snapshot, "is_active", True) is False:
        out["reason"] = "snapshot_inactive"
        out["message"] = "활성 공식 기준 스냅샷이 없어 계산할 수 없어요."
        return out

    if not _snapshot_has_required_fields(snapshot):
        out["reason"] = "snapshot_required_fields_missing"
        out["message"] = "공식 기준 스냅샷 필수 값이 누락돼 계산할 수 없어요."
        return out

    mismatches = diff_runtime_snapshot_vs_reference(
        effective_year=int(out.get("snapshot_year") or OFFICIAL_REFERENCE_YEAR),
        health_insurance_rate=getattr(snapshot, "health_insurance_rate", 0),
        long_term_care_ratio_of_health=getattr(snapshot, "long_term_care_ratio_of_health", 0),
        regional_point_value=getattr(snapshot, "regional_point_value", 0),
        property_basic_deduction_krw=getattr(snapshot, "property_basic_deduction_krw", 0),
    )
    out["snapshot_value_mismatches"] = list(mismatches)
    if mismatches:
        out["reason"] = "snapshot_value_mismatch"
        out["message"] = "공식 기준 스냅샷 값이 기준 레퍼런스와 달라 계산을 제한했어요."
        return out

    snapshot_warnings = _snapshot_guard_warnings(snapshot)
    if snapshot_warnings:
        merged = list(dict.fromkeys([*(out.get("guard_warnings") or []), *snapshot_warnings]))
        out["guard_warnings"] = merged
        if not str(out.get("guard_warning") or "").strip():
            out["guard_warning"] = str(snapshot_warnings[0])
        if out.get("guard_mode") != "degraded_artifact_only":
            out["reason"] = "snapshot_format_drift_or_fallback"
            out["message"] = "공식 기준 스냅샷 상태가 불안정해 숫자를 표시할 수 없어요."
            return out

    table_ok, table_reason = _load_property_points_table(property_points_path)
    out["property_points_table_loaded"] = bool(table_ok)
    if not table_ok:
        out["reason"] = table_reason
        out["message"] = "공식 재산 점수표가 준비되지 않아 숫자를 표시할 수 없어요."
        return out

    out["ready"] = True
    out["reason"] = "ok"
    out["message"] = ""
    return out

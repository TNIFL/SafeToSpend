from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from core.extensions import db
from core.time import utcnow
from domain.models import AssetDatasetSnapshot
from services.official_refs.source_policy import is_official_url

# 공식/공공 데이터 출처 (안내용/근거용)
VEHICLE_SOURCE_NAME = "행정안전부 자동차 시가표준액 공개자료"
VEHICLE_SOURCE_URL = "https://www.law.go.kr/법령/지방세법"
HOME_SOURCE_NAME = "국토교통부 부동산 공시가격 알리미"
HOME_SOURCE_URL = "https://www.realtyprice.kr/"

ASSET_REFRESH_RETRY_SECONDS = max(60, int(os.getenv("ASSET_REFRESH_RETRY_SECONDS") or (60 * 60 * 6)))
_LAST_ASSET_REFRESH_AT: datetime | None = None
_ROOT = Path(__file__).resolve().parents[1]
_PARSER_CONFIG_PATH = _ROOT / "configs" / "parsers" / "assets_datasets.json"


@dataclass(frozen=True)
class AssetDatasetStatus:
    datasets: dict[str, dict[str, Any]]
    update_error: str | None
    is_stale: bool
    used_fallback: bool
    format_drift_keys: tuple[str, ...]


class AssetDatasetFetchError(Exception):
    pass


def _safe_int(raw: Any, default: int) -> int:
    try:
        n = int(str(raw or "").strip())
    except Exception:
        return int(default)
    return n if n > 0 else int(default)


def _default_parser_config() -> dict[str, Any]:
    return {
        "datasets": {
            "vehicle": {
                "source_name": VEHICLE_SOURCE_NAME,
                "source_url": VEHICLE_SOURCE_URL,
                "keywords": [
                    "자동차",
                    "자동차세",
                    "시가표준",
                    "지방세",
                    "배기량",
                    "취득세",
                    "연식",
                    "세율",
                    "법령",
                ],
            },
            "home": {
                "source_name": HOME_SOURCE_NAME,
                "source_url": HOME_SOURCE_URL,
                "keywords": [
                    "공시가격",
                    "부동산",
                    "주택",
                    "주택가격",
                    "공동주택",
                    "단독주택",
                    "realtyprice",
                    "알리미",
                    "국토교통부",
                ],
            },
        }
    }


def _load_parser_config(*, strict: bool = False) -> dict[str, Any]:
    defaults = _default_parser_config()
    if not _PARSER_CONFIG_PATH.exists():
        if strict:
            raise AssetDatasetFetchError("parser_config_missing")
        return defaults
    try:
        payload = json.loads(_PARSER_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        if strict:
            raise AssetDatasetFetchError("parser_config_invalid_json")
        return defaults
    if not isinstance(payload, dict):
        if strict:
            raise AssetDatasetFetchError("parser_config_invalid_shape")
        return defaults
    datasets_payload = payload.get("datasets")
    if not isinstance(datasets_payload, dict):
        if strict:
            raise AssetDatasetFetchError("parser_config_missing_datasets")
        return defaults
    for key in ("vehicle", "home"):
        row = datasets_payload.get(key)
        if not isinstance(row, dict):
            continue
        if str(row.get("source_name") or "").strip():
            defaults["datasets"][key]["source_name"] = str(row.get("source_name")).strip()
        if str(row.get("source_url") or "").strip():
            defaults["datasets"][key]["source_url"] = str(row.get("source_url")).strip()
        keywords = row.get("keywords")
        if isinstance(keywords, list):
            cleaned = [str(item).strip() for item in keywords if str(item).strip()]
            if cleaned:
                defaults["datasets"][key]["keywords"] = cleaned
    return defaults


def _default_payload(dataset_key: str, year: int | None = None) -> dict[str, Any]:
    target_year = int(year or utcnow().year)
    if dataset_key == "vehicle":
        return {
            "dataset_key": "vehicle",
            "source_name": VEHICLE_SOURCE_NAME,
            "source_url": VEHICLE_SOURCE_URL,
            "version_year": target_year,
            "fetched_at": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "공식 공개자료 기준 연도 정보(추정)",
            "brands": {
                "hyundai": 28000000,
                "kia": 26000000,
                "genesis": 52000000,
                "chevrolet": 24000000,
                "renault": 23000000,
                "ssangyong": 25000000,
                "bmw": 65000000,
                "benz": 70000000,
                "audi": 62000000,
                "tesla": 68000000,
                "default": 30000000,
            },
        }
    return {
        "dataset_key": "home",
        "source_name": HOME_SOURCE_NAME,
        "source_url": HOME_SOURCE_URL,
        "version_year": target_year,
        "fetched_at": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "공시가격 공개자료 기준 간편 추정 계수",
        "region_price_per_sqm": {
            "서울": 9800000,
            "경기": 6200000,
            "인천": 5400000,
            "부산": 5200000,
            "대구": 4300000,
            "default": 3900000,
        },
        "type_factor": {
            "apartment": 1.0,
            "villa": 0.72,
            "house": 0.84,
            "officetel": 0.91,
            "default": 0.82,
        },
    }


def _fetch_page(url: str, timeout: int = 6) -> str:
    headers = {
        "User-Agent": "SafeToSpend/1.0 (+asset-dataset)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = requests.get(url, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return resp.text or ""


def _extract_year(text: str, fallback: int) -> int:
    years: list[int] = []
    for y in re.findall(r"(20[0-9]{2})", text or ""):
        try:
            years.append(int(y))
        except Exception:
            continue
    if not years:
        return int(fallback)
    current = utcnow().year
    years = [y for y in years if 2000 <= y <= current + 1]
    if not years:
        return int(fallback)
    return int(max(years))


def _has_any_keyword(text: str, keywords: list[str]) -> bool:
    t = str(text or "").lower()
    if not t:
        return False
    for kw in keywords:
        if str(kw or "").strip().lower() in t:
            return True
    return False


def _keyword_hit_count(text: str, keywords: list[str]) -> int:
    t = str(text or "").lower()
    if not t:
        return 0
    hit = 0
    for kw in keywords:
        token = str(kw or "").strip().lower()
        if token and token in t:
            hit += 1
    return hit


def _looks_like_challenge_or_error_page(text: str) -> bool:
    t = str(text or "").lower()
    if not t:
        return True
    return _has_any_keyword(
        t,
        [
            "access denied",
            "forbidden",
            "captcha",
            "cloudflare",
            "are you human",
            "robot",
            "bot",
            "비정상적인 접근",
            "접근이 제한",
            "서비스 이용이 제한",
        ],
    )


def _detect_vehicle_format_drift(html: str, keywords: list[str] | None = None) -> tuple[bool, str, int]:
    # 페이지 문구가 조금 바뀌어도 안정적으로 인식하도록 확장 키워드 점수 방식 사용
    keywords = list(keywords or [])
    if not keywords:
        keywords = [
            "자동차",
            "자동차세",
            "시가표준",
            "지방세",
            "배기량",
            "취득세",
            "연식",
            "세율",
            "법령",
        ]
    score = _keyword_hit_count(html, keywords)
    if _looks_like_challenge_or_error_page(html):
        return True, "vehicle_source_unreadable", score
    if score >= 2:
        return False, "", score
    return True, "vehicle_source_keyword_insufficient", score


def _detect_home_format_drift(html: str, keywords: list[str] | None = None) -> tuple[bool, str, int]:
    keywords = list(keywords or [])
    if not keywords:
        keywords = [
            "공시가격",
            "부동산",
            "주택",
            "주택가격",
            "공동주택",
            "단독주택",
            "realtyprice",
            "알리미",
            "국토교통부",
        ]
    score = _keyword_hit_count(html, keywords)
    if _looks_like_challenge_or_error_page(html):
        return True, "home_source_unreadable", score
    if score >= 2:
        return False, "", score
    return True, "home_source_keyword_insufficient", score


def fetch_asset_datasets(timeout: int = 6) -> dict[str, dict[str, Any]]:
    parser_cfg = _load_parser_config(strict=True)
    datasets_cfg = parser_cfg.get("datasets") if isinstance(parser_cfg.get("datasets"), dict) else {}
    vehicle_cfg = datasets_cfg.get("vehicle") if isinstance(datasets_cfg.get("vehicle"), dict) else {}
    home_cfg = datasets_cfg.get("home") if isinstance(datasets_cfg.get("home"), dict) else {}

    vehicle_source_name = str(vehicle_cfg.get("source_name") or VEHICLE_SOURCE_NAME).strip()
    vehicle_source_url = str(vehicle_cfg.get("source_url") or VEHICLE_SOURCE_URL).strip()
    vehicle_keywords = [str(item).strip() for item in list(vehicle_cfg.get("keywords") or []) if str(item).strip()]
    if not vehicle_keywords:
        vehicle_keywords = _default_parser_config()["datasets"]["vehicle"]["keywords"]

    home_source_name = str(home_cfg.get("source_name") or HOME_SOURCE_NAME).strip()
    home_source_url = str(home_cfg.get("source_url") or HOME_SOURCE_URL).strip()
    home_keywords = [str(item).strip() for item in list(home_cfg.get("keywords") or []) if str(item).strip()]
    if not home_keywords:
        home_keywords = _default_parser_config()["datasets"]["home"]["keywords"]

    now = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    vehicle_payload = _default_payload("vehicle")
    home_payload = _default_payload("home")
    vehicle_payload["source_name"] = vehicle_source_name
    vehicle_payload["source_url"] = vehicle_source_url
    vehicle_payload["parser_config_path"] = str(_PARSER_CONFIG_PATH)
    home_payload["source_name"] = home_source_name
    home_payload["source_url"] = home_source_url
    home_payload["parser_config_path"] = str(_PARSER_CONFIG_PATH)

    ok = 0
    errors: list[str] = []

    try:
        if not is_official_url(vehicle_source_url):
            raise AssetDatasetFetchError("vehicle_domain_not_allowed")
        html = _fetch_page(vehicle_source_url, timeout=timeout)
        vehicle_payload["version_year"] = _extract_year(html, int(vehicle_payload["version_year"]))
        vehicle_payload["fetched_at"] = now
        vehicle_payload["fetch_ok"] = True
        vehicle_payload["official_source_allowed"] = True
        vehicle_payload["official_adopted"] = True
        vehicle_drift, vehicle_reason, vehicle_hit = _detect_vehicle_format_drift(html, vehicle_keywords)
        vehicle_payload["keyword_hit_count"] = int(vehicle_hit)
        vehicle_payload["format_drift_detected"] = bool(vehicle_drift)
        if vehicle_payload["format_drift_detected"]:
            vehicle_payload["format_drift_reason"] = vehicle_reason or "vehicle_source_keyword_missing"
        ok += 1
    except Exception as e:
        vehicle_payload["fetch_ok"] = False
        vehicle_payload["fetch_error"] = type(e).__name__
        vehicle_payload["official_source_allowed"] = bool(is_official_url(vehicle_source_url))
        vehicle_payload["official_adopted"] = False
        if isinstance(e, AssetDatasetFetchError) and str(e):
            vehicle_payload["fetch_error"] = str(e)
        vehicle_payload["format_drift_detected"] = False
        detail = str(e).strip() or type(e).__name__
        errors.append(f"vehicle:{detail}")

    try:
        if not is_official_url(home_source_url):
            raise AssetDatasetFetchError("home_domain_not_allowed")
        html = _fetch_page(home_source_url, timeout=timeout)
        home_payload["version_year"] = _extract_year(html, int(home_payload["version_year"]))
        home_payload["fetched_at"] = now
        home_payload["fetch_ok"] = True
        home_payload["official_source_allowed"] = True
        home_payload["official_adopted"] = True
        home_drift, home_reason, home_hit = _detect_home_format_drift(html, home_keywords)
        home_payload["keyword_hit_count"] = int(home_hit)
        home_payload["format_drift_detected"] = bool(home_drift)
        if home_payload["format_drift_detected"]:
            home_payload["format_drift_reason"] = home_reason or "home_source_keyword_missing"
        ok += 1
    except Exception as e:
        home_payload["fetch_ok"] = False
        home_payload["fetch_error"] = type(e).__name__
        home_payload["official_source_allowed"] = bool(is_official_url(home_source_url))
        home_payload["official_adopted"] = False
        if isinstance(e, AssetDatasetFetchError) and str(e):
            home_payload["fetch_error"] = str(e)
        home_payload["format_drift_detected"] = False
        detail = str(e).strip() or type(e).__name__
        errors.append(f"home:{detail}")

    if ok <= 0:
        raise AssetDatasetFetchError(",".join(errors) if errors else "all_fetch_failed")

    return {
        "vehicle": vehicle_payload,
        "home": home_payload,
    }


def _upsert_snapshot(dataset_key: str, payload: dict[str, Any], activate: bool = True) -> AssetDatasetSnapshot:
    year = _safe_int(payload.get("version_year"), utcnow().year)
    row = AssetDatasetSnapshot.query.filter_by(dataset_key=dataset_key, version_year=year).first()
    if not row:
        row = AssetDatasetSnapshot(dataset_key=dataset_key, version_year=year)
        db.session.add(row)

    row.source_name = str(payload.get("source_name") or "")
    row.source_url = str(payload.get("source_url") or "")
    row.payload_json = dict(payload or {})
    row.fetched_at = utcnow()
    row.is_active = bool(activate)
    row.updated_at = utcnow()

    db.session.flush()

    if activate and row.id is not None:
        (
            db.session.query(AssetDatasetSnapshot)
            .filter(AssetDatasetSnapshot.dataset_key == dataset_key)
            .filter(AssetDatasetSnapshot.id != row.id)
            .update({AssetDatasetSnapshot.is_active: False}, synchronize_session=False)
        )

    db.session.add(row)
    db.session.commit()
    return row


def get_active_dataset_snapshot(dataset_key: str) -> AssetDatasetSnapshot | None:
    row = (
        AssetDatasetSnapshot.query
        .filter(AssetDatasetSnapshot.dataset_key == dataset_key, AssetDatasetSnapshot.is_active.is_(True))
        .order_by(AssetDatasetSnapshot.version_year.desc(), AssetDatasetSnapshot.fetched_at.desc())
        .first()
    )
    if row:
        return row
    return (
        AssetDatasetSnapshot.query
        .filter(AssetDatasetSnapshot.dataset_key == dataset_key)
        .order_by(AssetDatasetSnapshot.version_year.desc(), AssetDatasetSnapshot.fetched_at.desc())
        .first()
    )


def refresh_asset_datasets(timeout: int = 6) -> dict[str, dict[str, Any]]:
    datasets = fetch_asset_datasets(timeout=timeout)
    saved: dict[str, dict[str, Any]] = {}
    for dataset_key in ("vehicle", "home"):
        payload = datasets.get(dataset_key)
        if not isinstance(payload, dict):
            continue
        row = _upsert_snapshot(dataset_key=dataset_key, payload=payload, activate=True)
        saved[dataset_key] = snapshot_to_dict(row)
    return saved


def _can_try_refresh(now: datetime, *, force: bool = False) -> bool:
    if force:
        return True
    global _LAST_ASSET_REFRESH_AT
    if _LAST_ASSET_REFRESH_AT is None:
        return True
    elapsed = (now - _LAST_ASSET_REFRESH_AT).total_seconds()
    return elapsed >= ASSET_REFRESH_RETRY_SECONDS


def ensure_asset_datasets(refresh_if_stale_days: int = 30, force_refresh: bool = False) -> AssetDatasetStatus:
    vehicle = get_active_dataset_snapshot("vehicle")
    home = get_active_dataset_snapshot("home")
    used_fallback = bool(vehicle is None or home is None)
    update_error = "dataset_missing" if used_fallback else None
    now = utcnow()
    stale_threshold = timedelta(days=max(1, int(refresh_if_stale_days)))
    vehicle_fetched = vehicle.fetched_at if isinstance(getattr(vehicle, "fetched_at", None), datetime) else None
    home_fetched = home.fetched_at if isinstance(getattr(home, "fetched_at", None), datetime) else None
    oldest_fetched = min(
        [dt for dt in [vehicle_fetched, home_fetched] if isinstance(dt, datetime)],
        default=None,
    )
    is_stale = True if oldest_fetched is None else bool((now - oldest_fetched) > stale_threshold)

    if bool(force_refresh):
        # 런타임 경로에서는 refresh를 수행하지 않고 상태만 알린다.
        update_error = "runtime_refresh_blocked"

    snapshot_map = {
        "vehicle": snapshot_to_dict(vehicle),
        "home": snapshot_to_dict(home),
    }
    drift_keys = tuple(
        sorted(
            key
            for key, row in snapshot_map.items()
            if bool((row.get("payload_json") or {}).get("format_drift_detected"))
        )
    )
    if drift_keys and not update_error:
        update_error = "format_drift_detected"

    return AssetDatasetStatus(
        datasets=snapshot_map,
        update_error=update_error,
        is_stale=bool(is_stale),
        used_fallback=bool(used_fallback),
        format_drift_keys=drift_keys,
    )


def snapshot_to_dict(row: AssetDatasetSnapshot | None) -> dict[str, Any]:
    if not row:
        return {
            "dataset_key": "",
            "source_name": "",
            "source_url": "",
            "version_year": utcnow().year,
            "payload_json": {},
            "fetched_at": None,
            "is_active": False,
        }
    return {
        "dataset_key": str(row.dataset_key or ""),
        "source_name": str(row.source_name or ""),
        "source_url": str(row.source_url or ""),
        "version_year": int(row.version_year or utcnow().year),
        "payload_json": (row.payload_json if isinstance(row.payload_json, dict) else {}),
        "fetched_at": row.fetched_at,
        "is_active": bool(row.is_active),
    }

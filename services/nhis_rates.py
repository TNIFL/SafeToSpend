from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from core.extensions import db
from core.time import utcnow
from domain.models import NhisRateSnapshot
from services.official_refs.source_policy import is_official_url
from services.nhis_reference import get_official_defaults


MOHW_HEALTH_RATE_URL = "https://www.mohw.go.kr/gallery.es?act=view&bid=0003&list_no=379625&mid=a10607030000"
MOHW_LTC_RATE_URL = "https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010300"
EASYLAW_POINT_VALUE_URL = "https://easylaw.go.kr/CSP/CnpClsMain.laf?ccfNo=4&cciNo=1&cnpClsNo=1&csmSeq=1141&popMenu=ov"
MOHW_POLICY_CHANGE_URL = "https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1479847&mid=a10503000000"
KOREA_KR_POLICY_URL = "https://www.korea.kr/docViewer/result/2024.01/05/a84f92d76bfce4dc951e5b694131516e/a84f92d76bfce4dc951e5b694131516e.view.xhtml"
LAW_GO_KR_INCOME_RULE_URL = "https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493"

MOHW_HEALTH_RATE_URL_OLD = "https://www.mohw.go.kr/board.es?act=view&bid=0027&cg_code=&list_no=1487279&mid=a10503010300&tag="
MOHW_LTC_RATE_URL_OLD = "https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010100"


def _safe_env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    return value if value > 0 else int(default)


NHIS_REFRESH_RETRY_SECONDS = _safe_env_int("NHIS_REFRESH_RETRY_SECONDS", 60 * 60 * 6)
_LAST_REFRESH_ATTEMPT_AT: datetime | None = None
_ROOT = Path(__file__).resolve().parents[1]
_PARSER_CONFIG_PATH = _ROOT / "configs" / "parsers" / "nhis_rates.json"


@dataclass(frozen=True)
class NhisSnapshotStatus:
    snapshot: NhisRateSnapshot | None
    update_error: str | None
    is_stale: bool
    is_fallback_default: bool


class NhisRatesFetchError(Exception):
    pass


def _default_year() -> int:
    try:
        return int(utcnow().strftime("%Y"))
    except Exception:
        return 2026


def _as_decimal(raw: Any, fallback: str = "0") -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(fallback)


def _clamp_decimal(raw: Any, *, fallback: str, min_v: str, max_v: str) -> Decimal:
    value = _as_decimal(raw, fallback)
    lo = _as_decimal(min_v, min_v)
    hi = _as_decimal(max_v, max_v)
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _default_values(effective_year: int | None = None) -> dict[str, Any]:
    year = int(effective_year or _default_year())
    defaults = get_official_defaults(year)
    health_rate_default = defaults.get("health_insurance_rate")
    ltc_ratio_default = defaults.get("long_term_care_ratio_of_health")
    ltc_optional_default = defaults.get("long_term_care_rate_optional")
    point_value_default = defaults.get("regional_point_value")
    property_deduction_default = defaults.get("property_basic_deduction_krw")
    return {
        "effective_year": int(year),
        "health_insurance_rate": _as_decimal(health_rate_default, str(health_rate_default or "0")),
        "long_term_care_ratio_of_health": _as_decimal(ltc_ratio_default, str(ltc_ratio_default or "0")),
        "long_term_care_rate_optional": _as_decimal(ltc_optional_default, str(ltc_optional_default or "0")),
        "regional_point_value": _as_decimal(point_value_default, str(point_value_default or "0")),
        "property_basic_deduction_krw": int(property_deduction_default or 0),
        "car_premium_enabled": bool(defaults.get("car_premium_enabled", False)),
        "income_reference_rule": str(
            defaults.get("income_reference_rule") or "1~10월은 전전년도 소득, 11~12월은 전년도 소득을 반영합니다."
        ),
    }


def _default_parser_config() -> dict[str, Any]:
    return {
        "url_candidates": {
            "health_rate": [MOHW_HEALTH_RATE_URL, MOHW_HEALTH_RATE_URL_OLD],
            "ltc_rate": [MOHW_LTC_RATE_URL, MOHW_LTC_RATE_URL_OLD],
            "point_value": [EASYLAW_POINT_VALUE_URL],
            "policy_change": [MOHW_POLICY_CHANGE_URL, KOREA_KR_POLICY_URL],
            "income_rule": [LAW_GO_KR_INCOME_RULE_URL],
        },
        "patterns": {
            "health_rate": [
                r"건강보험(?:료)?율[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
                r"보험료율[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
            ],
            "ltc_ratio": [
                r"건강보험료의[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
            ],
            "ltc_optional": [
                r"장기요양(?:보험)?(?:료)?(?:율)?[^0-9]{0,30}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
                r"요양보험료율[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
            ],
            "point_value": [
                r"점수당\s*금액[^0-9]{0,20}([0-9]{1,4}(?:\.[0-9]{1,3})?)\s*원",
                r"점수당금액[^0-9]{0,20}([0-9]{1,4}(?:\.[0-9]{1,3})?)",
            ],
            "property_deduction": [
                r"재산\s*기본\s*공제[^0-9]{0,20}([0-9,]{5,})\s*원",
                r"기본공제[^0-9]{0,20}([0-9,]{5,})\s*원",
            ],
        },
        "keywords": {
            "health_rate": ["건강보험", "보험료율", "%"],
            "ltc_rate": ["장기요양", "건강보험료의", "%"],
            "point_value": ["점수당", "금액", "원"],
            "policy_change": ["재산", "공제", "자동차", "부과"],
            "income_rule": ["11월", "12월", "소득", "반영"],
        },
    }


def _load_parser_config(*, strict: bool = False) -> dict[str, Any]:
    defaults = _default_parser_config()
    if not _PARSER_CONFIG_PATH.exists():
        if strict:
            raise NhisRatesFetchError("parser_config_missing")
        return defaults
    try:
        payload = json.loads(_PARSER_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        if strict:
            raise NhisRatesFetchError("parser_config_invalid_json")
        return defaults
    if not isinstance(payload, dict):
        if strict:
            raise NhisRatesFetchError("parser_config_invalid_shape")
        return defaults

    cfg = defaults
    for top_key in ("url_candidates", "patterns", "keywords"):
        section = payload.get(top_key)
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, list):
                cleaned = [str(item).strip() for item in value if str(item).strip()]
                if cleaned:
                    cfg[top_key][str(key)] = cleaned
    return cfg


def _cfg_list(cfg: dict[str, Any], section: str, key: str, fallback: list[str]) -> list[str]:
    section_payload = cfg.get(section)
    if not isinstance(section_payload, dict):
        return fallback
    values = section_payload.get(key)
    if not isinstance(values, list):
        return fallback
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    return cleaned or fallback


def _extract_percent(text: str, patterns: list[str]) -> Decimal | None:
    if not text:
        return None
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        v = (m.group(1) or "").replace(",", "").strip()
        if not v:
            continue
        try:
            pct = Decimal(v)
            if pct <= 0:
                continue
            return (pct / Decimal("100")).quantize(Decimal("0.000001"))
        except (InvalidOperation, ValueError):
            continue
    return None


def _extract_number(text: str, patterns: list[str]) -> Decimal | None:
    if not text:
        return None
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        v = (m.group(1) or "").replace(",", "").strip()
        if not v:
            continue
        try:
            num = Decimal(v)
            if num <= 0:
                continue
            return num
        except (InvalidOperation, ValueError):
            continue
    return None


def _extract_percent_candidates(text: str) -> list[Decimal]:
    out: list[Decimal] = []
    if not text:
        return out
    for raw in re.findall(r"([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%", text or "", flags=re.IGNORECASE):
        try:
            pct = Decimal(str(raw).replace(",", "").strip()) / Decimal("100")
        except (InvalidOperation, ValueError):
            continue
        if pct > 0:
            out.append(pct)
    return out


def _pick_percent_in_range(text: str, *, min_v: str, max_v: str) -> Decimal | None:
    lo = _as_decimal(min_v, min_v)
    hi = _as_decimal(max_v, max_v)
    candidates = _extract_percent_candidates(text)
    for pct in sorted(candidates, reverse=True):
        if lo <= pct <= hi:
            return pct.quantize(Decimal("0.000001"))
    return None


def _extract_year(texts: list[str], fallback_year: int) -> int:
    years: list[int] = []
    for t in texts:
        for y in re.findall(r"(20[0-9]{2})", t or ""):
            try:
                years.append(int(y))
            except Exception:
                continue
    if not years:
        return int(fallback_year)
    cur = _default_year()
    bounded = [y for y in years if 2000 <= y <= cur + 1]
    if not bounded:
        return int(fallback_year)
    return int(max(bounded))


def _has_any_keyword(text: str, keywords: list[str]) -> bool:
    t = str(text or "").lower()
    if not t:
        return False
    for kw in keywords:
        if str(kw or "").strip().lower() in t:
            return True
    return False


def _fetch_text(url: str, timeout: int = 8) -> tuple[str, int]:
    headers = {
        "User-Agent": "SafeToSpend/1.0 (+nhis-rates-fetcher)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = requests.get(url, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return (resp.text or ""), int(resp.status_code or 200)


def fetch_sources(timeout: int = 8) -> tuple[dict[str, Any], dict[str, Any], int]:
    defaults = _default_values()
    parser_cfg = _load_parser_config(strict=True)
    default_health_rate = str(defaults["health_insurance_rate"])
    default_ltc_ratio = str(defaults["long_term_care_ratio_of_health"])
    default_ltc_optional = str(defaults["long_term_care_rate_optional"])
    default_point_value = str(defaults["regional_point_value"])
    default_property_deduction = int(defaults["property_basic_deduction_krw"])
    now_iso = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    pages: dict[str, str] = {}
    sources: dict[str, Any] = {}
    ok_count = 0
    format_warnings: list[str] = []

    url_candidates = {
        "health_rate": _cfg_list(
            parser_cfg, "url_candidates", "health_rate", [MOHW_HEALTH_RATE_URL, MOHW_HEALTH_RATE_URL_OLD]
        ),
        "ltc_rate": _cfg_list(parser_cfg, "url_candidates", "ltc_rate", [MOHW_LTC_RATE_URL, MOHW_LTC_RATE_URL_OLD]),
        "point_value": _cfg_list(parser_cfg, "url_candidates", "point_value", [EASYLAW_POINT_VALUE_URL]),
        "policy_change": _cfg_list(
            parser_cfg, "url_candidates", "policy_change", [MOHW_POLICY_CHANGE_URL, KOREA_KR_POLICY_URL]
        ),
        "income_rule": _cfg_list(parser_cfg, "url_candidates", "income_rule", [LAW_GO_KR_INCOME_RULE_URL]),
    }

    for key, urls in url_candidates.items():
        fetched = False
        tried: list[dict[str, Any]] = []
        for url in urls:
            official_allowed = bool(is_official_url(url))
            if not official_allowed:
                tried.append({"url": url, "error": "domain_not_allowed"})
                continue
            try:
                text, status_code = _fetch_text(url, timeout=timeout)
                pages[key] = text
                ok_count += 1
                sources[key] = {
                    "url": url,
                    "ok": True,
                    "official_source_allowed": True,
                    "status_code": status_code,
                    "fetched_at": now_iso,
                    "tried_urls": tried,
                }
                fetched = True
                break
            except Exception as e:
                tried.append({"url": url, "error": f"{type(e).__name__}"})
        if not fetched:
            default_url = str(urls[0] if urls else "")
            official_allowed = bool(is_official_url(default_url))
            if not official_allowed:
                format_warnings.append(f"{key}_official_source_unavailable")
            pages[key] = ""
            sources[key] = {
                "url": default_url,
                "ok": False,
                "error": "all_sources_failed",
                "official_source_allowed": official_allowed,
                "tried_urls": tried,
                "fetched_at": now_iso,
            }

    if ok_count <= 0:
        raise NhisRatesFetchError("공식 기준 데이터를 가져오지 못했어요.")

    health_rate_raw = _extract_percent(
        pages.get("health_rate", ""),
        _cfg_list(
            parser_cfg,
            "patterns",
            "health_rate",
            [
                r"건강보험(?:료)?율[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
                r"보험료율[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
            ],
        ),
    )
    health_parse_mode = "pattern"
    health_rate = health_rate_raw
    if health_rate is None:
        fallback_health = _pick_percent_in_range(pages.get("health_rate", ""), min_v="0.03", max_v="0.20")
        if fallback_health is not None:
            health_rate = fallback_health
            health_parse_mode = "fallback_percent_scan"
        else:
            health_rate = defaults["health_insurance_rate"]
            health_parse_mode = "default"
    if not _has_any_keyword(
        pages.get("health_rate", ""),
        _cfg_list(parser_cfg, "keywords", "health_rate", ["건강보험", "보험료율", "%"]),
    ):
        format_warnings.append("health_rate_page_structure_changed")

    ltc_ratio_raw = _extract_percent(
        pages.get("ltc_rate", ""),
        _cfg_list(
            parser_cfg,
            "patterns",
            "ltc_ratio",
            [r"건강보험료의[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%"],
        ),
    )
    ltc_optional_raw = _extract_percent(
        pages.get("ltc_rate", ""),
        _cfg_list(
            parser_cfg,
            "patterns",
            "ltc_optional",
            [
                r"장기요양(?:보험)?(?:료)?(?:율)?[^0-9]{0,30}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
                r"요양보험료율[^0-9]{0,20}([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%",
            ],
        ),
    )
    if not _has_any_keyword(
        pages.get("ltc_rate", ""),
        _cfg_list(parser_cfg, "keywords", "ltc_rate", ["장기요양", "건강보험료의", "%"]),
    ):
        format_warnings.append("ltc_rate_page_structure_changed")

    # 공식 문구 구조가 바뀌어도 터무니없는 수치가 저장되지 않도록 안전 범위를 둔다.
    health_rate_dec = _clamp_decimal(health_rate, fallback=default_health_rate, min_v="0.03", max_v="0.20")
    ratio = _as_decimal(ltc_ratio_raw, str(defaults["long_term_care_ratio_of_health"]))
    optional = _as_decimal(ltc_optional_raw, "0")
    ltc_parse_mode = "direct_ratio"

    if ltc_ratio_raw is None:
        fallback_ratio = _pick_percent_in_range(pages.get("ltc_rate", ""), min_v="0.05", max_v="0.50")
        if fallback_ratio is not None:
            ratio = fallback_ratio
            ltc_parse_mode = "fallback_ratio_scan"
        elif ltc_optional_raw is None:
            fallback_optional = _pick_percent_in_range(pages.get("ltc_rate", ""), min_v="0.001", max_v="0.05")
            if fallback_optional is not None:
                optional = fallback_optional
                ltc_parse_mode = "fallback_optional_scan"

    # 장기요양 값 파싱 보정:
    # - page에 "건강보험료의 13.14%"가 없고 "0.9448%"만 있으면 ratio를 환산해서 사용
    # - 반대로 ratio 위치에 optional이 잘못 잡힌 경우(<=3%)도 환산
    if ltc_ratio_raw is None:
        if optional > 0 and optional < Decimal("0.03") and health_rate_dec > 0:
            ratio = (optional / health_rate_dec).quantize(Decimal("0.000001"))
            ltc_parse_mode = "derived_ratio_from_optional"
        elif optional > 0:
            ratio = optional
            optional = Decimal("0")
            ltc_parse_mode = "ratio_from_optional_slot"
    elif ratio > 0 and ratio < Decimal("0.03") and optional <= 0 and health_rate_dec > 0:
        optional = ratio
        ratio = (optional / health_rate_dec).quantize(Decimal("0.000001"))
        ltc_parse_mode = "ratio_fixed_from_small_value"

    if ratio <= 0 or ratio > Decimal("0.5"):
        ratio = _as_decimal(defaults["long_term_care_ratio_of_health"], default_ltc_ratio)
        ltc_parse_mode = "fallback_ratio_default"
    ratio = _clamp_decimal(ratio, fallback=default_ltc_ratio, min_v="0.01", max_v="0.5")

    if optional <= 0 or optional > Decimal("0.05"):
        optional = (health_rate_dec * ratio).quantize(Decimal("0.000001"))
    optional = _clamp_decimal(optional, fallback=default_ltc_optional, min_v="0.0001", max_v="0.05")

    point_value = _extract_number(
        pages.get("point_value", ""),
        _cfg_list(
            parser_cfg,
            "patterns",
            "point_value",
            [
                r"점수당\s*금액[^0-9]{0,20}([0-9]{1,4}(?:\.[0-9]{1,3})?)\s*원",
                r"점수당금액[^0-9]{0,20}([0-9]{1,4}(?:\.[0-9]{1,3})?)",
            ],
        ),
    ) or defaults["regional_point_value"]
    if not _has_any_keyword(
        pages.get("point_value", ""),
        _cfg_list(parser_cfg, "keywords", "point_value", ["점수당", "금액", "원"]),
    ):
        format_warnings.append("point_value_page_structure_changed")
    point_value = _clamp_decimal(point_value, fallback=default_point_value, min_v="1", max_v="5000")

    policy_text = pages.get("policy_change", "")
    property_deduction = default_property_deduction
    if re.search(r"1억\s*원", policy_text):
        property_deduction = default_property_deduction
    else:
        raw_property = _extract_number(
            policy_text,
            _cfg_list(
                parser_cfg,
                "patterns",
                "property_deduction",
                [
                    r"재산\s*기본\s*공제[^0-9]{0,20}([0-9,]{5,})\s*원",
                    r"기본공제[^0-9]{0,20}([0-9,]{5,})\s*원",
                ],
            ),
        )
        if raw_property and raw_property > 0:
            property_deduction = int(raw_property)
    if property_deduction < 0:
        property_deduction = 0

    car_premium_enabled = not bool(re.search(r"자동차[^<]{0,20}부과[^<]{0,20}폐지", policy_text))
    if not _has_any_keyword(
        policy_text,
        _cfg_list(parser_cfg, "keywords", "policy_change", ["재산", "공제", "자동차", "부과"]),
    ):
        format_warnings.append("policy_page_structure_changed")

    income_reference_rule = defaults["income_reference_rule"]
    income_text = pages.get("income_rule", "")
    if re.search(r"1\s*월.*10\s*월", income_text) and re.search(r"11\s*월.*12\s*월", income_text):
        income_reference_rule = defaults["income_reference_rule"]
    if not _has_any_keyword(
        income_text,
        _cfg_list(parser_cfg, "keywords", "income_rule", ["11월", "12월", "소득", "반영"]),
    ):
        format_warnings.append("income_rule_page_structure_changed")

    effective_year = _extract_year(
        [
            pages.get("health_rate", ""),
            pages.get("ltc_rate", ""),
            pages.get("point_value", ""),
            policy_text,
        ],
        fallback_year=int(defaults["effective_year"]),
    )

    values = {
        "effective_year": int(effective_year),
        "health_insurance_rate": health_rate_dec,
        "long_term_care_ratio_of_health": ratio,
        "long_term_care_rate_optional": optional,
        "regional_point_value": _as_decimal(point_value, default_point_value),
        "property_basic_deduction_krw": int(property_deduction),
        "car_premium_enabled": bool(car_premium_enabled),
        "income_reference_rule": str(income_reference_rule),
    }
    sources["resolved_values"] = {
        "effective_year": int(values["effective_year"]),
        "health_insurance_rate": str(values["health_insurance_rate"]),
        "health_rate_parse_mode": health_parse_mode,
        "long_term_care_ratio_of_health": str(values["long_term_care_ratio_of_health"]),
        "long_term_care_rate_optional": str(values["long_term_care_rate_optional"]),
        "ltc_parse_mode": ltc_parse_mode,
        "regional_point_value": str(values["regional_point_value"]),
        "property_basic_deduction_krw": int(values["property_basic_deduction_krw"]),
        "car_premium_enabled": bool(values["car_premium_enabled"]),
        "income_reference_rule": values["income_reference_rule"],
        "resolved_at": now_iso,
    }
    sources["parser_config"] = {
        "path": str(_PARSER_CONFIG_PATH),
        "loaded": bool(_PARSER_CONFIG_PATH.exists()),
    }
    sources["format_warnings"] = format_warnings
    sources["format_warning_count"] = len(format_warnings)
    sources["format_drift_detected"] = bool(len(format_warnings) > 0)
    return values, sources, ok_count


def upsert_snapshot(
    *,
    effective_year: int,
    values: dict[str, Any],
    sources_json: dict[str, Any],
    activate: bool = True,
) -> NhisRateSnapshot:
    defaults = _default_values(effective_year=int(effective_year))
    row = NhisRateSnapshot.query.filter_by(effective_year=int(effective_year)).first()
    if not row:
        row = NhisRateSnapshot(effective_year=int(effective_year))
        db.session.add(row)

    row.health_insurance_rate = _as_decimal(values.get("health_insurance_rate"), str(defaults["health_insurance_rate"]))
    row.long_term_care_ratio_of_health = _as_decimal(
        values.get("long_term_care_ratio_of_health"),
        str(defaults["long_term_care_ratio_of_health"]),
    )
    row.long_term_care_rate_optional = _as_decimal(
        values.get("long_term_care_rate_optional"),
        str(defaults["long_term_care_rate_optional"]),
    )
    row.regional_point_value = _as_decimal(values.get("regional_point_value"), str(defaults["regional_point_value"]))
    row.property_basic_deduction_krw = int(
        values.get("property_basic_deduction_krw") or defaults["property_basic_deduction_krw"]
    )
    row.car_premium_enabled = bool(values.get("car_premium_enabled", False))
    row.income_reference_rule = str(values.get("income_reference_rule") or _default_values()["income_reference_rule"])
    row.sources_json = dict(sources_json or {})
    row.fetched_at = utcnow()
    row.is_active = bool(activate)
    row.updated_at = utcnow()

    if activate:
        (
            db.session.query(NhisRateSnapshot)
            .filter(NhisRateSnapshot.id != row.id)
            .update({NhisRateSnapshot.is_active: False}, synchronize_session=False)
        )

    db.session.add(row)
    db.session.commit()
    return row


def get_active_snapshot() -> NhisRateSnapshot | None:
    row = (
        NhisRateSnapshot.query.filter_by(is_active=True)
        .order_by(NhisRateSnapshot.effective_year.desc(), NhisRateSnapshot.fetched_at.desc())
        .first()
    )
    if row:
        return row
    return (
        NhisRateSnapshot.query.order_by(NhisRateSnapshot.effective_year.desc(), NhisRateSnapshot.fetched_at.desc())
        .first()
    )


def refresh_nhis_rates(timeout: int = 8) -> NhisRateSnapshot:
    values, sources_json, ok_count = fetch_sources(timeout=timeout)
    if ok_count <= 0:
        raise NhisRatesFetchError("공식 기준 데이터 수집에 실패했어요.")
    return upsert_snapshot(
        effective_year=int(values["effective_year"]),
        values=values,
        sources_json=sources_json,
        activate=True,
    )


def _can_try_network_refresh(now: datetime, *, force: bool = False) -> bool:
    if force:
        return True
    global _LAST_REFRESH_ATTEMPT_AT
    if _LAST_REFRESH_ATTEMPT_AT is None:
        return True
    elapsed = (now - _LAST_REFRESH_ATTEMPT_AT).total_seconds()
    return elapsed >= max(60, NHIS_REFRESH_RETRY_SECONDS)


def ensure_active_snapshot(
    *,
    refresh_if_stale_days: int = 30,
    refresh_timeout: int = 8,
    force_refresh: bool = False,
) -> NhisSnapshotStatus:
    # Phase 1 hard gate:
    # 런타임 요청 경로에서는 외부 refresh/부트스트랩 쓰기를 절대 수행하지 않는다.
    _ = refresh_timeout
    _ = force_refresh

    row = get_active_snapshot()
    now = utcnow()

    if row is None:
        return NhisSnapshotStatus(
            snapshot=None,
            update_error="snapshot_missing",
            is_stale=True,
            is_fallback_default=False,
        )

    stale_days = max(1, int(refresh_if_stale_days))
    fetched_at = getattr(row, "fetched_at", None)
    if not isinstance(fetched_at, datetime):
        fetched_at = now
    is_stale = bool((now - fetched_at) > timedelta(days=stale_days))
    is_fallback_default = bool(
        isinstance(row.sources_json, dict)
        and isinstance(row.sources_json.get("bootstrap"), dict)
        and row.sources_json.get("bootstrap", {}).get("source") == "built_in_default"
    )

    return NhisSnapshotStatus(
        snapshot=row,
        update_error=None,
        is_stale=bool(is_stale),
        is_fallback_default=bool(is_fallback_default),
    )


def snapshot_to_display_dict(snapshot: NhisRateSnapshot | None) -> dict[str, Any]:
    if not snapshot:
        defaults = _default_values()
        return {
            "effective_year": int(defaults["effective_year"]),
            "health_insurance_rate": float(defaults["health_insurance_rate"]),
            "long_term_care_ratio_of_health": float(defaults["long_term_care_ratio_of_health"]),
            "long_term_care_rate_optional": float(defaults["long_term_care_rate_optional"]),
            "regional_point_value": float(defaults["regional_point_value"]),
            "property_basic_deduction_krw": int(defaults["property_basic_deduction_krw"]),
            "car_premium_enabled": bool(defaults["car_premium_enabled"]),
            "income_reference_rule": str(defaults["income_reference_rule"]),
            "fetched_at": None,
            "sources_json": {},
        }
    return {
        "effective_year": int(snapshot.effective_year or _default_year()),
        "health_insurance_rate": float(snapshot.health_insurance_rate or 0),
        "long_term_care_ratio_of_health": float(snapshot.long_term_care_ratio_of_health or 0),
        "long_term_care_rate_optional": (
            float(snapshot.long_term_care_rate_optional) if snapshot.long_term_care_rate_optional is not None else None
        ),
        "regional_point_value": float(snapshot.regional_point_value or 0),
        "property_basic_deduction_krw": int(snapshot.property_basic_deduction_krw or 0),
        "car_premium_enabled": bool(snapshot.car_premium_enabled),
        "income_reference_rule": str(snapshot.income_reference_rule or ""),
        "fetched_at": snapshot.fetched_at,
        "sources_json": snapshot.sources_json if isinstance(snapshot.sources_json, dict) else {},
    }

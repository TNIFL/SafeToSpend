from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from core.time import utcnow
from services.nhis_reference import get_official_defaults, resolve_default_year
from services.nhis_rates import snapshot_to_display_dict


@dataclass(frozen=True)
class NhisRules:
    target_month: str
    effective_year: int
    health_insurance_rate: float
    regional_point_value: float
    ltc_ratio_of_health: float
    long_term_care_rate_optional: float
    monthly_floor_krw: int
    monthly_cap_krw: int
    property_basic_deduction_krw: int
    financial_income_threshold_krw: int
    property_points_table: tuple[tuple[int, float], ...]
    property_points_table_loaded: bool
    property_points_table_version: str
    income_min_annual_krw: int
    income_min_monthly_krw: int
    health_premium_floor_krw: int
    health_premium_cap_krw: int
    rent_eval_multiplier: float
    rent_month_to_deposit_multiplier: int
    employee_share_ratio: float
    car_points_enabled: bool
    income_reference_rule: str
    reference_last_checked_date: str
    source_urls: dict[str, str]
    sources: tuple[str, ...]
    fetched_at_text: str
    rules_version: str
    used_snapshot_fallback: bool
    effective_date: str

    @property
    def insurance_rate(self) -> float:
        return float(self.health_insurance_rate)

    @property
    def point_value(self) -> float:
        return float(self.regional_point_value)

    @property
    def ltc_rate_optional(self) -> float:
        return float(self.long_term_care_rate_optional)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _REPO_ROOT / "data"
_DEFAULT_PROPERTY_TABLE_YEAR = 2026

_SOURCE_URLS = {
    "health_rate_and_point_value": "https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28",
    "income_property_table": "https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=",
    "income_property_table_alt": "https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493",
    "rent_eval_rule": "https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=",
    "income_eval_rule": "https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846",
    "car_policy": "https://www.korea.kr/docViewer/result/2024.01/05/a84f92d76bfce4dc951e5b694131516e/a84f92d76bfce4dc951e5b694131516e.view.xhtml",
    "cycle_reference": "https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493",
    "ltc_ratio": "https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200",
    "official_calculator": "https://www.nhis.or.kr/nhis/minwon/initCtrbCalcView.do",
}

def _now_month_key() -> str:
    return utcnow().strftime("%Y-%m")


def parse_month_key(raw: Any) -> str:
    s = str(raw or "").strip()
    if len(s) == 7 and s[4] == "-":
        try:
            y = int(s[:4])
            m = int(s[5:7])
            if 2000 <= y <= 2100 and 1 <= m <= 12:
                return f"{y:04d}-{m:02d}"
        except Exception:
            pass
    return _now_month_key()


def month_cycle_info(target_month: str) -> dict[str, int]:
    month_key = parse_month_key(target_month)
    year = int(month_key[:4])
    month = int(month_key[5:7])
    cycle_start_year = year if month >= 11 else (year - 1)
    return {
        "target_year": year,
        "target_month": month,
        "cycle_start_year": cycle_start_year,
        "income_year_applied": cycle_start_year - 1,
        "property_year_applied": cycle_start_year,
    }


def _load_property_points_table(effective_year: int) -> tuple[tuple[tuple[int, float], ...], bool, str]:
    candidates = [
        _DATA_DIR / f"nhis_property_points_{int(effective_year)}.json",
        _DATA_DIR / f"nhis_property_points_{_DEFAULT_PROPERTY_TABLE_YEAR}.json",
    ]
    for path in candidates:
        try:
            if not path.exists():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw.get("rows")
            if not isinstance(rows, list) or not rows:
                continue
            table: list[tuple[int, float]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                upper = int(row.get("upper_krw") or 0)
                points = float(row.get("points") or 0.0)
                if upper <= 0 or points < 0:
                    continue
                table.append((upper, points))
            table.sort(key=lambda x: x[0])
            if table:
                version = str(raw.get("version") or path.name)
                return tuple(table), True, version
        except Exception:
            continue
    return tuple(), False, "missing_property_points_table"


def property_points_from_amount(net_property_krw: int, rules: NhisRules) -> float | None:
    amount = max(0, int(net_property_krw or 0))
    if amount <= 0:
        return 0.0
    if not rules.property_points_table:
        return None
    for upper, points in rules.property_points_table:
        if amount <= int(upper):
            return float(points)
    # Phase 1 hard gate: 선형 외삽(fallback) 금지. 표 상한 초과 시 마지막 공식 구간 점수를 유지한다.
    _last_upper, last_points = rules.property_points_table[-1]
    return float(last_points)


def _to_fetched_at_text(raw: Any) -> str:
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y-%m-%d %H:%M")
    s = str(raw or "").strip()
    return s or "-"


def get_rules(target_month: str, snapshot_obj: Any | None = None) -> NhisRules:
    month_key = parse_month_key(target_month)
    y = int(month_key[:4])
    m = int(month_key[5:7])
    snap = snapshot_to_display_dict(snapshot_obj)
    default_year = resolve_default_year(y)
    defaults = get_official_defaults(default_year)
    used_snapshot_fallback = snapshot_obj is None

    def _pick_float(key: str, fallback: float) -> float:
        nonlocal used_snapshot_fallback
        try:
            value = float(snap.get(key))
        except Exception:
            value = 0.0
        if value > 0:
            return value
        used_snapshot_fallback = True
        return float(fallback)

    def _pick_int(key: str, fallback: int) -> int:
        nonlocal used_snapshot_fallback
        try:
            value = int(snap.get(key))
        except Exception:
            value = 0
        if value > 0:
            return value
        used_snapshot_fallback = True
        return int(fallback)

    health_insurance_rate = _pick_float("health_insurance_rate", float(defaults["health_insurance_rate"]))
    regional_point_value = _pick_float("regional_point_value", float(defaults["regional_point_value"]))
    ltc_ratio = _pick_float("long_term_care_ratio_of_health", float(defaults["long_term_care_ratio_of_health"]))
    ltc_rate_optional = _pick_float("long_term_care_rate_optional", float(defaults["long_term_care_rate_optional"]))
    property_deduction = _pick_int("property_basic_deduction_krw", int(defaults["property_basic_deduction_krw"]))
    financial_income_threshold = int(defaults.get("financial_income_threshold_krw") or 0)
    employee_share_ratio = max(0.0, min(1.0, float(defaults.get("employee_share_ratio") or 0.5)))
    reference_last_checked = str(defaults.get("last_checked_date") or "-")

    income_rule = str(snap.get("income_reference_rule") or "").strip()
    if not income_rule:
        used_snapshot_fallback = True
        income_rule = str(defaults["income_reference_rule"])

    # 제도 변경 시점 반영
    current_day = date(y, m, 1)
    car_disabled_after = date(2024, 2, 13)
    car_points_enabled = bool(snap.get("car_premium_enabled"))
    if current_day >= car_disabled_after:
        car_points_enabled = False

    try:
        effective_year_raw = int(snap.get("effective_year") or 0)
    except Exception:
        effective_year_raw = 0
    effective_year = effective_year_raw if effective_year_raw > 0 else int(default_year)
    if effective_year_raw <= 0:
        used_snapshot_fallback = True

    property_points_table, table_loaded, table_version = _load_property_points_table(effective_year=effective_year)
    if not table_loaded:
        used_snapshot_fallback = True

    fetched_at_text = _to_fetched_at_text(snap.get("fetched_at"))
    monthly_floor_krw = int(defaults["monthly_floor_krw"])
    monthly_cap_krw = int(defaults["monthly_cap_krw"])
    rules_version = f"nhis-{effective_year}-{'snapshot' if not used_snapshot_fallback else 'fallback'}"
    source_urls = dict(_SOURCE_URLS)
    source_values = tuple(source_urls.values())
    source_meta = defaults.get("sources")
    if isinstance(source_meta, dict) and source_meta:
        flattened_urls: list[str] = []
        merged_urls: dict[str, str] = {}
        for key, urls in source_meta.items():
            if isinstance(urls, (list, tuple)):
                cleaned = [str(u).strip() for u in urls if str(u).strip()]
            else:
                one = str(urls).strip()
                cleaned = [one] if one else []
            if cleaned:
                merged_urls[str(key)] = cleaned[0]
                flattened_urls.extend(cleaned)
        if merged_urls:
            source_urls = merged_urls
        if flattened_urls:
            source_values = tuple(dict.fromkeys(flattened_urls))
    effective_date = str(defaults.get("effective_from_date") or datetime(y, m, 1).strftime("%Y-%m-%d"))

    return NhisRules(
        target_month=month_key,
        effective_year=int(effective_year),
        health_insurance_rate=float(max(0.0, health_insurance_rate)),
        regional_point_value=float(max(0.0, regional_point_value)),
        ltc_ratio_of_health=float(max(0.0, ltc_ratio)),
        long_term_care_rate_optional=float(max(0.0, ltc_rate_optional)),
        monthly_floor_krw=monthly_floor_krw,
        monthly_cap_krw=monthly_cap_krw,
        property_basic_deduction_krw=max(0, int(property_deduction)),
        financial_income_threshold_krw=max(0, int(financial_income_threshold)),
        property_points_table=property_points_table,
        property_points_table_loaded=bool(table_loaded),
        property_points_table_version=table_version,
        income_min_annual_krw=3_360_000,
        income_min_monthly_krw=280_000,
        health_premium_floor_krw=monthly_floor_krw,
        health_premium_cap_krw=monthly_cap_krw,
        # 전월세 평가액 = (보증금 + (월세 × 40)) × 0.30
        rent_eval_multiplier=float(defaults.get("rent_eval_multiplier") or 0.30),
        rent_month_to_deposit_multiplier=int(defaults.get("rent_month_to_deposit_multiplier") or 40),
        employee_share_ratio=float(employee_share_ratio),
        car_points_enabled=bool(car_points_enabled),
        income_reference_rule=income_rule,
        reference_last_checked_date=reference_last_checked,
        source_urls=source_urls,
        sources=source_values,
        fetched_at_text=fetched_at_text,
        rules_version=rules_version,
        used_snapshot_fallback=bool(used_snapshot_fallback),
        effective_date=effective_date,
    )


def get_rules_for_month(target_month: str, snapshot_obj: Any | None = None) -> NhisRules:
    # 하위 호환용 alias
    return get_rules(target_month=target_month, snapshot_obj=snapshot_obj)

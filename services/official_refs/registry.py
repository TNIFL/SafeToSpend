from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from services.official_refs.source_policy import OFFICIAL_ALLOWED_DOMAINS
from services.reference.nhis_reference import get_nhis_reference_snapshot
from services.reference.tax_reference import get_tax_reference_snapshot

OFFICIAL_REFERENCE_YEAR = 2026
REGISTRY_VERSION = "official-refs-2026.03.07"
REGISTRY_LAST_REVIEWED_DATE = "2026-03-07"

ALLOWED_OFFICIAL_DOMAINS: tuple[str, ...] = OFFICIAL_ALLOWED_DOMAINS


def _registry_sources() -> dict[str, str]:
    return {
        "nhis_enforcement_decree_article_44": "https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28",
        "nhis_enforcement_decree_article_41": "https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493",
        "nhis_enforcement_rule_article_44": "https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846",
        "nhis_enforcement_rule_annex_8": "https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=",
        "nhis_enforcement_decree_annex_4": "https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135095&gubun=",
        "nhis_premium_floor_ceiling_notice": "https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=2100000270472&chrClsCd=010201",
        "mohw_ltc_2026": "https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200",
        "nhis_act_article_107": "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95",
        "national_treasury_act_article_47": "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EA%B3%A0%EA%B8%88%EA%B4%80%EB%A6%AC%EB%B2%95",
        "income_tax_act_article_55": "https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661",
        "nts_income_tax_table": "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594",
        "local_tax_act": "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95",
    }


def _tax_brackets_rows(target_year: int) -> list[dict[str, Any]]:
    ref = get_tax_reference_snapshot(int(target_year))
    rows: list[dict[str, Any]] = []
    for row in ref.income_tax_brackets:
        rows.append(
            {
                "upper_limit_krw": int(row.upper_limit_krw),
                "rate": float(row.rate),
                "progressive_deduction_krw": int(row.progressive_deduction_krw),
            }
        )
    return rows


def get_official_reference_registry(target_year: int = OFFICIAL_REFERENCE_YEAR) -> dict[str, Any]:
    year = int(target_year)
    nhis = get_nhis_reference_snapshot(year)
    tax = get_tax_reference_snapshot(year)
    sources = _registry_sources()

    return {
        "meta": {
            "registry_version": REGISTRY_VERSION,
            "last_reviewed_date": REGISTRY_LAST_REVIEWED_DATE,
            "target_year": year,
            "allowed_domains": list(ALLOWED_OFFICIAL_DOMAINS),
        },
        "nhis": {
            "effective_from_date": nhis.effective_from_date,
            "health_insurance_rate": float(nhis.health_insurance_rate),
            "property_point_value": float(nhis.property_point_value),
            "ltc_rate_of_income": float(nhis.ltc_rate_of_income),
            "ltc_ratio_of_health": float(nhis.ltc_ratio_of_health),
            "premium_floor_health_only": int(nhis.premium_floor_health_only),
            "premium_ceiling_health_only": int(nhis.premium_ceiling_health_only),
            "property_basic_deduction_krw": int(nhis.property_basic_deduction_krw),
            "financial_income_threshold_krw": int(nhis.financial_income_threshold_krw),
            "financial_income_inclusion_rule": "<= threshold excluded, > threshold included in full",
            "rent_eval_formula": {
                "expression": "(deposit + monthly * 40) * 0.30",
                "monthly_multiplier": int(nhis.rent_month_to_deposit_multiplier),
                "evaluation_multiplier": float(nhis.rent_eval_multiplier),
            },
            "income_cycle_rule": {
                "month_1_to_10": "year-2",
                "month_11_to_12": "year-1",
            },
            "rounding_rule": {
                "premium": "truncate_under_10",
                "legal_basis": [
                    sources["nhis_act_article_107"],
                    sources["national_treasury_act_article_47"],
                ],
            },
            "reform_flags": {
                "car_premium_abolished": bool(nhis.car_premium_abolished),
                "property_deduction_basic_krw": int(nhis.property_basic_deduction_krw),
            },
            "sources": {
                "health_rate_and_point": sources["nhis_enforcement_decree_article_44"],
                "income_cycle": sources["nhis_enforcement_decree_article_41"],
                "financial_income_threshold": sources["nhis_enforcement_rule_article_44"],
                "rent_eval_rule": sources["nhis_enforcement_rule_annex_8"],
                "property_points_table": sources["nhis_enforcement_decree_annex_4"],
                "premium_floor_ceiling": sources["nhis_premium_floor_ceiling_notice"],
                "ltc_ratio": sources["mohw_ltc_2026"],
            },
        },
        "tax": {
            "effective_from_date": tax.effective_from_date,
            "income_tax_brackets": _tax_brackets_rows(year),
            "local_income_tax_ratio": float(tax.local_income_tax_ratio),
            "rounding_rule": {
                "national_income_tax": "round_to_won",
                "local_income_tax": "round_to_won",
            },
            "sources": {
                "income_tax_rate_table": sources["income_tax_act_article_55"],
                "income_tax_reference_table": sources["nts_income_tax_table"],
                "local_income_tax_rule": sources["local_tax_act"],
            },
        },
    }


def get_registry_hash(target_year: int = OFFICIAL_REFERENCE_YEAR) -> str:
    payload = get_official_reference_registry(target_year=target_year)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_verify_targets(target_year: int = OFFICIAL_REFERENCE_YEAR) -> list[dict[str, Any]]:
    reg = get_official_reference_registry(target_year=target_year)
    src = _registry_sources()
    year = int(target_year)

    return [
        {
            "key": "nhis_rate_and_point_value",
            "url": src["nhis_enforcement_decree_article_44"],
            "required_patterns": [r"1만분의\s*719", r"211(?:\.|,)5\s*원"],
            "expected_values": {
                "health_insurance_rate": reg["nhis"]["health_insurance_rate"],
                "property_point_value": reg["nhis"]["property_point_value"],
            },
            "snapshot_path": str(Path("data/official_snapshots/nhis/nhis_enforcement_decree_article44.html")),
        },
        {
            "key": "nhis_annex4_property_points",
            "url": src["nhis_enforcement_decree_annex_4"],
            "required_patterns": [r"별표\s*4", r"재산"],
            "expected_values": {
                "property_basic_deduction_krw": reg["nhis"]["property_basic_deduction_krw"],
            },
            "snapshot_path": str(Path("data/official_snapshots/nhis/nhis_enforcement_decree_annex4_latest.pdf")),
        },
        {
            "key": "nhis_annex8_rent_formula",
            "url": src["nhis_enforcement_rule_annex_8"],
            "required_patterns": [r"별표\s*8", r"40", r"0\.30|0,30"],
            "expected_values": {
                "rent_month_to_deposit_multiplier": reg["nhis"]["rent_eval_formula"]["monthly_multiplier"],
                "rent_eval_multiplier": reg["nhis"]["rent_eval_formula"]["evaluation_multiplier"],
            },
            "snapshot_path": str(Path("data/official_snapshots/nhis/nhis_enforcement_rule_annex8_latest.pdf")),
        },
        {
            "key": "nhis_financial_income_threshold",
            "url": src["nhis_enforcement_rule_article_44"],
            "required_patterns": [r"1,?000만원", r"이하", r"초과"],
            "expected_values": {
                "financial_income_threshold_krw": reg["nhis"]["financial_income_threshold_krw"],
            },
            "snapshot_path": str(Path("data/official_snapshots/nhis/nhis_enforcement_rule_article44.html")),
        },
        {
            "key": "nhis_floor_ceiling",
            "url": src["nhis_premium_floor_ceiling_notice"],
            "required_patterns": [r"20,?160", r"4,?591,?740"],
            "expected_values": {
                "premium_floor_health_only": reg["nhis"]["premium_floor_health_only"],
                "premium_ceiling_health_only": reg["nhis"]["premium_ceiling_health_only"],
            },
            "snapshot_path": str(Path("data/official_snapshots/nhis/nhis_floor_ceiling_notice.html")),
        },
        {
            "key": "mohw_ltc_ratio",
            "url": src["mohw_ltc_2026"],
            "required_patterns": [r"13\.14%", r"0\.9448%"],
            "expected_values": {
                "ltc_ratio_of_health": reg["nhis"]["ltc_ratio_of_health"],
                "ltc_rate_of_income": reg["nhis"]["ltc_rate_of_income"],
            },
            "snapshot_path": str(Path("data/official_snapshots/mohw/ltc_rate_2026_release.html")),
        },
        {
            "key": "tax_income_rate_table",
            "url": src["nts_income_tax_table"],
            "required_patterns": [r"14,?000,?000", r"1,?260,?000", r"65,?940,?000"],
            "expected_values": {
                "target_year": year,
                "bracket_count": len(reg["tax"]["income_tax_brackets"]),
            },
            "snapshot_path": str(Path("data/official_snapshots/tax/nts_income_tax_table.html")),
        },
        {
            "key": "tax_local_income_ratio",
            "url": src["local_tax_act"],
            "required_patterns": [r"지방소득세", r"10"],
            "expected_values": {
                "local_income_tax_ratio": reg["tax"]["local_income_tax_ratio"],
            },
            "snapshot_path": str(Path("data/official_snapshots/tax/local_tax_act.html")),
        },
    ]

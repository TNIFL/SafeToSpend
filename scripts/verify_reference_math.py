#!/usr/bin/env python3
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.reference.nhis_reference import get_nhis_reference_snapshot
from services.reference.tax_reference import get_tax_reference_snapshot


REPORT_PATH = ROOT / "docs" / "VERIFICATION_REPORT.md"


def _run_unittest(target: str) -> tuple[bool, str]:
    suite = unittest.defaultTestLoader.loadTestsFromName(target)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return bool(result.wasSuccessful()), stream.getvalue()


def _status_mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def main() -> int:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_year = 2026
    nhis_ref = get_nhis_reference_snapshot(target_year)
    tax_ref = get_tax_reference_snapshot(target_year)

    nhis_ok, nhis_log = _run_unittest("tests.test_nhis_reference_rules")
    tax_ok, tax_log = _run_unittest("tests.test_tax_reference_rules")
    all_ok = nhis_ok and tax_ok

    print("[REFERENCE VERIFY]")
    print(f"- NHIS rate: {nhis_ref.health_insurance_rate} / point: {nhis_ref.property_point_value}")
    print(
        f"- NHIS floor/cap: {nhis_ref.premium_floor_health_only:,} / {nhis_ref.premium_ceiling_health_only:,} "
        f"(last_checked={nhis_ref.last_checked_date})"
    )
    print(f"- Tax local ratio: {tax_ref.local_income_tax_ratio} (last_checked={tax_ref.last_checked_date})")
    print(f"- NHIS tests: {_status_mark(nhis_ok)}")
    print(f"- TAX tests: {_status_mark(tax_ok)}")

    lines: list[str] = []
    lines.append("# 검증 리포트")
    lines.append("")
    lines.append(f"- 생성 시각: `{now_text}`")
    lines.append(f"- 기준 연도: `{target_year}`")
    lines.append(f"- NHIS 테스트: **{_status_mark(nhis_ok)}**")
    lines.append(f"- Tax 테스트: **{_status_mark(tax_ok)}**")
    lines.append(f"- 전체 결과: **{_status_mark(all_ok)}**")
    lines.append("")
    lines.append("## NHIS 기준 스냅샷")
    lines.append(f"- 건강보험료율: `{nhis_ref.health_insurance_rate}`")
    lines.append(f"- 재산점수당 금액: `{nhis_ref.property_point_value}`")
    lines.append(f"- 장기요양(소득 대비): `{nhis_ref.ltc_rate_of_income}`")
    lines.append(f"- 장기요양(건강보험료 대비): `{nhis_ref.ltc_ratio_of_health}`")
    lines.append(
        f"- 월 하한/상한(건강보험료): `{nhis_ref.premium_floor_health_only:,}` / `{nhis_ref.premium_ceiling_health_only:,}`"
    )
    lines.append(f"- 재산 기본공제: `{nhis_ref.property_basic_deduction_krw:,}`")
    lines.append(
        "- 전월세 공식: "
        f"`[보증금 + (월세 * {nhis_ref.rent_month_to_deposit_multiplier})] * {nhis_ref.rent_eval_multiplier}`"
    )
    lines.append(f"- 금융소득 임계: `<= {nhis_ref.financial_income_threshold_krw:,}원 제외, 초과 시 전액 합산`")
    lines.append(f"- 마지막 확인일: `{nhis_ref.last_checked_date}`")
    lines.append("")
    lines.append("### NHIS 소스")
    for key, urls in (nhis_ref.sources or {}).items():
        lines.append(f"- {key}:")
        for url in urls:
            lines.append(f"  - {url}")
    lines.append("")
    lines.append("## 세금 기준 스냅샷")
    lines.append(f"- 지방소득세 비율: `{tax_ref.local_income_tax_ratio}`")
    lines.append(f"- 마지막 확인일: `{tax_ref.last_checked_date}`")
    lines.append("- 누진표:")
    for idx, b in enumerate(tax_ref.income_tax_brackets, start=1):
        lines.append(
            f"  - {idx}. 상한 `{b.upper_limit_krw:,}` / 세율 `{b.rate}` / 누진공제 `{b.progressive_deduction_krw:,}`"
        )
    lines.append("")
    lines.append("### 세금 소스")
    for key, urls in (tax_ref.sources or {}).items():
        lines.append(f"- {key}:")
        for url in urls:
            lines.append(f"  - {url}")
    lines.append("")
    lines.append("## 테스트 로그 요약")
    lines.append("")
    lines.append("### tests.test_nhis_reference_rules")
    lines.append("```text")
    lines.append(nhis_log.strip() or "(no output)")
    lines.append("```")
    lines.append("")
    lines.append("### tests.test_tax_reference_rules")
    lines.append("```text")
    lines.append(tax_log.strip() or "(no output)")
    lines.append("```")
    lines.append("")
    if not all_ok:
        lines.append("## 실패 요약")
        if not nhis_ok:
            lines.append("- NHIS 테스트 실패: 상수/규칙/엔진 계산 중 최소 1개 불일치")
        if not tax_ok:
            lines.append("- Tax 테스트 실패: 누진세 또는 지방소득세 계산 불일치")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"- report: {REPORT_PATH}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

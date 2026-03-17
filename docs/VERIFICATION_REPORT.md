# 검증 리포트

- 생성 시각: `2026-03-07 04:50:22`
- 기준 연도: `2026`
- NHIS 테스트: **PASS**
- Tax 테스트: **PASS**
- 전체 결과: **PASS**

## NHIS 기준 스냅샷
- 건강보험료율: `0.0719`
- 재산점수당 금액: `211.5`
- 장기요양(소득 대비): `0.009448`
- 장기요양(건강보험료 대비): `0.1314`
- 월 하한/상한(건강보험료): `20,160` / `4,591,740`
- 재산 기본공제: `100,000,000`
- 전월세 공식: `[보증금 + (월세 * 40)] * 0.30`
- 금융소득 임계: `<= 10,000,000원 제외, 초과 시 전액 합산`
- 마지막 확인일: `2026-03-06`

### NHIS 소스
- health_rate_and_point_value:
  - https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
- ltc_rate:
  - https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200
- premium_floor_ceiling:
  - https://www.law.go.kr/LSW//admRulInfoP.do?admRulSeq=2100000270472&chrClsCd=010201
- income_cycle_reference:
  - https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493
- financial_income_rule:
  - https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846
- rent_eval_rule:
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=
- reform_2024_02:
  - https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95%EC%8B%9C%ED%96%89%EB%A0%B9
  - https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95%EC%8B%9C%ED%96%89%EA%B7%9C%EC%B9%99

## 세금 기준 스냅샷
- 지방소득세 비율: `0.10`
- 마지막 확인일: `2026-03-06`
- 누진표:
  - 1. 상한 `14,000,000` / 세율 `0.06` / 누진공제 `0`
  - 2. 상한 `50,000,000` / 세율 `0.15` / 누진공제 `1,260,000`
  - 3. 상한 `88,000,000` / 세율 `0.24` / 누진공제 `5,760,000`
  - 4. 상한 `150,000,000` / 세율 `0.35` / 누진공제 `15,440,000`
  - 5. 상한 `300,000,000` / 세율 `0.38` / 누진공제 `19,940,000`
  - 6. 상한 `500,000,000` / 세율 `0.40` / 누진공제 `25,940,000`
  - 7. 상한 `1,000,000,000` / 세율 `0.42` / 누진공제 `35,940,000`
  - 8. 상한 `1,000,000,000,000,000,000` / 세율 `0.45` / 누진공제 `65,940,000`

### 세금 소스
- income_tax_law:
  - https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661
- nts_rate_table:
  - https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594
- local_income_tax_ratio:
  - https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95

## 테스트 로그 요약

### tests.test_nhis_reference_rules
```text
test_employee_case_exact (tests.test_nhis_reference_rules.NhisReferenceRulesTest.test_employee_case_exact) ... ok
test_financial_income_threshold_branch (tests.test_nhis_reference_rules.NhisReferenceRulesTest.test_financial_income_threshold_branch) ... ok
test_income_cycle_reference_exact (tests.test_nhis_reference_rules.NhisReferenceRulesTest.test_income_cycle_reference_exact) ... ok
test_reference_constants_exact (tests.test_nhis_reference_rules.NhisReferenceRulesTest.test_reference_constants_exact) ... ok
test_rent_eval_formula_exact (tests.test_nhis_reference_rules.NhisReferenceRulesTest.test_rent_eval_formula_exact) ... ok
test_rules_constants_exact (tests.test_nhis_reference_rules.NhisReferenceRulesTest.test_rules_constants_exact) ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.003s

OK
```

### tests.test_tax_reference_rules
```text
test_local_tax_ratio_exact (tests.test_tax_reference_rules.TaxReferenceRulesTest.test_local_tax_ratio_exact) ... ok
test_national_tax_vectors_exact (tests.test_tax_reference_rules.TaxReferenceRulesTest.test_national_tax_vectors_exact) ... ok
test_reference_constants_exact (tests.test_tax_reference_rules.TaxReferenceRulesTest.test_reference_constants_exact) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.001s

OK
```


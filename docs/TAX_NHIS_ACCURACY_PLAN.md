# TAX/NHIS 99% 정확도 보강 계획 (Ticket 1)

- 작성일: 2026-03-12 (KST)
- 기준 코드: 현재 워크스페이스 HEAD
- 범위: 병목 진단 전용(티켓1 기준 문서, 이후 구현 반영 사항은 하단 최신 스냅샷 참고)
- 제외: `.env`, `.git`, `.venv`, `.uploads`

---

## 0) 최신 실행 스냅샷 (2026-03-12)

- 근거:
  - `reports/accuracy_level_audit_latest.json` (실사용자 97명)
  - `docs/TAX_NHIS_99_ACCURACY_REPORT.md`
- 현재 분포:
  - 세금: `blocked 95 (97.94%)`, `limited 2 (2.06%)`, `exact/high 0%`
  - 건보: `blocked 97 (100.00%)`, `exact/high/limited 0%`
- 현재 1순위 병목:
  - 세금: `missing_taxable_income`
  - 건보: `missing_snapshot`

---

## 1) 세금 정확도 병목 분류

### 1-0. 세금 reason taxonomy (표준 코드)
- `ok`
- `estimate_unavailable`
- `missing_taxable_income`
- `missing_income_classification`
- `missing_withheld_tax`
- `missing_prepaid_tax`
- `proxy_from_annual_income`
- `insufficient_profile_inputs`

legacy alias 정규화:
- `missing_official_taxable_income` -> `proxy_from_annual_income`
- `limited_proxy` -> `insufficient_profile_inputs`
- `unknown` -> `insufficient_profile_inputs`

### 1-1. 공식/제한/차단 분기(코드 기준)
- `official_exact`
  - 위치: `services/risk.py: compute_tax_estimate`
  - 조건:
    - `official_input_satisfied == True`
    - `compute_tax_official_core(...).calculable == True`
  - 핵심 입력:
    - `official_taxable_income_annual_krw` (또는 alias)
- `limited_proxy`
  - 위치: `services/risk.py: compute_tax_estimate`
  - 조건:
    - `official_input_satisfied == False`
    - `taxable_income_used_annual_krw > 0` (income override 또는 월 이익 연환산 proxy)
    - `compute_tax_official_core(...).calculable == True`
- `blocked`
  - 위치: `services/risk.py: compute_tax_estimate`
  - 조건:
    - `taxable_income_used_annual_krw <= 0` 또는 공식 코어 계산 불가
  - 결과:
    - 월 예상세액/지방세/원천징수 차감이 0 또는 최소값에 수렴

### 1-2. 병목 원인(카테고리별)
| 카테고리 | 코드 병목 | 정확도 영향 |
|---|---|---|
| 1) 입력 부족 | `official_taxable_income_annual_krw` 부재 시 `limited_proxy/blocked` 전환 | 과소추정 또는 0원 위험 |
| 2) 저장/정규화 누락 | 세액 차감 관련 입력(기납부·중간예납) canonical 처리 부재 | 실제 납부세액 대비 편차 확대 |
| 3) 산식 단순화 | 원천징수 차감이 `3.3%` 휴리스틱 중심 | 실 납부분(원천/기납부/중간예납) 반영 누락 |
| 4) 반영연도 문제 | 기준년도는 `month_key` 연도 단일 사용 | 연도 경계/개정 반영 타이밍 오차 가능 |
| 5) 라우트 값 불일치 | 사용자 입력 필드와 공식 코어 필수값 연결이 약함 | 공식 경로 진입률 저하 |
| 6) fallback 과개입 | 공식 입력 미충족 시 proxy/차단이 빈번 | 99% 영역 진입 불가 |

### 1-3. 세금 99%에 필요한 입력(진단 시점)
- 최소 입력(공식 계산 진입 최소 조건)
  - 연 과세표준(`official_taxable_income_annual_krw`)
- 권장 입력(99% 근접에 필요)
  - 원천징수 여부 + 연간 기납부/원천세액 누적
  - 중간예납/선납 세액
  - 소득 구성(사업/근로/기타) 및 연간 필요경비 정보
  - 계산 대상 연도 확정값(기준월과 신고연도 차이 방지)

---

## 2) 건보료 정확도 병목 분류

### 2-0. 건보료 reason taxonomy (표준 코드)
- `ok`
- `missing_membership_type`
- `unknown_membership_type`
- `missing_salary_monthly`
- `missing_non_salary_income`
- `missing_property_tax_base`
- `missing_snapshot`
- `dataset_fallback`
- `insufficient_profile_inputs`

legacy alias 정규화:
- `official_not_ready` -> `missing_snapshot`
- `input_insufficient` -> `insufficient_profile_inputs`
- `dataset_update_error` -> `dataset_fallback`
- `dataset_fallback_default` -> `dataset_fallback`
- `dataset_stale` -> `dataset_fallback`

### 2-1. 정확도 레벨 분기(코드 기준)
- 레벨 계산 위치: `services/nhis_runtime.py: build_nhis_result_meta`
- `blocked`
  - 조건: `official_ready == False` 또는 `member_type` 미확정/비표준
- `limited`
  - 조건: `confidence_level == "low"` 또는 필수 입력 미충족
- `high_confidence`
  - 조건: 유형별 high 필수 입력 충족 + `can_estimate == True` + `confidence in {"high","medium"}`
- `exact_ready`
  - 조건: high 조건 충족 + `mode.startswith("bill")` + `confidence_level == "high"`
- 참고:
  - 내부 `level(normal/limited/blocked)`은 호환 필드로 유지되고, 운영 판정은 `accuracy_level`을 우선 사용

### 2-2. 병목 원인(카테고리별)
| 카테고리 | 코드 병목 | 정확도 영향 |
|---|---|---|
| 1) 입력 부족 | 가입유형/보수월액/비보수소득/재산 입력 누락 | 하한 중심 계산으로 과소추정 |
| 2) 저장/정규화 누락 | NHIS 폼 입력과 `NhisUserProfile` 키 연결 편차 | 유형별 산식 분기 정확도 저하 |
| 3) 산식 단순화 | 직장가입자 추가보험료/세대·경감 예외 단순화 | 고지액과 편차 확대 가능 |
| 4) 반영연도 문제 | 1~10월/11~12월 사이클은 반영되나 입력 기준연도 불명확 시 오차 | 10/11월 경계 편차 |
| 5) 라우트 값 불일치 | 자산 동기화값과 NHIS 직접입력 혼합 우선순위 | 사용자가 기대한 값과 계산 입력 불일치 |
| 6) fallback 과개입 | snapshot fallback/guard 미준비 시 제한 추정 | 99% 판정 불가 |

### 2-3. 건보료 99%에 필요한 입력(진단 시점)
- 지역가입자
  - 소득군(사업/이자/배당/기타/근로·연금) 연간값
  - 재산세 과표/전월세(보증금·월세) 및 중복 여부
  - 최근 고지서 점수/금액(있으면 정확도 급상승)
- 직장가입자
  - 보수월액(월 급여) + 보수외소득 연간값
  - 최근 고지서(health only 또는 total)
- 피부양자
  - 피부양자 자격 유지 여부(소득·재산 기준 충족 여부 확인 입력)

---

## 3) 대표 케이스 설계(최소 10개)

| ID | 영역 | 시나리오 | 현재 예상 분기 | 현재 정확도 병목 원인 |
|---|---|---|---|---|
| TAX-01 | 세금 | 프리랜서 단순(연 과세표준 입력 있음) | `official_exact` | 기납부/중간예납 입력이 없어 실납부세액 편차 가능 |
| TAX-02 | 세금 | 프리랜서 고경비(연 수입 높고 필요경비 큼) | `limited_proxy` 가능 | 과세표준 미입력 시 월 이익 proxy로 과세표준 대체 |
| TAX-03 | 세금 | 원천징수 3.3 입력 있음(거래 키워드 없음) | `official_exact` 또는 `limited_proxy` | 차감이 단순 3.3% 추정이라 실제 원천세액과 차이 |
| TAX-04 | 세금 | 원천징수 미입력 + 키워드 거래 일부만 존재 | `limited_proxy` | 휴리스틱 누락/과다 반영 위험 |
| TAX-05 | 세금 | 과세표준/소득 입력 모두 없음 | `blocked` | 공식 계산 차단으로 0원/저평가 위험 |
| TAX-06 | 세금 | 10월/11월 연도 경계(신고연도 민감) | `official_exact` | 기준연도 단순 매핑으로 신고 기준연도 불일치 가능 |
| NHIS-01 | 건보 | 지역가입자 단순(소득/재산 핵심 입력 있음) | `high_confidence` | 세대합산·감면·예외 규칙 미입력 시 오차 |
| NHIS-02 | 건보 | 지역가입자 금융소득 9.9M(문턱 직전) | `high_confidence` | 금융 문턱 경계 입력 오차에 민감 |
| NHIS-03 | 건보 | 지역가입자 금융소득 10.1M(문턱 직후) | `high_confidence` | 문턱 초과 전액 반영으로 급변, 입력 검증 필요 |
| NHIS-04 | 건보 | 직장가입자(보수월액 입력, 보수외소득 없음) | `high_confidence` | 회사부담/정산/감면 반영 한계 |
| NHIS-05 | 건보 | 직장가입자(보수월액 누락) | `limited` | 핵심 입력 부족으로 계산 불가/저신뢰 |
| NHIS-06 | 건보 | 피부양자 | `high_confidence`(0원) | 피부양자 자격 판정 입력 부족 시 오분류 위험 |
| NHIS-07 | 건보 | 10월 vs 11월 반영연도 경계 | `high_confidence` 또는 `limited` | 기준연도는 반영되나 입력 연도 정합성 부족 |
| NHIS-08 | 건보 | 공식 스냅샷 미준비/검증 불가 | `blocked` | `missing_snapshot` 우선 병목으로 계산 차단 |

---

## 4) 병목 요약(99% 달성 관점)

### 세금
- 가장 큰 병목:
  - 공식 코어는 있으나, 실사용 입력이 연 과세표준/세액차감 항목까지 충분히 수집되지 않아 `official_exact` 진입률과 정확도 상한이 낮음.
- 즉시 보강 우선순위:
  1. 연 과세표준 입력 경로 명확화/강화
  2. 기납부·중간예납(연간) 입력 canonical 추가
  3. 입력 부재 시 `limited_proxy` 사유를 명확히 분리

### 건보료
- 가장 큰 병목:
  - 현 시점 실사용자 분포에서 `missing_snapshot`으로 `blocked`가 100% 발생.
  - snapshot 준비 이후에도 가입유형별 핵심 입력(보수월액/비보수소득/재산세과표/고지이력) 미충족 시 `limited` 비중이 커짐.
- 즉시 보강 우선순위:
  1. snapshot 준비율/갱신 성공률 확보(`missing_snapshot` 해소)
  2. NHIS 직접입력 경로에서 핵심 필드 수집 강화
  3. 자산 동기화값과 직접 입력값 우선순위 명확화
  4. 금융 문턱/10·11월 경계 테스트를 회귀 고정

---

## 5) 티켓 2~7 실행 기준(게이트)

- 티켓 2/4(입력 구조 보강) 완료 조건:
  - 위 표의 `입력 부족` 병목을 줄이는 필드가 실제 저장/조회/계산에 연결될 것
- 티켓 3/5(정확도 측정) 완료 조건:
  - 대표 케이스에서 기대값/계산값/오차율을 수치로 고정할 것
- 티켓 6(내부 판정) 완료 조건:
  - `exact_ready/high_confidence/limited/blocked`가 테스트로 검증될 것

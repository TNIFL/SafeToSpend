# TAX/NHIS 99% Required Inputs

- 작성일: 2026-03-14
- 목적: `exact_ready` / `high_confidence` 판정 기준을 코드/운영 문서로 고정
- 원칙: 자동 추론/백필은 보조 수단이며 `exact_ready/high_confidence` 대체 수단으로 사용 금지

## 1) TAX 필수 입력 세트

### 1-1. `exact_ready` 필수
- `high_confidence` 필수 전체 충족
- `official_taxable_income_annual_krw` (고급 입력에서 직접 입력)
- `tax_advanced_input_confirmed=true` (고급 입력 저장 완료)

### 1-2. `high_confidence` 필수
- `income_classification`
- `annual_gross_income_krw`
- `annual_deductible_expense_krw`
- `withheld_tax_annual_krw` (없으면 `0` 명시)
- `prepaid_tax_annual_krw` (없으면 `0` 명시)
- `tax_basic_inputs_confirmed=true` (기본 입력 저장 완료)

### 1-3. 보조/선택 입력
- `industry_group`
- `tax_type`
- `prev_income_band`
- `withholding_3_3`
- `other_income`, `other_income_types`

## 2) NHIS 필수 입력 세트

### 2-1. 공통 필수
- `official_ready=true` (guard+snapshot+reference check 통과)
- `member_type` (`regional|employee|dependent`)

### 2-2. 유형별 `high_confidence` 필수
- `employee`
  - `salary_monthly_krw` (> 0)
  - `non_salary_annual_income_krw` (없으면 `0` 명시)
- `regional`
  - `annual_income_krw` (없으면 `0` 명시)
  - `non_salary_annual_income_krw` (없으면 `0` 명시)
  - `property_tax_base_total_krw` (없으면 `0` 명시)
- `dependent`
  - `member_type=dependent` 명시

### 2-3. `exact_ready` 추가 조건
- `high_confidence` 필수 충족
- `mode`가 `bill*`이고 `confidence_level=high`
- `fallback/stale/update_error` 없음

## 3) 자동 보완/재입력 분리

### 3-1. 자동 보완 가능
- TAX: `annual_gross_income_krw`, `annual_deductible_expense_krw`
- NHIS: `annual_income_krw` (연동 거래 집계 기반)

### 3-2. 저신뢰 추론(참고만)
- TAX: `official_taxable_income_annual_krw` proxy(gross-expense/거래 합산)
- NHIS: `non_salary_annual_income_krw` 추론

### 3-3. 반드시 사용자 입력 필요
- TAX:
  - `income_classification`
  - `annual_gross_income_krw`
  - `annual_deductible_expense_krw`
  - `withheld_tax_annual_krw`
  - `prepaid_tax_annual_krw`
  - `official_taxable_income_annual_krw` (`exact_ready` 목표 시)
- NHIS:
  - `member_type`
  - `salary_monthly_krw` (직장)
  - `property_tax_base_total_krw` (지역)

## 4) 현재 충족률(97명 집계)

출처:
- `reports/tax_input_gap_audit_post_completion_improvement.json`
- `reports/nhis_snapshot_gap_audit_post_completion_improvement.json`

### 4-1. TAX
- `official_taxable_income_annual_krw` 보유: `0.00%` (고급 입력)
- `income_classification` 보유: `0.00%`
- `annual_gross_income_krw` 보유: `0.00%`
- `annual_deductible_expense_krw` 보유: `0.00%`
- `withheld_tax_annual_krw` 보유: `0.00%`
- `prepaid_tax_annual_krw` 보유: `0.00%`

### 4-2. NHIS
- `member_type` 보유: `37.11%`
- `salary_monthly_krw` 보유: `9.28%`
- `annual_income_krw` 보유: `9.28%`
- `non_salary_annual_income_krw` 보유: `11.34%`
- `property_tax_base_total_krw` 보유: `36.08%`
- `financial_income_annual_krw` 보유: `0.00%`

## 5) 코드 반영 상태

- TAX:
  - `routes/web/profile.py` step2 기본 입력 저장 전 진행 차단
  - `routes/web/auth.py` 온보딩 완료 후 `tax_profile step=2` 강제 진입
  - `services/risk.py`에서 `high_confidence=기본 입력 저장`, `exact_ready=고급 입력 저장`으로 판정 분리
  - `templates/tax_profile.html`에서 기본 입력/고급 입력 분리 및 초안 prefill 제공(저장 전 승급 불가)
- NHIS:
  - `services/nhis_profile.py` 저장 시 가입유형별 필수 입력 누락 차단
  - `services/nhis_runtime.py` 판정은 `member_type` 미입력 시 `blocked` 유지
  - `templates/nhis.html`에서 blocked/limited 상태별 입력 복구 CTA 우선 노출

## 6) 운영 원칙

- 필수 입력 미충족이면 `high_confidence/exact_ready` 금지
- 자동 보완값만으로 `exact_ready` 부여 금지
- `blocked/limited`는 핵심 숫자 강노출보다 입력 보완 CTA 우선

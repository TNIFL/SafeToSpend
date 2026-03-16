# TAX Input Flow Redesign

- 작성일: 2026-03-14
- 목적: 세법 용어 중심 입력을 사용자 언어 중심 입력 + 단계별 저장/신뢰도 상승 구조로 전환

## 1) 새 입력 모델

### 1-1. 기본 입력(사용자 언어)
- `income_classification` (소득 유형)
- `annual_gross_income_krw` (총수입)
- `annual_deductible_expense_krw` (업무 관련 지출)
- `withheld_tax_annual_krw` (이미 떼인 세금)
- `prepaid_tax_annual_krw` (이미 낸 세금)

### 1-2. 고급 입력(정밀 모드)
- `official_taxable_income_annual_krw` (연 과세표준 직접 입력)

## 2) 신뢰도 판정 규칙

### 2-1. `high_confidence`
- 기본 입력 5종이 모두 저장됨
- `tax_basic_inputs_confirmed=true` (사용자 저장 완료)
- 과세표준 직접 입력은 필수가 아님

### 2-2. `exact_ready`
- `high_confidence` 조건 충족
- `official_taxable_income_annual_krw > 0`
- `tax_advanced_input_confirmed=true` (고급 입력 저장 완료)

### 2-3. `limited` / `blocked`
- `limited`: 기본 입력 일부 누락 또는 초안만 존재
- `blocked`: 소득 유형 누락 등 핵심 기본 입력 다수 누락으로 계산 신뢰도 확보 불가

## 3) 자동 초안 원칙

- 자동 초안은 `draft`로만 사용
- 초안 존재만으로 `high_confidence`/`exact_ready` 승급 금지
- 사용자 확인/수정 후 저장했을 때만 승급

## 4) 단계형 저장(기본 입력 5단계)

1. 소득 유형(`income_classification`)
2. 총수입(`annual_gross_income_krw`)
3. 업무 관련 지출(`annual_deductible_expense_krw`)
4. 이미 떼인 세금(`withheld_tax_annual_krw`)
5. 이미 낸 세금(`prepaid_tax_annual_krw`)

핵심 엔드포인트:
- `POST /dashboard/profile/tax-income-classification` (1문항 빠른 저장)
- `POST /dashboard/profile/tax-basic-step` (단계별 저장)

규칙:
- 초안 prefill 허용
- 저장 전 승급 금지
- 5개 기본 입력 저장 완료 시에만 `tax_basic_inputs_confirmed=true`

## 5) 인라인 1문항 저장(신규 반영)

- `missing_income_classification` 상태에서는 CTA 이동보다 인라인 저장 카드를 우선 노출한다.
- 반영 화면:
  - `overview`
  - `tax_buffer`
  - `tax_profile`
- 이벤트:
  - `tax_inline_income_classification_shown`
  - `tax_inline_income_classification_saved`
  - `tax_basic_next_step_viewed`
  - `tax_basic_next_step_saved`
- 기존 CTA는 fallback 링크로만 유지.

## 6) 코드 반영 지점

- `services/onboarding.py`: 기본/고급 필수 입력 및 확인 플래그 판정
- `services/risk.py`: `accuracy_level` 판정(`high_confidence`/`exact_ready`)과 메타 메시지
- `services/tax_input_draft.py`: 거래/라벨 기반 기본 입력 초안 생성
- `routes/web/profile.py`: quick-save, stepwise 저장, 인라인 퍼널 이벤트
- `routes/web/calendar/tax.py`: blocked/limited 복구 블록 + 인라인 노출 이벤트
- `routes/web/overview.py`: 인라인 저장 카드 노출 이벤트
- `templates/tax_profile.html`, `templates/overview.html`, `templates/calendar/tax_buffer.html`: 인라인 1문항 저장 카드 우선

## 7) 검증 명령

```bash
.venv/bin/python -m unittest \
  tests.test_input_funnel_instrumentation \
  tests.test_tax_inline_first_field_flow \
  tests.test_tax_stepwise_completion_flow \
  tests.test_tax_input_draft \
  tests.test_tax_required_input_flow \
  tests.test_tax_nhis_ui_guard_behavior \
  tests.test_tax_estimate_service
```

결과:
- `Ran 49 tests ... OK` (관련 통합 세트 실행 기준)

## 8) 재집계 결과 요약

- 퍼널 집계 파일: `reports/input_funnel_audit_post_inline_save.json`
- 정확도 집계 파일: `reports/accuracy_level_audit_post_inline_save.json`

요약:
- `tax_inline_income_classification_shown/saved`: 0 / 0
- all_users TAX 분포: `blocked 95`, `limited 2`, `exact/high 0`
- 상위 원인: `missing_income_classification` 95명

해석:
- 입력 구조/인라인 저장 경로는 반영 완료.
- 현재 운영 데이터 윈도우에서는 inline 저장 이벤트가 아직 발생하지 않아 분포 개선이 관측되지 않았다.

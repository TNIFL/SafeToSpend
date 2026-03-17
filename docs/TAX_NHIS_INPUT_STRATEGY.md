# TAX/NHIS Input Strategy (Next Priority)

- 작성일: 2026-03-14
- 목적: 남은 blocked/limited를 줄이기 위한 입력 전략 우선순위 고정
- 근거:
  - `reports/accuracy_level_audit_post_inline_save.json`
  - `reports/accuracy_input_gap_report_post_completion_improvement.json`
  - `reports/tax_input_gap_audit_post_completion_improvement.json`
  - `reports/nhis_snapshot_gap_audit_post_completion_improvement.json`
  - `reports/input_funnel_audit_post_inline_save.json`

## 1) 입력 항목별 전략표

### 1-1. TAX

| 입력 항목 | exact_ready 필요 | high_confidence 필요 | 자동 백필 | 현재 충족률 | 없으면 상태 | 다음 액션 |
|---|---|---|---|---:|---|---|
| `official_taxable_income_annual_krw` | Y | N | 저신뢰 추론만 가능 | 0.00% | exact_ready 불가 | 고급 입력(정밀 모드) 분리 유지 |
| `income_classification` | Y | Y | 불가 | 0.00% | blocked/limited | 인라인 1문항 저장 카드 최우선 |
| `annual_gross_income_krw` | Y | Y | 가능 | 0.00% | blocked/limited | 단계형 저장 + prefill |
| `annual_deductible_expense_krw` | Y | Y | 가능 | 0.00% | blocked/limited | 단계형 저장 + prefill |
| `withheld_tax_annual_krw` | Y | Y | 불가 | 0.00% | blocked/limited | 입력/0값 확인 저장 강제 |
| `prepaid_tax_annual_krw` | Y | Y | 불가 | 0.00% | blocked/limited | 입력/0값 확인 저장 강제 |
| `tax_basic_inputs_confirmed` | Y | Y | 불가 | 0.00% | high/exact 불가 | 사용자 저장 완료 시만 true |
| `tax_advanced_input_confirmed` | Y | N | 불가 | 0.00% | exact_ready 불가 | 고급 입력 저장 시만 true |

### 1-2. NHIS

| 입력 항목 | exact_ready 필요 | high_confidence 필요 | 자동 백필 | 현재 충족률 | 없으면 상태 | 다음 액션 |
|---|---|---|---|---:|---|---|
| `official_ready`(guard+snapshot) | Y | Y | 시스템 복구 | 복구 완료 | blocked | guard mismatch 재발 감시 |
| `member_type` | Y | Y | 불가 | 37.11% | blocked | 인라인 1문항 저장 카드 최우선 |
| `salary_monthly_krw`(직장) | Y | Y | 불가 | 9.28% | limited | 직장 선택 후 상세 단계 저장 |
| `annual_income_krw`(지역) | Y | Y | 일부 가능 | 9.28% | limited | prefill+사용자 저장 확인 |
| `non_salary_annual_income_krw` | Y | Y | 저신뢰 가능 | 11.34% | limited | 상세 단계 저장 유도 |
| `property_tax_base_total_krw`(지역) | Y | Y | 불가 | 36.08% | limited | 지역가입자 필수 입력 |
| `bill mode + high` | Y | N | 불가 | 0.00% | exact_ready 불가 | 고지이력 입력 유도 |

## 2) 자동 보완 vs 재입력

### 2-1. 자동 보완 가능(승급 보조)
1. TAX `annual_gross_income_krw`
2. TAX `annual_deductible_expense_krw`
3. NHIS `annual_income_krw`

### 2-2. 저신뢰 추론(참고만, 승급 금지)
1. TAX `official_taxable_income_annual_krw` proxy
2. NHIS `non_salary_annual_income_krw` 추론

### 2-3. 반드시 재입력 필요
1. TAX `income_classification`, `annual_gross_income_krw`, `annual_deductible_expense_krw`, `withheld_tax_annual_krw`, `prepaid_tax_annual_krw`
2. TAX `official_taxable_income_annual_krw`(exact_ready 목표 시)
3. NHIS `member_type`, `salary_monthly_krw`(직장), `property_tax_base_total_krw`(지역)

## 3) 승급 금지 규칙

- 자동 백필/추론값만으로 `exact_ready` 금지
- 자동 백필/추론값만으로 `high_confidence` 금지
- 승급은 필수 입력 직접 저장 + guard 정상 통과를 전제로만 허용

## 4) 입력 회수 우선순위 Top 5

1. TAX `income_classification`
2. NHIS `member_type`
3. TAX `annual_gross_income_krw` + `annual_deductible_expense_krw`
4. TAX `withheld_tax_annual_krw` + `prepaid_tax_annual_krw`
5. NHIS `salary_monthly_krw`(직장) / `property_tax_base_total_krw`(지역)

## 5) 핵심 숫자 노출 정책

- `exact_ready/high_confidence`: 정상 노출
- `limited`: `~` 약노출 + 제한 사유 + 입력 유도
- `blocked`: 핵심 숫자 차단, 입력 완료 카드 우선

## 6) 인라인 퍼널 기반 현재 판단

- TAX inline: `shown/saved` 0/0
- NHIS inline: `shown/saved` 0/0
- CTA fallback: TAX `24/1`, NHIS `0/0`

결론:
- 다음 병목은 산식이 아니라 인라인 카드에서 실제 저장 행동이 발생하지 않는 점이다.
- 다음 단계는 인라인 카드 노출 타이밍/강제성/유입경로 운영 실험이다.

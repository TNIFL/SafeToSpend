# TAX/NHIS Accuracy Distribution & Root Cause Audit

- 작성일: 2026-03-14
- 목적: 실사용 분모 기준 accuracy level 분포/원인/입력 병목을 최신화한다.
- 개인정보: 사용자 식별정보 미포함(집계형 결과만 사용)

## 1) 실행 명령

```bash
PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py \
  --limit 300 \
  --recent-active-days 90 \
  --legacy-days 365 \
  --output reports/accuracy_level_audit_post_inline_save.json
```

```bash
PYTHONPATH=. .venv/bin/python scripts/tax_input_gap_audit.py \
  --limit 300 \
  --output reports/tax_input_gap_audit_post_completion_improvement.json
```

```bash
PYTHONPATH=. .venv/bin/python scripts/nhis_snapshot_gap_audit.py \
  --limit 300 \
  --output reports/nhis_snapshot_gap_audit_post_completion_improvement.json
```

```bash
PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py \
  --limit 300 \
  --output reports/accuracy_input_gap_report_post_completion_improvement.json
```

```bash
PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py \
  --days 30 \
  --limit 5000 \
  --output reports/input_funnel_audit_post_inline_save.json
```

## 2) 분포 집계 대상(분모) 검증

### 2-1. 스캔 규모
- 전체 스캔 사용자: 97명
- 관리자 계정: 1명
- 테스트 계정: 88명
- 최근 90일 비활성: 80명
- profile started: 39명
- linked account: 3명

### 2-2. 코호트별 최신 분포

| cohort | users | TAX blocked | TAX limited | NHIS blocked | NHIS limited | NHIS high |
|---|---:|---:|---:|---:|---:|---:|
| all_users | 97 | 95 (97.94%) | 2 (2.06%) | 61 (62.89%) | 28 (28.87%) | 8 (8.25%) |
| recent_active_users | 17 | 15 (88.24%) | 2 (11.76%) | 14 (82.35%) | 2 (11.76%) | 1 (5.88%) |
| profile_started_users | 39 | 37 (94.87%) | 2 (5.13%) | 3 (7.69%) | 28 (71.79%) | 8 (20.51%) |
| linked_account_users | 3 | 1 (33.33%) | 2 (66.67%) | 1 (33.33%) | 1 (33.33%) | 1 (33.33%) |
| exclude_admin_test_inactive_legacy | 5 | 4 (80.00%) | 1 (20.00%) | 4 (80.00%) | 1 (20.00%) | 0 (0.00%) |
| operational_target_users | 3 | 2 (66.67%) | 1 (33.33%) | 2 (66.67%) | 1 (33.33%) | 0 (0.00%) |

권장 대표 분모:
- `operational_target_users` (관리자/테스트 제외 + 최근 활성 + 입력 시작 사용자)

## 3) 전후 비교(핵심)

### 3-1. all_users 기준

| metric | 변경 전 (`accuracy_level_audit_latest`) | `post_input_recovery` | `post_completion_improvement` | `post_inline_save` |
|---|---:|---:|---:|---:|
| TAX exact/high | 0.00% | 0.00% | 0.00% | 0.00% |
| TAX blocked | 97.94% | 97.94% | 97.94% | 97.94% |
| NHIS high_confidence | 0.00% | 8.25% | 8.25% | 8.25% |
| NHIS blocked | 100.00% | 62.89% | 62.89% | 62.89% |

### 3-2. operational_target_users 기준

| metric | 변경 전 | `post_input_recovery` | `post_completion_improvement` | `post_inline_save` |
|---|---:|---:|---:|---:|
| TAX exact/high | 0.00% | 0.00% | 0.00% | 0.00% |
| TAX blocked | 66.67% | 66.67% | 66.67% | 66.67% |
| NHIS high_confidence | 0.00% | 0.00% | 0.00% | 0.00% |
| NHIS blocked | 100.00% | 66.67% | 66.67% | 66.67% |

해석:
- 분포는 inline 저장 코드 반영 직후 데이터에서도 아직 유의미한 변화가 없다.
- 원인은 계산 엔진이 아니라 입력 저장 이벤트 발생량 부족이다.

## 4) TAX blocked 원인 확정

출처: `reports/tax_input_gap_audit_post_completion_improvement.json`

- `missing_taxable_income + blocked`: 0명(0.00%)
- `official_taxable_income_annual_krw` 보유: 0명(0.00%)
- `income_classification` 미입력: 97명(100.00%)
- `withheld_tax_annual_krw` 미입력: 97명(100.00%)
- `prepaid_tax_annual_krw` 미입력: 97명(100.00%)

확정 결론:
- blocked 핵심은 `missing_income_classification`.
- 과세표준 미입력은 blocked 직접 원인이 아니라 `exact_ready` 미달 원인이다.

## 5) NHIS blocked 원인 확정

출처: `reports/nhis_snapshot_gap_audit_post_completion_improvement.json`

- `official_guard_status.valid=false`, reason=`value_mismatch` (guard 경고 상태는 남아 있음)
- `nhis_ready_status.ready=true`, reason=`ok`
- `snapshot_runtime_status.snapshot_exists=true`, `is_stale=false`
- blocked 최상위 reason: `missing_membership_type` 61명(62.89%)

입력 보유율:
- `membership_type_present`: 36명(37.11%)
- `salary_monthly_krw_present`: 9명(9.28%)
- `annual_income_krw_present`: 9명(9.28%)
- `non_salary_annual_income_krw_present`: 11명(11.34%)
- `property_tax_base_total_krw_present`: 35명(36.08%)
- `financial_income_annual_krw_present`: 0명(0.00%)

확정 결론:
- NHIS 분포 병목은 snapshot 부재가 아니라 가입유형/필수 입력 미완성이다.

## 6) 입력 갭 분포(자동 보완 가능성)

출처: `reports/accuracy_input_gap_report_post_completion_improvement.json`

### 6-1. TAX
- `mixed_requires_user_input`: 97명(100%)
- 자동 보완만으로 승급 가능 비율: 0.0%
- 사용자 직접 입력 최상위 항목:
  - `annual_gross_income_krw`
  - `annual_deductible_expense_krw`
  - `income_classification`
  - `official_taxable_income_annual_krw`
  - `withheld_tax_annual_krw`
  - `prepaid_tax_annual_krw`

### 6-2. NHIS
- `user_input_required`: 61명(62.89%)
- `auto_upgrade_possible`: 26명(26.80%)
- `already_high_or_exact`: 8명(8.25%)
- 사용자 직접 입력 최상위 항목: `member_type`

## 7) 입력 퍼널 요약(Inline 전환 반영)

출처: `reports/input_funnel_audit_post_inline_save.json`

- 최근 30일 이벤트 row: 28
- TAX inline:
  - `tax_inline_income_classification_shown`: 0
  - `tax_inline_income_classification_saved`: 0
  - `tax_recovery_completed`: 0
- NHIS inline:
  - `nhis_inline_membership_type_shown`: 0
  - `nhis_inline_membership_type_saved`: 0
  - `nhis_recovery_completed`: 0
- CTA fallback:
  - `tax_recovery_cta_shown`: 24
  - `tax_recovery_cta_clicked`: 1
  - `nhis_recovery_cta_shown/clicked`: 0/0

해석:
- 인라인 퍼널 계측은 반영됐지만, 현재 데이터 윈도우에서는 inline 저장 이벤트가 아직 발생하지 않았다.
- 여전히 실사용 병목은 “저장 행동 전환”이다.

## 8) 현재 판단

- NHIS: 계산 엔진보다 `membership_type` 회수율이 다음 병목.
- TAX: `income_classification` 저장률이 분포 개선을 가로막는 1순위 병목.
- 다음 우선순위: 로직 추가보다 인라인 저장 노출 사용자군의 실제 저장 발생률을 운영에서 끌어올리는 실험.

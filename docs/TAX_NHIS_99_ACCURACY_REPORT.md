# TAX/NHIS 99% Accuracy Report (Inline Save Recovery Phase)

- 작성일: 2026-03-14
- 범위: CTA 클릭형 복구 플로우를 화면 내 인라인 1문항 저장 플로우로 전환
- 근거 데이터:
  - `reports/input_funnel_audit_post_completion_improvement.json`
  - `reports/input_funnel_audit_post_inline_save.json`
  - `reports/accuracy_level_audit_post_completion_improvement.json`
  - `reports/accuracy_level_audit_post_inline_save.json`
  - `reports/tax_input_gap_audit_post_completion_improvement.json`
  - `reports/nhis_snapshot_gap_audit_post_completion_improvement.json`

## A. 세금 인라인 1문항 저장 플로우 반영 결과

- 반영 내용:
  - `overview`, `tax_buffer`, `tax_profile`에서 `missing_income_classification` 상태 시 인라인 저장 카드 우선 노출
  - 소득 유형 1문항 저장 엔드포인트(`POST /dashboard/profile/tax-income-classification`) 유지
  - 저장 후 `reason`/`accuracy` 재계산 + 다음 기본 입력 단계 유도
- 회귀 테스트:
  - `tests.test_tax_inline_first_field_flow`
  - `tests.test_tax_nhis_ui_guard_behavior`
  - `tests.test_tax_estimate_service`

## B. 건보 인라인 1문항 저장 플로우 반영 결과

- 반영 내용:
  - `overview`, `nhis`에서 `missing_membership_type` 상태 시 인라인 저장 카드 우선 노출
  - 가입유형 1문항 저장 엔드포인트(`POST /dashboard/nhis/membership-type`) 유지
  - 저장 후 유형별 다음 입력 단계 유도
- 회귀 테스트:
  - `tests.test_nhis_inline_first_field_flow`
  - `tests.test_tax_nhis_ui_guard_behavior`
  - `tests.test_nhis_input_paths`

## C. 인라인 저장 중심 퍼널 계측 결과

- 계측 기준: `inline shown -> inline saved -> next step viewed -> next step saved -> recovery completed`
- 근거: `reports/input_funnel_audit_post_inline_save.json`

세금 inline 퍼널:
- `tax_inline_income_classification_shown`: 0
- `tax_inline_income_classification_saved`: 0
- `tax_basic_next_step_viewed/saved`: 0 / 0
- `tax_recovery_completed`: 0

건보 inline 퍼널:
- `nhis_inline_membership_type_shown`: 0
- `nhis_inline_membership_type_saved`: 0
- `nhis_detail_next_step_viewed/saved`: 0 / 0
- `nhis_recovery_completed`: 0

보조(CTA fallback):
- TAX `cta_shown/clicked`: 24 / 1 (4.17%)
- NHIS `cta_shown/clicked`: 0 / 0

## D. 기존 CTA/fallback 정리 결과

- CTA 경로는 제거하지 않고 fallback으로 유지했다.
- 퍼널 집계 요약은 CTA가 아니라 inline을 primary로 전환했다.
- CTA 클릭 이벤트는 운영 비교용 보조 지표로만 사용한다.

## E. 저장 완료율 / reason 변화

- 저장 완료율:
  - TAX inline save rate(from shown): 0.0%
  - NHIS inline save rate(from shown): 0.0%
- reason 상위(퍼널 이벤트 기준):
  - `proxy_from_annual_income`: 24
  - `ok`: 3
  - `missing_income_classification`: 1
- 해석:
  - 코드/계측은 inline 기준으로 전환됐지만, 현재 데이터 윈도우에서는 inline 저장 발생이 아직 없다.

## F. exact_ready / high_confidence / limited / blocked 분포 전후 비교

### F-1. all_users 기준

| domain | metric | `post_completion_improvement` | `post_inline_save` | delta |
|---|---|---:|---:|---:|
| TAX | exact+high | 0 | 0 | 0 |
| TAX | limited | 2 | 2 | 0 |
| TAX | blocked | 95 | 95 | 0 |
| NHIS | exact+high | 8 | 8 | 0 |
| NHIS | limited | 28 | 28 | 0 |
| NHIS | blocked | 61 | 61 | 0 |

### F-2. operational_target_users 기준

| domain | metric | `post_completion_improvement` | `post_inline_save` | delta |
|---|---|---:|---:|---:|
| TAX | exact+high | 0 | 0 | 0 |
| TAX | blocked | 2 | 2 | 0 |
| NHIS | exact+high | 0 | 0 | 0 |
| NHIS | blocked | 2 | 2 | 0 |

## G. 실제 운영 가능 여부 최종 판정

1) 세금 로직: **조건부 신뢰**
- 산식/판정 로직은 유지되지만 실사용 입력 저장률이 낮아 분포가 blocked 중심이다.

2) 건보료 로직: **조건부 신뢰**
- guard/snapshot 기반 계산은 동작하나 `missing_membership_type` 입력 미완성이 blocked를 지배한다.

3) 제품 운영: **특정 조건에서만 가능**
- 입력을 실제 저장한 사용자군에서는 운영 가능.
- 현재 전체 분포/퍼널 기준으로는 99% 운영 가능 판정 불가.

최종 결론:
- 이번 단계는 퍼널 모델을 click 중심에서 inline save 중심으로 바꾼 단계다.
- 다음 우선순위는 로직 추가가 아니라 inline 카드 노출 사용자군의 실제 저장 전환율을 운영에서 발생시키는 것이다.

# TAX/NHIS Input Recovery Plan

- 작성일: 2026-03-14

## Scope
- Goal: move existing users out of `blocked`/`limited` by forcing missing required inputs through immediate inline save cards.
- Non-goal: formula redesign or full UI redesign.

## Status Segments
- Tax `blocked`: missing core tax inputs (most often `income_classification` 포함 기본 입력 누락).
- Tax `limited`: calculation runs, but 기본 입력 저장 확인/고급 입력 확인이 부족함.
- NHIS `blocked`: missing/invalid membership type or guard not ready.
- NHIS `limited`: estimate exists, but membership-type required fields incomplete.

## Recovery Entry Points (Inline-first)
| Surface | Tax blocked | Tax limited | NHIS blocked | NHIS limited |
|---|---|---|---|---|
| `overview` | `income_classification` 즉시 저장 카드 우선 + fallback 링크 | 보완 카드 + step2 링크 | `membership_type` 즉시 저장 카드 우선 + fallback 링크 | 보완 카드 + nhis 상세 입력 링크 |
| `tax_buffer` | 숫자보다 인라인 1문항 저장 카드 우선 | 약노출 숫자 + 보완 카드 | NHIS 숫자 약화 + membership 저장 유도 | NHIS 보완 카드 |
| `nhis` | (tax 컨텍스트만 표시) | (tax 컨텍스트만 표시) | KPI보다 가입유형 인라인 저장 카드 우선 | 유형별 누락 입력 보완 카드 |
| `profile` | step 2에서 1문항 quick-save + 단계형 저장 | 동일 | membership-only quick-save + 유형별 상세 저장 | 동일 |

## Quick Recovery Save Paths
- Tax single field:
  - `POST /dashboard/profile/tax-income-classification`
- Tax stepwise field:
  - `POST /dashboard/profile/tax-basic-step`
- NHIS single field:
  - `POST /dashboard/nhis/membership-type`
- NHIS membership-only save in form:
  - `action=save_membership_only`

## Display Policy
- `blocked`: 핵심 숫자보다 즉시 저장 카드 우선.
- `limited`: 제한 문구 + 보완 카드 우선, 숫자는 약노출.
- `exact_ready/high_confidence`: 복구 카드 숨김.

## Canonical Missing Field Labels
- Tax: 소득 유형, 총수입, 업무 관련 지출, 이미 떼인 세금, 이미 낸 세금, 연 과세표준(고급 입력).
- NHIS: 가입유형, 직장 월 보수, 보수 외 소득(연), 연소득 총액, 재산세 과세표준 합계, 금융소득(연).

## Validation/Guard Coupling
- Recovery cards are generated from `result_meta.accuracy_level` + `required_inputs` + `needs_user_input_fields`.
- Auto backfill/draft remains helper-only; promotion to `high_confidence`/`exact_ready` requires explicit user save.

## Funnel Tracking Coupling (Inline-first)
- Tax:
  - `tax_inline_income_classification_shown`
  - `tax_inline_income_classification_saved`
  - `tax_basic_next_step_viewed`
  - `tax_basic_next_step_saved`
  - `tax_recovery_completed`
- NHIS:
  - `nhis_inline_membership_type_shown`
  - `nhis_inline_membership_type_saved`
  - `nhis_detail_next_step_viewed`
  - `nhis_detail_next_step_saved`
  - `nhis_recovery_completed`
- CTA(`*_cta_shown/clicked`)는 fallback 트래픽 모니터링용 보조 지표로 유지.

## Latest Audit Snapshot (2026-03-14)
- 근거: `reports/input_funnel_audit_post_inline_save.json`
- inline save 이벤트: TAX/NHIS 모두 0건
- CTA fallback: TAX `24 -> 1`, NHIS `0 -> 0`

해석:
- 코드상 인라인 복구 경로는 반영됨.
- 운영 데이터에서는 아직 인라인 저장 행동이 발생하지 않아 전환 개선이 미관측 상태.

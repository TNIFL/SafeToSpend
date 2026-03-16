# TAX/NHIS Input Funnel Plan

- 작성일: 2026-03-14
- 목적: CTA 클릭 병목을 우회하기 위해 입력 복구 퍼널을 `inline save` 중심으로 측정한다.

## 1) 이벤트 정의

### 1-1. 세금(Primary: Inline)
- `tax_inline_income_classification_shown`
- `tax_inline_income_classification_saved`
- `tax_basic_next_step_viewed`
- `tax_basic_next_step_saved`
- `tax_recovery_completed`

### 1-2. 건보(Primary: Inline)
- `nhis_inline_membership_type_shown`
- `nhis_inline_membership_type_saved`
- `nhis_detail_next_step_viewed`
- `nhis_detail_next_step_saved`
- `nhis_recovery_completed`

### 1-3. 기존 CTA(Fallback)
- 세금: `tax_recovery_cta_shown`, `tax_recovery_cta_clicked`
- 건보: `nhis_recovery_cta_shown`, `nhis_recovery_cta_clicked`

## 2) 저장 포맷
- 저장소: `action_logs`
- `action_type`: `label_update`
- `before_state.metric_type`: `input_funnel`
- 공통 필드:
  - `metric_event`
  - `route`
  - `screen`
  - `accuracy_level_before`
  - `accuracy_level_after`
  - `reason_code_before`
  - `reason_code_after`
  - `timestamp`

## 3) 집계 스크립트
- 파일: `scripts/input_funnel_audit.py`
- 기본 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --days 30 --limit 5000`
- 최신 결과 저장(인라인 저장 전환 단계):
  - `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --days 30 --limit 5000 --output reports/input_funnel_audit_post_inline_save.json`
- 실검증 사용자 단건 저장(수동 검증):
  - `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --user-pk 343 --days 30 --limit 5000 --output reports/input_funnel_audit_manual_validation.json`
- 사용자 단건:
  - `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --user-pk 123 --days 30 --limit 2000`

## 4) 해석 기준
- 1차 병목(Primary): `inline_shown -> inline_saved`
- 2차 병목(Primary): `next_step_viewed -> next_step_saved`
- 3차 병목(Primary): `next_step_saved -> recovery_completed`
- 보조 병목(Fallback): `cta_shown -> cta_clicked`

## 5) 최신 계측 결과 (2026-03-14)
- 근거: `reports/input_funnel_audit_post_inline_save.json`
- 윈도우: 최근 30일, 조회 5000건, 집계 row 28

### 5-1. TAX inline 퍼널
- `tax_inline_income_classification_shown`: 0
- `tax_inline_income_classification_saved`: 0
- `tax_basic_next_step_viewed/saved`: 0 / 0
- `tax_recovery_completed`: 0

### 5-2. NHIS inline 퍼널
- `nhis_inline_membership_type_shown`: 0
- `nhis_inline_membership_type_saved`: 0
- `nhis_detail_next_step_viewed/saved`: 0 / 0
- `nhis_recovery_completed`: 0

### 5-3. CTA fallback 퍼널
- TAX:
  - `tax_recovery_cta_shown`: 24
  - `tax_recovery_cta_clicked`: 1 (4.17%)
- NHIS:
  - `nhis_recovery_cta_shown`: 0
  - `nhis_recovery_cta_clicked`: 0

### 5-4. 분포 보조지표
- `reason_code_before` 상위:
  - `proxy_from_annual_income`: 24
  - `ok`: 3
  - `missing_income_classification`: 1
- `screen` 분포:
  - `tax_buffer`: 14
  - `overview`: 10
  - `nhis`: 3
  - `tax_profile`: 1

## 6) 실검증 결과 (2026-03-14)
- 근거: `reports/input_funnel_audit_manual_validation.json` (user_pk=343, 수동 재현 사용자)
- TAX inline:
  - `shown`: 2
  - `saved`: 1
  - `next_step_viewed`: 1
- NHIS inline:
  - `shown`: 4
  - `saved`: 1
  - `next_step_viewed`: 1

판정:
- 인라인 저장 UI 렌더, 저장 경로, 이벤트 계측은 정상 동작한다.
- `post_inline_save` 0건의 1차 원인은 계측/저장 버그가 아니라 운영 트래픽에서 inline 저장 행동이 아직 거의 발생하지 않은 점이다.

## 7) 결론
- 퍼널 주지표를 CTA 클릭에서 inline 저장으로 전환한 구조는 정상이다.
- 이 트랙의 시스템 버그 확인은 종료 가능하며, 남은 과제는 운영 전환율(노출 대비 저장 행동) 개선이다.

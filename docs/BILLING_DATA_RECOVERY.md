# Billing Data Recovery

작성일: 2026-03-11

## 목적
오염 데이터 중 자동 복구가 안전한 케이스만 보수적으로 복구한다.

## 복구 스크립트
- dry-run:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300`
- 실제 적용:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300 --apply`
- 특정 사용자:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --user-pk <USER_PK> --apply`

## 자동 복구 범위
1. 같은 user/provider에 method가 정확히 1개이고, 현재 inactive이며 revoked가 아니고, registration_attempt가 `billing_key_issued/completed`인 경우
- 해당 method를 `active`로 복구

2. `checkout_intent.status=ready_for_charge` 이고 `billing_method_id is null`이며, 해당 user의 active valid method가 정확히 1개인 경우
- intent에 해당 method id 연결

## 자동 복구 금지(수동 검토)
- active 후보 2개 이상
- revoked/suspended(또는 동등 비정상) 의심
- owner/provider mismatch 의심
- ready intent가 비활성 method를 직접 가리키는 경우

## 2026-03-11 실행 결과(실DB)
### dry-run
명령:
- `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300`

결과:
- `fixed_count=0`
- `manual_review_count=2`
- 수동 검토 reason: `manual_review_required:ready_intent_billing_method_not_active`

### apply
명령:
- `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300 --apply`

결과:
- `fixed_count=0`
- `manual_review_count=2`
- 자동 복구 가능한 안전 케이스 없음

### 적용 후 재진단
명령:
- `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --limit 300`

결과:
- 남은 오염 2건(`ready_intent_unusable_method`)
- 모두 수동 검토 대상으로 유지

### 수동 검토 상세(2026-03-11)
명령:
- `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --user-pk 5 --limit 50`

결과:
- `user_pk=5`의 `checkout_intent_id=2,3`이 `ready_for_charge`인데 연결 method(`id=17,19`)가 `inactive`
- 자동 복구 스코프 밖(의도적 보수 정책)

## 주의
- 본 스크립트는 `users.plan_code / plan_status / extra_account_slots`를 변경하지 않는다.
- entitlement는 기존 reconcile/projector 경로만 사용한다.

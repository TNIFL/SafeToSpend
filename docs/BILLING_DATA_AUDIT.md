# Billing Data Audit

작성일: 2026-03-11

## 목적
과거 결제수단 등록 버그로 남았을 수 있는 오염 데이터를 조회 전용(dry-run)으로 진단한다.

## 실행 명령
- 전체 점검:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --limit 300`
- 특정 사용자 점검:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --user-pk <USER_PK> --limit 300`
- JSON 출력:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --json`

## 진단 규칙
1. `registration_attempt.status in (billing_key_issued, completed)`인데 같은 user/provider의 `active billing_method`가 0개
2. `checkout_intent.status=ready_for_charge`인데 `billing_method_id`가 없거나, method가 없거나, 소유자 불일치거나, status가 active 아님
3. 같은 user/provider에 `active billing_method`가 2개 이상
4. `payment_attempt.checkout_intent_id` 연결 누락/소유자 불일치

## 출력 필드
- `user_pk`
- `registration_attempt_id/status`
- `checkout_intent_id/status/billing_method_id`
- `billing_method_id/status/issued_at/revoked_at`
- `payment_attempt_id`
- `reason`

## 2026-03-11 실행 결과(실DB)
명령:
- `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --limit 300`

요약:
- `ready_intent_unusable_method`: 2건
- 총 2건

샘플 reason:
- `ready_intent_billing_method_not_active`

해석:
- 과거 버그 영향으로 `ready_for_charge` intent가 비활성 method를 가리키는 데이터가 남아 있음.
- 현재 코드(`resolve_checkout_billing_method` fallback)로 런타임은 완화되지만, 데이터 자체는 수동 검토 대상.

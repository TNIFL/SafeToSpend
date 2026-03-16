# Billing Recurring Stage Report

작성일: 2026-03-10  
작성자: Codex

## 1) 실제 수정 파일 목록
- `services/billing/pricing.py`
- `services/billing/reconcile.py`
- `services/billing/recurring.py`
- `app.py`
- `tests/test_billing_recurring.py`
- `tests/test_billing_reconcile_service.py`
- `docs/DEV_TESTING.md`
- `docs/BILLING_GO_NO_GO_REPORT.md`

## 2) due selection 구현 상태
- `services/billing/recurring.py`
  - `evaluate_recurring_candidate(...)` 구현/보정
  - `select_recurring_candidates(...)` 구현
  - 제외 사유 분기:
    - `billing_method_missing`
    - `next_billing_in_future`
    - `subscription_canceled`
    - `cancel_effective_reached`
    - `cycle_attempt_already_exists`
    - `cancel_requested_period_end_only`
    - `grace_expired` 등

## 3) recurring charge attempt 구현 상태
- `services/billing/recurring.py`
  - `_charge_subscription_candidate(...)`에서
    - `payment_attempt(attempt_type=recurring|retry)` 생성
    - billingKey 복호화
    - Toss 청구 호출
    - `reconcile_by_order_id(..., apply_projection=True)` 연결
  - 같은 사이클 중복 attempt 차단 로직 포함

## 4) grace / retry / past_due 구현 상태
- grace 시작:
  - `services/billing/reconcile.py`
  - **recurring/retry 실패 시**에만 `grace_started` 전이 + `grace_until=now+3days`
- retry:
  - `services/billing/recurring.py`의 `run_retry_batch(...)`
- past_due 전환:
  - `services/billing/recurring.py`의 `run_grace_expiry(...)`
  - projector 반영 포함
- 사용자 상태 정책:
  - grace 중 `users.plan_status`는 projector 정책대로 `active` 유지
  - past_due 전환 후 제한 상태 projection

## 5) cancel_at_period_end 구현 상태
- `services/billing/recurring.py`의 `run_cancel_effective(...)`
  - `cancel_effective_at <= now` 조건에서 `canceled` 전이
  - `next_billing_at=None`
  - projector 반영
- due selection에서 `cancel_requested`는 다음 주기 청구 대상 제외

## 6) 운영 CLI/worker 구현 상태
- `app.py` CLI 추가:
  - `flask billing-run-recurring [--dry-run] [--subscription-id] [--limit] [--exclude-retry]`
  - `flask billing-run-retry [--dry-run] [--subscription-id] [--limit]`
  - `flask billing-run-grace-expiry [--dry-run] [--subscription-id] [--limit]`
  - `flask billing-run-cancel-effective [--dry-run] [--subscription-id] [--limit]`

## 7) 테스트 결과
- 실행:
  - `.venv/bin/python -m unittest tests.test_billing_recurring tests.test_billing_reconcile_service`
  - `.venv/bin/python -m unittest tests.test_billing_service_layer tests.test_billing_checkout_pipeline tests.test_billing_checkout_routes tests.test_billing_projector tests.test_billing_reconcile_wrappers tests.test_billing_webhook_ingest_service`
- 결과:
  - 총 68개 테스트 PASS
- 신규 고정 항목:
  - 주기 금액 계산(기본/추가 계좌/effective_to 반영)
  - due selection 분기
  - recurring/retry 배치 집계
  - grace 만료 past_due 전환
  - cancel_effective 처리
  - recurring 성공 시 주기 전진
  - addon 실패가 grace를 시작하지 않는 정책 보정

## 8) 아직 남은 항목(오픈 전 필수)
- 스테이징 실결제 E2E
  - recurring success
  - recurring fail -> grace_started -> retry -> past_due
  - cancel_effective 실제 전환
- 프록시/APM/CSP 실측
- 최종 실결제 오픈 GO/NO-GO 재판정

## 주의
- 이번 단계는 **반복 과금 엔진 구현 단계**다.
- 본 문서는 실결제 오픈 가능 판정 문서가 아니다.

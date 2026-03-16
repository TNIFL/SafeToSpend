# BILLING_RECURRING_VERIFICATION

작성일: 2026-03-10  
목적: recurring automation 단계가 "보고서상 완료"가 아닌 "실제 동작"인지 검증

## 1) 테스트 원문 실행 결과

### 명령 1
```bash
.venv/bin/python -m unittest tests.test_billing_recurring tests.test_billing_reconcile_service
```
원문:
```text
.......................
----------------------------------------------------------------------
Ran 23 tests in 0.025s

OK
```
집계:
- 총 실행: 23
- passed: 23
- skipped: 0
- failed: 0
- errors: 0

### 명령 2
```bash
.venv/bin/python -m unittest tests.test_billing_service_layer tests.test_billing_checkout_pipeline tests.test_billing_checkout_routes tests.test_billing_projector tests.test_billing_reconcile_wrappers tests.test_billing_webhook_ingest_service
```
원문:
```text
.............................................
----------------------------------------------------------------------
Ran 45 tests in 0.217s

OK
```
집계:
- 총 실행: 45
- passed: 45
- skipped: 0
- failed: 0
- errors: 0

### 명령 3
```bash
.venv/bin/python -m unittest discover -s tests -p 'test_billing_*.py'
```
원문:
```text
........s........................................[2026-03-10 14:26:21,983] WARNING in billing: [WARN][billing] registration_callback_state_mismatch order=reg_1
...............................[2026-03-10 14:26:22,112] ERROR in startup_checks: [BILLING_STARTUP_CHECK] missing env; missing schema
..........[2026-03-10 14:26:22,123] ERROR in billing_webhook: [ERR][billing] webhook_store_failed type=PAYMENT_STATUS_CHANGED err=RuntimeError
.....
----------------------------------------------------------------------
Ran 94 tests in 0.420s

OK (skipped=1)
```
집계:
- 총 실행: 94
- passed: 93
- skipped: 1
- failed: 0
- errors: 0

skip 사유 확인 원문:
```text
setUpClass (test_billing_concurrency_postgres.BillingConcurrencyPostgresTest) ... skipped 'Postgres DSN이 없어 동시성 검증을 건너뜁니다.'
```

## 2) 핵심 파일 정책 정합성 체크 (PASS/FAIL/불명확)

| 점검 항목 | 결과 | 근거 |
|---|---|---|
| recurring due selection이 중복 recurring attempt를 차단하는가 | PASS | `services/billing/recurring.py:244-251` (`cycle_attempt_already_exists`), retry 중복은 `:210-223` |
| recurring charge가 BillingCustomer.customer_key를 실제 사용하는가 | PASS | `services/billing/recurring.py:416-443` (`_resolve_customer_key` 결과를 `charge_billing_key(customer_key=...)`에 전달) |
| recurring/retry 실패만 grace_started로 가는가 | PASS | `services/billing/reconcile.py:398-426` (`is_recurring_family = attempt_type in {"recurring","retry"}` 조건 하에서만 실패→grace) |
| addon/upgrade(one-off) 실패는 grace를 시작하지 않는가 | PASS | 코드 경로상 `is_recurring_family` 조건 외에는 grace 전이 없음 (`services/billing/reconcile.py:422-426`) + 실DB 보조 검증에서 `addon_proration` 실패 후 `subscription_status=active` 확인 |
| grace 만료 시 past_due 전환이 있는가 | PASS | `services/billing/recurring.py:568-619` (`run_grace_expiry`) |
| cancel_effective 시 canceled/free projection이 있는가 | PASS | `services/billing/recurring.py:632-688` (`run_cancel_effective` + `apply_entitlement_from_subscription_state`) |
| CLI가 실제 서비스 로직을 호출하는가 | PASS | `app.py:310-390` (`billing-run-recurring/retry/grace-expiry/cancel-effective`가 recurring 서비스 호출) |
| next_billing_at 전진(다음 주기 처리) 코드가 존재하는가 | PASS | `services/billing/reconcile.py:372-384` (`_advance_subscription_cycle`) + `:415-416` (reconciled recurring/retry 시 호출) |

## 3) 수동 시나리오 A (정상 recurring)

실행:
```bash
PYTHONPATH=. .venv/bin/python /tmp/recurring_verify_manual.py
```

시나리오 A 결과(실DB):
```json
{
  "subscription_id": 8,
  "dry_run": {
    "due_recurring_count": 1,
    "due_retry_count": 0,
    "skipped_count": 0
  },
  "run": {
    "executed_count": 1,
    "success_count": 1,
    "failure_count": 0,
    "results": [
      {
        "ok": true,
        "subscription_id": 8,
        "payment_attempt_id": 4,
        "order_id": "pay_959668d32c984b79b8569585e7954a2f",
        "status_after": "reconciled",
        "reconciled": true,
        "reconcile_needed": false,
        "due_kind": "recurring"
      }
    ]
  },
  "payment_attempt": {
    "id": 4,
    "attempt_type": "recurring",
    "status": "reconciled",
    "amount_krw": 9900
  },
  "subscription_before": {
    "status": "active",
    "next_billing_at": "2026-03-10T14:58:22.188078+09:00",
    "current_period_start": "2026-02-08T14:59:22.188078+09:00",
    "current_period_end": "2026-03-10T14:59:22.188078+09:00",
    "grace_until": null
  },
  "subscription_after": {
    "status": "active",
    "next_billing_at": "2026-04-10T14:59:22.188078+09:00",
    "current_period_start": "2026-03-10T14:59:22.188078+09:00",
    "current_period_end": "2026-04-10T14:59:22.188078+09:00",
    "grace_until": null
  }
}
```

판정: PASS

## 4) 수동 시나리오 B (recurring 실패 -> grace -> past_due)

실행:
```bash
PYTHONPATH=. .venv/bin/python /tmp/recurring_verify_manual.py
```

시나리오 B 결과(실DB):
```json
{
  "subscription_id": 9,
  "run": {
    "executed_count": 1,
    "success_count": 0,
    "failure_count": 1,
    "results": [
      {
        "ok": false,
        "subscription_id": 9,
        "payment_attempt_id": 5,
        "order_id": "pay_48bc1a66842d44f5b2017b5adcde3a1d",
        "reason": "charge_request_failed"
      }
    ]
  },
  "payment_attempt": {
    "id": 5,
    "attempt_type": "recurring",
    "status": "failed",
    "fail_code": "charge_request_failed"
  },
  "after_fail": {
    "subscription": {
      "status": "grace_started",
      "next_billing_at": "2026-03-10T14:58:22.381723+09:00",
      "current_period_start": "2026-02-08T14:59:22.381723+09:00",
      "current_period_end": "2026-03-10T14:59:22.381723+09:00",
      "grace_until": "2026-03-13T14:59:22.402928+09:00"
    },
    "user": {
      "plan": "pro",
      "plan_code": "basic",
      "plan_status": "active",
      "extra_account_slots": 0
    }
  },
  "grace_expiry_run": {
    "ok": true,
    "dry_run": false,
    "scanned": 1,
    "processed": 1,
    "results": [
      {
        "ok": true,
        "subscription_id": 9,
        "status_after": "past_due",
        "projection_applied": true
      }
    ]
  },
  "after_expiry": {
    "subscription": {
      "status": "past_due",
      "next_billing_at": "2026-03-10T14:58:22.381723+09:00",
      "current_period_start": "2026-02-08T14:59:22.381723+09:00",
      "current_period_end": "2026-03-10T14:59:22.381723+09:00",
      "grace_until": "2026-03-10T14:58:22.435825+09:00"
    },
    "user": {
      "plan": "pro",
      "plan_code": "basic",
      "plan_status": "past_due",
      "extra_account_slots": 0
    }
  }
}
```

추가 보조 검증(one-off 실패는 grace 금지):
```json
{
  "result_status_after": "failed",
  "subscription_status_after": "active",
  "subscription_grace_until": null
}
```

판정: PASS

## 5) 검증 중 확인된 리스크

- 리스크 1 (환경): billing startup check 로그에서 필수 env 누락 경고가 반복 출력됨.
  - 메시지: `TOSS_PAYMENTS_CLIENT_KEY`, `TOSS_PAYMENTS_SECRET_KEY` 누락
  - 영향: 실PG 호출 시점 실패 가능
- 리스크 2 (스키마 드리프트): 검증 전 `billing_payment_attempts.checkout_intent_id` 누락 상태였고, 수동 검증을 위해 `flask db upgrade` 선행 필요했음.
- 리스크 3 (동시성 검증 범위): `test_billing_concurrency_postgres`는 DSN 미설정 환경에서 skip.

## 6) 최종 판단

판정: **몇 가지 수정 후 진행 가능 (CONDITIONAL GO)**

근거:
- 테스트 원문 기준 `billing` 테스트 94개 중 93 pass, 1 skip, fail/error 0.
- 수동 시나리오 A/B가 실DB 상태 전이까지 재현됨.
- 다만 실PG 환경변수 누락과 Postgres 동시성 skip은 다음 단계 전 보완 필요.

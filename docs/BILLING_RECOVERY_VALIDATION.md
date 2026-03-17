# Billing Recovery Validation

실행일: 2026-03-11

## 1) 검증 대상
- orderId 기준 재동기화 (`billing-reconcile --order-id`)
- paymentKey 기준 재동기화 (`billing-reconcile --payment-key`)
- payment event 재처리 (`billing-replay-event`)
- entitlement 재투영 (`billing-reproject-entitlement`)

## 2) 실측 실행

### 2-1. orderId 기준
명령:
- `FLASK_APP=app.py .venv/bin/flask billing-reconcile --order-id pay_959668d32c984b79b8569585e7954a2f --dry-run`
- `FLASK_APP=app.py .venv/bin/flask billing-reconcile --order-id pay_959668d32c984b79b8569585e7954a2f`
결과:
- `reason=already_finalized`
- `projection_applied=false` (duplicate no-op)
판정: PASS

### 2-2. paymentKey 기준
명령:
- `FLASK_APP=app.py .venv/bin/flask billing-reconcile --payment-key pay_bdb023ccfd204c5a8f1d --dry-run`
- `FLASK_APP=app.py .venv/bin/flask billing-reconcile --payment-key pay_bdb023ccfd204c5a8f1d`
결과:
- `reason=already_finalized`
- `projection_applied=false` (duplicate no-op)
판정: PASS

### 2-3. event replay
사전 준비:
- `ingest_payment_event(...)`로 `payment_event_id=5` 생성
명령:
- `FLASK_APP=app.py .venv/bin/flask billing-replay-event --event-id 5 --dry-run`
- `FLASK_APP=app.py .venv/bin/flask billing-replay-event --event-id 5`
결과:
- `payment_event_status=applied`
- 대상 attempt는 이미 `reconciled`라 상태 손상 없음
판정: PASS

### 2-4. entitlement reproject
명령:
- `FLASK_APP=app.py .venv/bin/flask billing-reproject-entitlement --user-pk 338 --dry-run`
- `FLASK_APP=app.py .venv/bin/flask billing-reproject-entitlement --user-pk 338`
- `FLASK_APP=app.py .venv/bin/flask billing-reproject-entitlement --user-pk 338`
결과:
- 1회차: `applied=true`
- 2회차: `duplicate=true`, `applied=false` (멱등 no-op)
판정: PASS

## 3) 결론
- 복구 도구 4종(order/payment/event/reproject) 실제 실행 확인 완료.
- 이미 처리된 건에 대한 재실행 시 no-op/duplicate 경로로 안전하게 종료됨.

## 4) 남은 리스크
- 스테이징 인프라 접근 권한이 없어 실운영 유사 환경에서의 CLI 실행 권한/감사로그 검증은 미실시.

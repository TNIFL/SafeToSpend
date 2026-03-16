# Billing Postgres Concurrency Results

실행일: 2026-03-10

## 1) 실행 환경
- DB: Postgres (로컬 연결)
- 실행 명령:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_pg_concurrency_probe.py --cleanup`
  - `python -m unittest tests.test_billing_concurrency_postgres` (환경변수 DSN 미지정 시 skip)

## 2) Probe 결과
```json
{
  "ok": true,
  "report": {
    "registration_success_race": {
      "ok": true,
      "exchange_calls": 1
    },
    "webhook_transmission_duplicate": {
      "ok": true,
      "duplicate_flags": [false, true]
    },
    "entitlement_log_duplicate": {
      "ok": true,
      "results": ["applied", "duplicate"]
    },
    "registration_success_fail_race": {
      "ok": true,
      "final_status": "billing_key_issued"
    },
    "reconcile_projection_race": {
      "ok": true,
      "result_statuses": ["reconciled", "reconciled"],
      "entitlement_logs": 1
    },
    "projector_source_idempotency": {
      "ok": true,
      "applied_flags": [false, true],
      "log_count": 1
    }
  }
}
```

## 3) 검증 항목별 결론
- 동일 order success 콜백 동시 2회: PASS
  - 외부 교환 함수 호출 1회만 발생
- success/fail 경합: PASS
  - 최종 상태 `billing_key_issued` 유지
- 동일 transmission webhook 중복: PASS
  - 2번째 요청 `ignored_duplicate`
- 동일 source entitlement 변경 경합: PASS
  - DB row 1건 유지(Unique 제약 확인)
- 동일 order reconcile 동시 2회 + projector 반영: PASS
  - 결제 시도 최종 `reconciled`, entitlement log 1건
- 동일 source_id projector 동시 2회: PASS
  - 한 번만 `applied`, 두 번째는 duplicate(no-op)

## 4) 보정 사항
- 경합 probe/테스트 실행 중 `app_context` 종료 후 `db.session.remove()` 호출 오류 발견
- 테스트/스크립트에서 정리 로직 수정 완료
- 추가 하드닝: `get_or_create_billing_customer()`에 IntegrityError 경합 fallback 추가

## 5) 남은 리스크
- `paymentKey` 기준 reconcile 단독 경합은 probe에 직접 추가되지 않았음(동일 로직이 order 기준 경합과 동일 락/멱등 경로를 사용)
- `past_due 전환`과 `retry 성공`의 초근접 경쟁 시나리오는 스테이징 실측이 추가로 필요

# DB Restore Rehearsal Results

작성일: 2026-03-11
목적: 백업 파일에서 실제 복구 가능성 검증

## 1) 사용 백업 파일
- `reports/rehearsals/db_backup_rehearsal_20260311_220959.dump`

## 2) 실행 절차(요약)
1. 임시 복구 DB 생성(`createdb`)
2. `pg_restore`로 백업 파일 복원
3. 핵심 테이블 row count 확인
4. 임시 DB 삭제(`dropdb`)

## 3) 실행 결과
- 실행 시각: 2026-03-11 22:10 (KST) 전후
- 결과: `SUCCESS`
- 임시 DB: `s2s_restore_rehearsal_20260311_221025`
- 정리: `RESTORE_DB_CLEANUP=dropped`

원문 핵심 로그:
- `RESTORE_STATUS=success`
- `COUNT_users=96`
- `COUNT_billing_customers=10`
- `COUNT_billing_methods=15`
- `COUNT_billing_checkout_intents=6`
- `COUNT_billing_payment_attempts=8`
- `COUNT_billing_subscriptions=10`
- `COUNT_billing_subscription_items=14`
- `COUNT_evidence_items=744`

## 4) 검증 판정
- 백업 파일 기반 복구: 가능
- billing/user/evidence 핵심 테이블 접근: 가능
- 리허설 후 임시 DB 정리: 성공

## 5) 한계/주의
- 운영 DB 직접 복구는 수행하지 않음(안전 정책)
- 운영 복구 시간(RTO), 시점 손실(RPO) 기준은 별도 확정 필요

# Backup and Recovery Runbook

작성일: 2026-03-11
목적: DB/업로드/billing 도메인 복구 절차 최소 기준 정의

## 1) 백업 대상 및 현재 준비 상태

| 대상 | 현재 상태 | 근거 |
|---|---|---|
| PostgreSQL DB 백업 | 리허설 1회 성공(로컬) | `docs/DB_BACKUP_REHEARSAL_RESULTS.md` |
| PostgreSQL DB 복구 | 리허설 1회 성공(임시 DB) | `docs/DB_RESTORE_REHEARSAL_RESULTS.md` |
| 업로드 파일 백업/복구 | 리허설 1회 성공(샘플 카운트 일치) | `docs/FILE_BACKUP_RECOVERY_RESULTS.md` |
| 운영 백업 자동화(스케줄/보존/암호화) | 미확정 | 인프라 권한 필요 |

## 2) 표준 절차(운영용)

### 2-1. DB 백업
1. 배포 전/정기 스케줄로 `pg_dump -Fc` 실행
2. 백업 파일 무결성/크기 점검
3. 원격 보관(암호화 + 접근통제)

권장 명령(마스킹 예시):
```bash
pg_dump -h <host> -p <port> -U <user> -d <db> -Fc -f <backup_file.dump>
```

### 2-2. DB 복구(리허설/장애)
1. 별도 DB 생성
2. `pg_restore`로 복원
3. 핵심 테이블 row count/샘플 조회
4. billing 재동기화/재투영 필요 시 실행

권장 명령(마스킹 예시):
```bash
createdb -h <host> -p <port> -U <user> <restore_db>
pg_restore -h <host> -p <port> -U <user> -d <restore_db> <backup_file.dump>
```

### 2-3. 업로드 파일 백업/복구
1. `uploads/evidence` 포함 경로 압축/스냅샷
2. 복구 경로로 해제
3. 백업 전/후 파일 수 및 샘플 파일 접근 확인

## 3) 결제/구독 복구 도구
- orderId 재동기화: `flask billing-reconcile --order-id <ORDER_ID>`
- paymentKey 재동기화: `flask billing-reconcile --payment-key <PAYMENT_KEY>`
- webhook 이벤트 재처리: `flask billing-replay-event --event-id <EVENT_ID>`
- entitlement 재투영: `flask billing-reproject-entitlement --user-pk <USER_ID>`

참조: `docs/BILLING_RECOVERY_VALIDATION.md`

## 4) 장애 유형별 복구 순서(최소)

### A. DB 장애/롤백 필요
1. 쓰기 트래픽 제한(maintenance/read-only)
2. 마지막 정상 백업 시점 확인
3. DB 복원 수행
4. `flask db upgrade` 및 `flask billing-startup-check`
5. billing 재동기화/재투영으로 정합성 복원
6. 점진적 트래픽 복귀

### B. 업로드 파일 손실
1. 손실 범위(기간/사용자/경로) 파악
2. 스토리지 백업에서 복원
3. evidence 메타데이터와 실제 파일 샘플 대조
4. 불일치 사용자 공지/재첨부 안내

### C. 결제 상태 꼬임
1. 대상 orderId/paymentKey/event 식별
2. `billing-reconcile --dry-run`
3. 필요 시 reconcile/replay/reproject 순서로 복구
4. `entitlement_change_logs` 중복/누락 확인

## 5) 복구 완료 체크
- `/health` 정상
- billing startup check 통과
- 최근 결제 샘플의 `attempt/subscription/users` 정합
- 증빙 파일 샘플 접근 정상

## 6) 오픈 전 남은 필수 과제
1. 운영/스테이징 백업 스케줄 자동화 확정
2. 백업 보존기간/암호화/접근권한 정책 확정
3. 운영 환경에서 동일 절차 리허설 1회 이상 추가 실행

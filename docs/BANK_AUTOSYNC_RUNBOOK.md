# BANK_AUTOSYNC_RUNBOOK

작성일: 2026-03-14

## 1) 목적
- 계좌 동기화를 사용자 수동 새로고침(`/bank/sync`)에만 의존하지 않고, 배치/스케줄러로 자동 실행한다.
- 수동/자동 모두 동일한 공용 실행부를 사용한다.

## 2) Source of Truth
- 동기화 주기(interval): `services.plan.sync_interval_minutes(user_pk)`
- 현재 코드 기준:
  - Basic: `240분`
  - Pro: `60분`
  - Free/비활성: `None` (자동 동기화 대상 아님)
- 주기 하드코딩 금지.

## 3) 실행 구조
- 공용 서비스: `services.bank_sync_scheduler`
  - `run_manual_bank_sync_batch(...)`
  - `run_due_bank_sync_batch(...)`
- 수동 라우트: `POST /bank/sync` → `run_manual_bank_sync_batch(...)`
- 자동 실행체: `flask bank-sync-run-due`

## 4) CLI 사용법

### 도움말
```bash
FLASK_APP=app.py .venv/bin/flask bank-sync-run-due --help
```

### due 대상 점검(dry-run)
```bash
FLASK_APP=app.py .venv/bin/flask bank-sync-run-due --dry-run --limit 20
```

### 실제 실행
```bash
FLASK_APP=app.py .venv/bin/flask bank-sync-run-due --limit 20
```

### 옵션
- `--dry-run`: 실제 sync 호출 없이 due 대상만 집계
- `--limit N`: 최대 처리 계좌 수 제한
- `--account-id ID`: 특정 `bank_account_links.id`만 대상
- `--user-pk ID`: 특정 사용자만 대상

## 5) 종료코드 정책
- `0`: 정상 완료(실패 계좌 없음)
- `2`: 실행은 됐지만 실패 계좌 존재
- `1`: 예외/실행 자체 실패

## 6) 운영 적용(권장)
- 운영 기본 경로: **외부 scheduler/cron이 CLI를 주기 호출**
- 예시(cron, 매 5분):
```cron
*/5 * * * * cd /path/to/app && FLASK_APP=app.py .venv/bin/flask bank-sync-run-due --limit 50 >> /var/log/bank_autosync.log 2>&1
```
- 매 5분 호출이어도 내부 due 판정은 사용자 플랜 interval(60/240) 기준으로 동작한다.

## 7) 로컬 개발용 보조 스케줄러(선택)
- 기본값: 비활성(off)
- 활성화:
```bash
export BANK_AUTOSYNC_ENABLE_LOCAL_SCHEDULER=true
export BANK_AUTOSYNC_LOCAL_TICK_SECONDS=180
export BANK_AUTOSYNC_LOCAL_LIMIT=50
```
- 주의:
  - 로컬/단일 프로세스 검증 목적.
  - 운영에서는 외부 scheduler + CLI 경로 권장.
  - 멀티 프로세스 환경에서 in-process 스케줄러는 중복 실행 위험이 있으므로 사용 금지.

## 8) 중복 실행 방지/실패 격리
- 계좌 단위 advisory lock(`pg_try_advisory_lock`) 사용.
- lock 획득 실패 계좌는 `skipped_locked`로 건너뛴다.
- 계좌 단위 실패는 batch 전체를 중단하지 않고 다음 계좌 진행.

## 9) 점검 포인트
- dry-run에서 `due_links`가 기대값과 맞는지
- 실제 실행에서 `processed_links/success_count/failed_count`가 정상 집계되는지
- `failed_count>0`이면 스케줄러가 재시도 가능하도록 종료코드/로그 감시 연결


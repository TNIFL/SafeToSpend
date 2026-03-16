# BANK_AUTOSYNC_STAGE_REPORT

작성일: 2026-03-14  
범위: 이슈5(계좌 자동 동기화) 전용

## 티켓 1: 수동 경로 / interval source of truth 확정

| 항목 | 확정 내용 | 코드 근거 |
|---|---|---|
| 수동 동기화 경로 | `POST /bank/sync` | `routes/web/bank.py` |
| 수동 동기화 호출 함수 | 일반: `sync_popbill_for_user(user_id)`, 3개월: `sync_popbill_backfill_max_3m(user_id, link_id=...)` | `routes/web/bank.py` |
| 실 import 실행 핵심 | `sync_popbill_for_user(...)` 내부에서 계좌별 팝빌 요청/파싱/Transaction upsert | `services/import_popbill.py` |
| 계좌 단위 실행 분기 | `link_id` 전달 시 해당 계좌만 대상, 미전달 시 user의 활성 링크 전체 | `services/import_popbill.py` |
| interval source of truth | `services.plan.get_user_entitlements(...).sync_interval_minutes` / `services.plan.sync_interval_minutes(...)` | `services/plan.py` |
| 현재 기본 interval 값 | Basic `240분`, Pro `60분`, Free/비활성 `None` | `services/plan.py::_sync_interval_minutes` |
| 자동 실행체 부재 근거 | 스케줄러/배치/CLI 자동 실행 경로 없음. 현재는 HTTP 수동 트리거 중심 | `app.py`, `routes/web/bank.py` |

### 티켓 1 결론
- 자동 동기화 구현 시 **수동과 동일한 핵심 실행부(`sync_popbill_for_user` 계열)** 를 공용으로 사용해야 함.
- interval은 하드코딩하지 않고 **`services.plan` 권한 계산 결과**를 source of truth로 사용해야 함.
- 현재 코드 기준 “30분 자동”은 source of truth가 아니며, 실제 기본값은 60/240임.

---

## 티켓 2: 수동/자동 공용 실행 서비스 추출

### 변경 사항
- 신규: `services/bank_sync_scheduler.py`
  - `run_bank_sync_batch(...)` (공용 핵심 실행부)
  - `run_manual_bank_sync_batch(...)` (수동 경로용)
  - `run_due_bank_sync_batch(...)` (자동 due 실행용)
- 변경: `routes/web/bank.py`
  - `/bank/sync`가 직접 `sync_popbill_*` 호출하지 않고 `run_manual_bank_sync_batch` 호출

### 결과
- 수동/자동 모두 동일 공용 실행부를 타도록 정리됨.
- 배치 결과 공통 스키마(대상 수/성공/실패/건너뜀/계좌별 결과) 확보.

---

## 티켓 3: due 선별 + 중복 실행 방지

### 구현 방식
- due 판정:
  - 활성 계좌 링크 기준
  - 사용자별 `sync_interval_minutes`로 판정
  - `last_synced_at`이 없거나 interval 경과 시 due
- 중복 실행 방지:
  - PostgreSQL advisory lock(`pg_try_advisory_lock`) per link
  - lock 중 계좌는 `skipped_locked`
- 실패 격리:
  - 계좌 단위 try/except
  - 실패해도 다음 계좌 계속 진행
- dry-run:
  - 외부 API 호출 없이 due 후보만 집계

### 스키마 변경 여부
- 없음 (기존 `last_synced_at` + advisory lock 사용)

---

## 티켓 4: 자동 실행체(CLI) 추가

### 추가된 CLI
- `flask bank-sync-run-due`
  - `--dry-run`
  - `--limit`
  - `--account-id`
  - `--user-pk`

### 검증 결과
- `FLASK_APP=app.py .venv/bin/flask bank-sync-run-due --help` 정상 출력
- dry-run 예시 출력:
```json
{"mode":"auto_due","dry_run":true,"total_links":3,"due_links":3,"processed_links":3,"success_count":3,"failed_count":0,"skipped_interval_count":0,"skipped_plan_count":0,"skipped_lock_count":0,"skipped_limit_count":0,"inserted_rows_total":0,"duplicate_rows_total":0,"failed_rows_total":0}
```
- 실제 실행 예시 출력:
```json
{"mode":"auto_due","dry_run":false,"total_links":3,"due_links":3,"processed_links":1,"success_count":1,"failed_count":0,"skipped_interval_count":0,"skipped_plan_count":0,"skipped_lock_count":0,"skipped_limit_count":2,"inserted_rows_total":0,"duplicate_rows_total":1,"failed_rows_total":0}
```

---

## 티켓 5: 로컬 opt-in 보조 스케줄러(선택)

### 구현
- `services.bank_sync_scheduler.start_local_bank_sync_scheduler(app)`
- 활성 조건:
  - `BANK_AUTOSYNC_ENABLE_LOCAL_SCHEDULER=true`
  - 운영 환경(`production/staging`)이 아님
  - 웹 프로세스(로컬 run)에서만
- tick 주기:
  - `BANK_AUTOSYNC_LOCAL_TICK_SECONDS` (기본 180, 최소 30)
- 처리 limit:
  - `BANK_AUTOSYNC_LOCAL_LIMIT` (기본 50)

### 운영 가이드
- 운영 기본 경로는 여전히 외부 scheduler/cron + CLI.
- 로컬 보조 스케줄러는 개발 검증용.

---

## 티켓 6: 통합 검증 결과

### 테스트
실행 명령:
```bash
.venv/bin/python -m unittest tests.test_bank_autosync_scheduler tests.test_bank_autosync_cli
```

결과:
- 8 tests, OK

### 테스트 커버
- due 계좌만 선별되는지
- interval 미도래/플랜 미지원 skip
- lock 중복 실행 skip
- 계좌 단위 실패 격리
- 수동 라우트가 공용 실행부를 사용하는지
- CLI help 노출/옵션 파싱/dry-run/실패 종료코드

### 최종 판정
- **자동 동기화 미구현 해소**
  - 수동-only 상태에서 벗어나 CLI 실행체 + due 선별 + 중복 방지 + 실패 격리까지 반영됨.

### 남은 리스크
- Popbill API rate limit/외부 장애에 따른 실패 재시도 정책은 운영 모니터링과 연계 필요.
- 멀티프로세스 환경에서는 외부 scheduler 주기 설계와 종료코드 기반 알림 연결 필요.

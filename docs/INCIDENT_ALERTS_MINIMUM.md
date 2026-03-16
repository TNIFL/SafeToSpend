# Incident Alerts Minimum

작성일: 2026-03-11  
목적: 1인 운영 기준 최소 장애 감지/알림 체계 정의

## 장애 유형별 현재 상태

| 장애 유형 | 현재 감지 가능 여부 | 현재 알림 경로 | 이상적인 알림 경로 | 지금 당장 필요 |
|---|---|---|---|---|
| 앱 500 급증 | 일부 가능 | 앱 로그 수동 확인, `/health` 응답 확인([app.py](/Users/tnifl/Desktop/SafeToSpend/app.py)) | APM 오류율 알림 + 5분 임계치 기반 푸시 | 예 |
| DB 연결 실패 | 일부 가능 | startup check/런타임 에러 로그([services/billing/startup_checks.py](/Users/tnifl/Desktop/SafeToSpend/services/billing/startup_checks.py)) | DB 연결 실패 즉시 알림(메신저/이메일) | 예 |
| 결제/정기청구 실패 급증 | 일부 가능 | `billing-run-recurring/retry` 결과 JSON 수동 확인([app.py](/Users/tnifl/Desktop/SafeToSpend/app.py)) | 결제 실패율 임계치 알림 + 일별 리포트 | 예 |
| 계좌 동기화 실패 | 일부 가능 | Popbill 연동 예외 로그/수동 점검 | 동기화 실패율 알림 + 대상 사용자 카운트 | 예 |
| 영수증 처리/배치 실패 | 일부 가능 | `scripts/receipt_worker.py` 오류/heartbeat 로그 | worker fail streak 알림 | 예 |
| 세무사 전달 패키지 생성 실패 | 일부 가능 | 사용자 화면 에러/로그 수동 확인([services/tax_package.py](/Users/tnifl/Desktop/SafeToSpend/services/tax_package.py)) | 패키지 실패 이벤트 알림 | 중 |
| 백업 실패 | 현재 없음 | 없음 | 백업 작업 실패 즉시 알림 | 예(오픈 차단) |

## 최소 운영 알림 체크리스트 (MVP)

| 이벤트 | 임계치 | 알림 채널 | 확인 주체 | 확인 주기 |
|---|---|---|---|---|
| 앱 500 비율 | 5분간 1% 초과 또는 연속 10건 | 메신저(권장) 또는 이메일 | 운영자 1인 | 상시 |
| `/health` 실패 | 2회 연속 실패 | 메신저 또는 전화/SMS 대체 | 운영자 1인 | 1분~5분 |
| 결제 실패율 급증 | 1시간 실패율 20% 초과 | 메신저 + 일일 요약 | 운영자 1인 | 시간별 |
| recurring 배치 실패 | 배치 1회 실패 또는 0건 반복 이상 | 메신저 | 운영자 1인 | 배치마다 |
| receipt worker 중단 | heartbeat 미수신 5분 | 메신저 | 운영자 1인 | 상시 |
| 백업 실패 | 1회 실패 | 메신저 + 이메일 | 운영자 1인 | 백업마다 |

## 현재 코드 기준 즉시 실행 가능한 수동 점검
1. 결제 startup/scheme: `FLASK_APP=app.py .venv/bin/flask billing-startup-check`
2. 결제 상태 재검증: `FLASK_APP=app.py .venv/bin/flask billing-reconcile --order-id <ID> --dry-run`
3. recurring dry-run: `FLASK_APP=app.py .venv/bin/flask billing-run-recurring --dry-run`
4. 서비스 헬스: `GET /health`

## 운영 차단 리스크
- 외부 알림 시스템 연동 부재(현재 수동 점검 의존)
- 백업 실패 알림 경로 부재
- 프록시/APM 권한 미확보 시 실시간 탐지 지연 가능

## 오픈 전 최소 요구
- 위 표의 `지금 당장 필요=예` 항목에 대해 최소 하나의 자동 알림 채널 확정
- 채널 미확정 시 `NO-GO` 또는 최소 `CONDITIONAL GO` 유지

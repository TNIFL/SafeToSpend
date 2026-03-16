# Ops Tools Inventory

최초 작성일: 2026-03-11  
최신 검토일: 2026-03-12 (도구 목록/우선순위 변경 없음)
목적: 현재 운영/복구 도구 현황과 공백 우선순위 정리

## 1) 현재 존재하는 운영 도구

| 도구 | 목적 | 입력값 | dry-run | 실제 실행 검증 | 문서화 |
|---|---|---|---|---|---|
| `flask billing-startup-check` | 결제 필수 env/스키마 사전 점검 | 없음 | 해당 없음 | 있음 | [docs/BILLING_DEPLOY_CHECKLIST.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_DEPLOY_CHECKLIST.md) |
| `flask cleanup-billing-registration-attempts` | 등록 시도 정리/abandoned 처리 | `--abandoned-hours`, `--retention-days` | 있음 | 부분 | [docs/BILLING_DEPLOY_CHECKLIST.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_DEPLOY_CHECKLIST.md) |
| `flask billing-reconcile` | orderId/paymentKey 기준 재동기화 | `--order-id` 또는 `--payment-key` | 있음 | 있음 | [docs/BILLING_RECOVERY_VALIDATION.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECOVERY_VALIDATION.md) |
| `flask billing-replay-event` | webhook event 재처리 | `--event-id`/`--transmission-id` | 있음 | 있음 | [docs/BILLING_RECOVERY_VALIDATION.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECOVERY_VALIDATION.md) |
| `flask billing-reproject-entitlement` | users projection 재투영 | `--user-pk` | 있음 | 있음 | [docs/BILLING_RECOVERY_VALIDATION.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECOVERY_VALIDATION.md) |
| `flask billing-run-recurring` | 정기청구 후보 실행 | `--subscription-id`, `--limit` | 있음 | 부분 | [docs/BILLING_RECURRING_VERIFICATION.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECURRING_VERIFICATION.md) |
| `flask billing-run-retry` | grace 재시도 실행 | `--subscription-id`, `--limit` | 있음 | 부분 | [docs/BILLING_RECURRING_STAGE_REPORT.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECURRING_STAGE_REPORT.md) |
| `flask billing-run-grace-expiry` | grace 만료 처리 | `--subscription-id`, `--limit` | 있음 | 부분 | [docs/BILLING_RECURRING_VERIFICATION.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECURRING_VERIFICATION.md) |
| `flask billing-run-cancel-effective` | 기간종료 해지 반영 | `--subscription-id`, `--limit` | 있음 | 부분 | [docs/BILLING_RECURRING_STAGE_REPORT.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECURRING_STAGE_REPORT.md) |
| `scripts/billing_data_audit.py` | 결제 오염 데이터 진단 | `--user-pk`, `--limit` | 조회 전용 | 있음 | [docs/BILLING_DATA_AUDIT.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_DATA_AUDIT.md) |
| `scripts/billing_data_recovery.py` | 안전 케이스 오염 복구 | `--user-pk`, `--limit`, `--apply` | 있음 | 있음 | [docs/BILLING_DATA_RECOVERY.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_DATA_RECOVERY.md) |
| `scripts/predeploy_check.py` | 공식참조/사전 배포 점검 | 없음 | 해당 없음 | 부분 | [docs/REFERENCE_UPDATE_RUNBOOK.md](/Users/tnifl/Desktop/SafeToSpend/docs/REFERENCE_UPDATE_RUNBOOK.md) |
| `scripts/receipt_worker.py` | 영수증 배치 처리 | `--once`, `--max-items` 등 | 부분(`--once`) | 부분 | [docs/DEV_TESTING.md](/Users/tnifl/Desktop/SafeToSpend/docs/DEV_TESTING.md) |

## 2) 현재 없는(또는 미흡한) 도구

| 항목 | 상태 | 우선순위 |
|---|---|---|
| DB 백업 성공/실패 검증 도구 | 없음 | 상 |
| 업로드 파일 백업/복구 검증 도구 | 없음 | 상 |
| 서비스 전역 데이터 정합성 종합 스캐너(billing 외 포함) | 없음 | 상 |
| 계좌 동기화 재실행/재처리 운영 CLI | 미흡(부분 수동) | 중 |
| 패키지 산출물 무결성 점검/재생성 도구 | 없음 | 중 |
| 운영자 전용 통합 조회/복구 UI | 미흡(CLI 중심) | 중 |

## 3) 우선순위 제안

### 상
- 백업 검증 자동화 도구
- DB+업로드 복구 리허설 스크립트
- billing 외 정합성 스캐너

### 중
- 계좌 동기화 재처리 CLI
- 패키지 무결성/재생성 CLI

### 하
- 운영자 UI 고도화(우선은 CLI+런북으로 대응)

## 4) 결론
- 결제 도메인 복구 도구는 비교적 잘 갖춰져 있음.
- 하지만 “서비스 전체 운영” 관점에서는 백업/복구 검증 도구가 가장 큰 공백이다.

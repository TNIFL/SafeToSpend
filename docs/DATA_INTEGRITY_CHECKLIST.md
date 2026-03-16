# Data Integrity Checklist

작성일: 2026-03-11  
목적: 오픈 후 조용한 데이터 오작동을 조기 탐지하기 위한 최소 점검 항목 정의

## 정합성 점검 항목

| 점검 항목 | 위험도 | 점검 주기 | 자동화 가능 여부 | 수동 확인 방법 |
|---|---|---|---|---|
| 1) active 구독인데 usable billing_method 없음 | 상 | 일 1회 | 높음(기존 스크립트) | `scripts/billing_data_audit.py` 결과에서 `registration_active_gap`, `ready_intent_unusable_method` 확인 |
| 2) users entitlement와 subscription 상태 불일치 | 상 | 일 1회 | 중간(현재 일부) | `billing-reproject-entitlement --dry-run`, `billing-reconcile --dry-run` 샘플 대조 |
| 3) ready_for_charge intent + unusable billing_method_id | 상 | 일 1회 | 높음(기존 스크립트) | `scripts/billing_data_audit.py --limit 300` |
| 4) recurring due인데 payment_attempt 없음 | 상 | 일 1~2회 | 중간(추가 쿼리 필요) | `billing-run-recurring --dry-run` 결과의 `due_found/skipped` 확인 |
| 5) grace_started인데 grace_until 없음 | 상 | 일 1회 | 중간(추가 쿼리 필요) | subscription 상태 샘플 조회 + `billing-run-grace-expiry --dry-run` |
| 6) past_due인데 users.plan_status 미반영 | 상 | 일 1회 | 중간(추가 쿼리 필요) | subscription/user projection 비교, 필요 시 `billing-reproject-entitlement --dry-run` |
| 7) evidence 상태와 실제 파일 불일치 | 상 | 일 1회 | 낮음(신규 스크립트 필요) | evidence 메타와 파일 경로 존재 여부 샘플 점검 |
| 8) 패키지 생성 이력과 실제 산출물 불일치 | 중 | 주 2~3회 | 낮음(신규 스크립트 필요) | 패키지 생성 로그/다운로드 실패 리포트 샘플 확인 |
| 9) 계좌 연동 상태와 entitlement 불일치 | 상 | 일 1회 | 중간(부분 구현) | bank 링크 수 vs plan limit/extra slots 비교(`services/plan.py`, bank 화면) |

## 즉시 실행 가능한 점검 명령
1. 오염 진단: `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --limit 300`
2. 보수 복구 dry-run: `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300`
3. recurring 대상 확인: `FLASK_APP=app.py .venv/bin/flask billing-run-recurring --dry-run`
4. grace 만료 후보 확인: `FLASK_APP=app.py .venv/bin/flask billing-run-grace-expiry --dry-run`
5. projection 점검: `FLASK_APP=app.py .venv/bin/flask billing-reproject-entitlement --user-pk <ID> --dry-run`

## 자동화 후보 우선순위

### 우선 구현(상)
- subscription ↔ users projection 정합성 점검 스크립트
- evidence 파일 실재성 점검 스크립트
- recurring due 대비 attempt 생성 여부 점검 스크립트

### 차순위(중)
- 패키지 산출물 무결성 점검
- 계좌 연동 수/플랜 제한 불일치 자동 리포트

## 운영 원칙
- 점검 결과는 “0건”이어도 기록
- 자동 복구는 안전 케이스만, 나머지는 수동 검토
- 정합성 이슈 발견 시 사용자 공지 필요 여부를 함께 판단

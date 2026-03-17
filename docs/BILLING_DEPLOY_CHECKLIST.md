# Billing Deploy Checklist

작성일: 2026-03-11

## 1) 배포 전 필수 점검
- `FLASK_APP=app.py .venv/bin/flask db upgrade`
- `FLASK_APP=app.py .venv/bin/flask billing-startup-check`
- 필수 billing 스키마 존재 확인
- 민감 로그 마스킹 설정 확인(authKey/paymentKey/querystring raw 미노출)

## 2) 필수 환경변수
- `TOSS_PAYMENTS_CLIENT_KEY`
- `TOSS_PAYMENTS_SECRET_KEY`
- `BILLING_KEY_ACTIVE_VERSION`
- `BILLING_KEY_ENCRYPTION_SECRET` 또는 버전별 키
- `BILLING_GUARD_MODE` (`strict` 권장)

## 3) 스테이징 실측 선행 입력값(현재 공백)
- `STAGING_DOMAIN` (미설정)
- `STAGING_BASE_URL` (미설정)
- `BILLING_WEBHOOK_URL` (미설정)

위 3개가 없으면 실브라우저 E2E 및 webhook 실측은 진행 불가.

## 4) 인프라 권한 필요 목록
- reverse proxy access log 조회 권한
- APM/에러수집 콘솔 조회 권한
- CSP 설정 조회(필요 시 수정) 권한

## 5) 운영 복구 명령
- `flask billing-reconcile --order-id <ORDER_ID>`
- `flask billing-reconcile --payment-key <PAYMENT_KEY>`
- `flask billing-replay-event --event-id <EVENT_ID>`
- `flask billing-reproject-entitlement --user-pk <USER_ID>`

## 6) recurring 운영 명령
- `flask billing-run-recurring --dry-run`
- `flask billing-run-retry --dry-run`
- `flask billing-run-grace-expiry --dry-run`
- `flask billing-run-cancel-effective --dry-run`

## 7) 오픈 판정 게이트
- registration/checkout/payment 실브라우저 2개 이상 시나리오 검증
- webhook/duplicate/refresh/세션없음 검증
- 프록시/APM/CSP 실측 완료
- 백업/복구 리허설 기록 존재
- 정책 문서 공개 경로 확정

하나라도 미완료면 실오픈은 최소 `CONDITIONAL GO` 또는 `NO-GO`.

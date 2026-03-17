# Billing E2E Checklist (Staging)

작성일: 2026-03-11
목적: 스테이징 실브라우저 결제 검증 전제/절차 확정

## 1) 전제 조건 상태

| 항목 | 필요값/권한 | 현재 상태 | 비고 |
|---|---|---|---|
| `TOSS_PAYMENTS_CLIENT_KEY` | set | 충족 | 로컬 `.env` 확인 완료 |
| `TOSS_PAYMENTS_SECRET_KEY` | set | 충족 | 로컬 `.env` 확인 완료 |
| `BILLING_KEY_ENCRYPTION_SECRET` | set | 충족 | 로컬 `.env` 확인 완료 |
| `STAGING_DOMAIN` | 실제 도메인 | 미충족 | 값 미설정 |
| `STAGING_BASE_URL` | 실제 base URL | 미충족 | 값 미설정 |
| `BILLING_WEBHOOK_URL` | webhook 공개 URL | 미충족 | 값 미설정 |
| 스테이징 DB 조회 권한 | read 권한 | 미확인 | 접근 권한 필요 |
| 프록시 access log 권한 | 조회 권한 | 미확인 | 인프라 권한 필요 |
| APM 조회 권한 | 조회 권한 | 미확인 | 인프라 권한 필요 |
| CSP 설정 조회 권한 | 정책 확인 권한 | 미확인 | 인프라 권한 필요 |

## 2) URL 템플릿
- registration success: `<STAGING_BASE_URL>/dashboard/billing/register/success`
- registration fail: `<STAGING_BASE_URL>/dashboard/billing/register/fail`
- payment success: `<STAGING_BASE_URL>/dashboard/billing/payment/success`
- payment fail: `<STAGING_BASE_URL>/dashboard/billing/payment/fail`
- webhook: `<BILLING_WEBHOOK_URL>` (일반적으로 `/api/billing/webhook`)

## 3) 실측 시나리오 (우선순위)
1. free -> basic 최초 구독
2. basic -> pro 업그레이드(또는 add-on 구매)
3. registration fail
4. success refresh 무해성
5. 세션 없는 registration callback
6. webhook 수신 + duplicate 무해성

## 4) 시나리오별 필수 기록 항목
- 시작 사용자 상태
- 클릭한 CTA/폼
- registration 창 노출 여부
- processing 자동 confirm 여부
- 원래 페이지 복귀 여부
- 성공/실패 토스트
- DB 변화
  - `billing_checkout_intents`
  - `billing_methods`
  - `billing_payment_attempts`
  - `billing_subscriptions`
  - `billing_subscription_items`
  - `entitlement_change_logs`
  - `users(plan_code, plan_status, extra_account_slots)`
- PASS/FAIL/미실시 및 사유

## 5) 실행 순서
1. `flask db upgrade`
2. `flask billing-startup-check`
3. registration success/fail
4. checkout start -> processing -> confirm -> success/fail UX
5. webhook/duplicate/refresh/세션없음
6. 결과를 `docs/BILLING_E2E_RESULTS_STAGING.md`에 기록

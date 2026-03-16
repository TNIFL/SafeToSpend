# Billing E2E Results (Staging)

작성일: 2026-03-11
목적: 실브라우저 기준 결제 흐름 검증 결과 기록

## 1) 실행 전제 확인
- `TOSS_PAYMENTS_CLIENT_KEY`: set
- `TOSS_PAYMENTS_SECRET_KEY`: set
- `BILLING_KEY_ENCRYPTION_SECRET`: set
- `STAGING_DOMAIN`: missing
- `STAGING_BASE_URL`: missing
- `BILLING_WEBHOOK_URL`: missing
- 프록시/APM/CSP 권한: 미확인

## 2) 시나리오 결과

### A. free -> basic 최초 구독
- 결과: `미실시`
- 사유: 실제 스테이징 도메인/결제창 진입 URL 부재
- 필요한 입력값: `STAGING_DOMAIN`, `STAGING_BASE_URL`

### B. basic -> pro 업그레이드 또는 add-on 구매
- 결과: `미실시`
- 사유: A 시나리오 미실행으로 선행 상태(basic) 준비 불가
- 필요한 입력값: 스테이징 URL + 테스트 계정

### C. registration success/fail
- 결과: `미실시`
- 사유: 토스 리다이렉트 가능한 스테이징 URL 부재

### D. payment success/fail UX
- 결과: `미실시`
- 사유: 실결제 콜백 URL 부재

### E. success refresh 무해성
- 결과: `미실시`
- 사유: 성공 콜백 실측 전제 미충족

### F. 세션 없는 callback
- 결과: `미실시`
- 사유: callback 실측 전제 미충족

### G. webhook 수신/duplicate
- 결과: `미실시`
- 사유: webhook 공개 URL/재전송 경로 미확정

## 3) 대체 확인(로컬)
- `flask billing-startup-check` 통과 (`ok`, mode=`warn`)
- 결제 필수 키 env 존재 확인
- 로컬 결제/복구 테스트 결과는 별도 문서 참조
  - `docs/BILLING_RECURRING_VERIFICATION.md`
  - `docs/BILLING_RECOVERY_VALIDATION.md`

## 4) 결론
- 스테이징 실브라우저 E2E는 아직 증거를 만들지 못함
- 현재 문서 기준 실결제 오픈 판정 근거로는 부족
- 다음 단계: 스테이징 URL/권한 확정 후 동일 체크리스트 재실행

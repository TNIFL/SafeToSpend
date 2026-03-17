# Billing Infra Validation (Proxy / APM / CSP)

작성일: 2026-03-11
목적: 앱 외부 인프라에서 민감값 노출/CSP 차단 여부 실측

## 1) 항목별 결과

| 항목 | 상태 | 확인 방법 | 결과 |
|---|---|---|---|
| 앱 로그 민감값 마스킹(authKey/paymentKey/query raw) | PASS | 코드/로컬 로그 확인 | raw 민감값 출력 없음 |
| callback 라우트에서 full URL 로깅 금지 | PASS | 코드 경로 검토 | 마스킹 경로 사용 |
| reverse proxy access log querystring 노출 | 미실시 | 프록시 로그 직접 조회 필요 | 권한 미보유 |
| APM query param raw 노출 | 미실시 | APM 콘솔 조회 필요 | 권한 미보유 |
| CSP가 토스 스크립트/프레임 차단 여부 | 미실시 | 스테이징 브라우저 + 응답헤더 확인 필요 | 스테이징 URL 미확정 |

## 2) 미실시 항목 조치 요청
1. 프록시 담당자: billing callback 경로 querystring 비기록/마스킹 설정 확인
2. APM 담당자: URL query capture 마스킹/비활성 정책 확인
3. 인프라/웹 담당자: CSP에서 토스 도메인 허용 정책 점검 및 실브라우저 확인

## 3) 오픈 영향
- 인프라 레벨 실측이 비어 있어 민감 쿼리 유출 리스크 판정이 불완전함
- 이 항목은 실오픈 GO를 막는 차단 리스크로 유지

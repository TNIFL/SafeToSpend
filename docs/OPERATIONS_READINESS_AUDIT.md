# Operations Readiness Audit

작성일: 2026-03-11  
대상: SafeToSpend 운영 준비 상태(코드/문서/도구 기준)

## 점검 기준
- 상태 분류: `있음` / `일부 있음` / `없음` / `환경 권한 필요`
- 중요도: `상` / `중` / `하`
- 근거는 현재 코드/문서 경로만 사용

## 운영 필수 항목 전수 점검

| 항목 | 현재 상태 | 근거 파일/기능/문서 | 부족한 점 | 오픈 전 중요도 |
|---|---|---|---|---|
| 1) 장애 감지/알림 | 일부 있음 | [app.py](/Users/tnifl/Desktop/SafeToSpend/app.py) `/health`, `billing-startup-check`; [services/admin_ops.py](/Users/tnifl/Desktop/SafeToSpend/services/admin_ops.py); [docs/BILLING_GO_NO_GO_REPORT.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_GO_NO_GO_REPORT.md) | 외부 알림(Pager/Slack/Email) 자동화 부재 | 상 |
| 2) 서버/DB/배치 로그 | 일부 있음 | [app.py](/Users/tnifl/Desktop/SafeToSpend/app.py) 로깅 필터 설치; [core/log_sanitize.py](/Users/tnifl/Desktop/SafeToSpend/core/log_sanitize.py); [scripts/receipt_worker.py](/Users/tnifl/Desktop/SafeToSpend/scripts/receipt_worker.py) heartbeat 로그 | 중앙 수집/보존 정책/로그 레벨 기준 문서 미비 | 상 |
| 3) 백업/복구 | 일부 있음 | billing 복구 CLI: [app.py](/Users/tnifl/Desktop/SafeToSpend/app.py), [docs/BILLING_RECOVERY_VALIDATION.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_RECOVERY_VALIDATION.md) | DB/업로드 파일 정기 백업 체계와 복구 검증 절차 미구축 | 상 |
| 4) 관리자 도구 | 일부 있음 | [routes/web/admin.py](/Users/tnifl/Desktop/SafeToSpend/routes/web/admin.py), [core/admin_guard.py](/Users/tnifl/Desktop/SafeToSpend/core/admin_guard.py), 운영 CLI 다수([app.py](/Users/tnifl/Desktop/SafeToSpend/app.py)) | 관리자 조회/복구 기능이 CLI 중심, UI 기반 운영 콘솔 제한적 | 중 |
| 5) 고객 문의 채널 | 있음 | [routes/web/support.py](/Users/tnifl/Desktop/SafeToSpend/routes/web/support.py), [templates/support/form.html](/Users/tnifl/Desktop/SafeToSpend/templates/support/form.html), [templates/admin/inquiries_list.html](/Users/tnifl/Desktop/SafeToSpend/templates/admin/inquiries_list.html) | SLA/응답시간/긴급 장애 공지 채널 명시 부족 | 상 |
| 6) 약관/개인정보/해지/자동결제 안내 | 일부 있음 | [templates/pricing.html](/Users/tnifl/Desktop/SafeToSpend/templates/pricing.html) 플랜/환불없음 문구 일부; [templates/base.html](/Users/tnifl/Desktop/SafeToSpend/templates/base.html) 면책 문구 | 정식 이용약관/개인정보처리방침/자동결제 고지 문서·공개 URL 부재 | 상 |
| 7) 운영 지표 | 일부 있음 | [routes/web/admin.py](/Users/tnifl/Desktop/SafeToSpend/routes/web/admin.py) `/admin/ops`; [services/admin_ops.py](/Users/tnifl/Desktop/SafeToSpend/services/admin_ops.py) DAU/실패율 집계 | 알림 임계치/자동 통지 연동 없음 | 중 |
| 8) 데이터 정합성 점검 | 일부 있음 | [scripts/billing_data_audit.py](/Users/tnifl/Desktop/SafeToSpend/scripts/billing_data_audit.py), [scripts/billing_data_recovery.py](/Users/tnifl/Desktop/SafeToSpend/scripts/billing_data_recovery.py), [docs/BILLING_DATA_AUDIT.md](/Users/tnifl/Desktop/SafeToSpend/docs/BILLING_DATA_AUDIT.md) | billing 외 도메인(증빙 파일 실재성, 패키지 산출물, 계좌동기화) 자동 점검 부족 | 상 |
| 9) 공지/상태 안내 | 일부 있음 | flash/알림 UI([templates/base.html](/Users/tnifl/Desktop/SafeToSpend/templates/base.html)); 결제 실패 안내([templates/billing/fail.html](/Users/tnifl/Desktop/SafeToSpend/templates/billing/fail.html)) | 장애 공지용 고정 상태 페이지/템플릿/운영 절차 미비 | 중 |
| 10) 보안/관리자 접근 통제 | 일부 있음 | [core/admin_guard.py](/Users/tnifl/Desktop/SafeToSpend/core/admin_guard.py); [app.py](/Users/tnifl/Desktop/SafeToSpend/app.py) CSP/HSTS/보안헤더; [core/security.py](/Users/tnifl/Desktop/SafeToSpend/core/security.py) CSRF | 관리자 2차 인증/접속 감사 강화 항목 미구현 | 상 |
| 11) 업로드 파일 보호 | 일부 있음 | [services/evidence_vault.py](/Users/tnifl/Desktop/SafeToSpend/services/evidence_vault.py) 시그니처 검사/용량 제한/민감 파일명 차단; [app.py](/Users/tnifl/Desktop/SafeToSpend/app.py) `MAX_CONTENT_LENGTH` | 악성코드 스캔/보관소 암호화·버전관리·백업 절차 문서화 부족 | 상 |
| 12) runbook/운영 문서 | 일부 있음 | billing 문서군([docs/BILLING_*.md](/Users/tnifl/Desktop/SafeToSpend/docs)); [docs/DEV_TESTING.md](/Users/tnifl/Desktop/SafeToSpend/docs/DEV_TESTING.md) | 서비스 전체 incident/backup/support/policy 통합 런북 부재 | 상 |

## 최종 분류

### 오픈 전 필수
- DB/업로드 백업 정책 및 복구 리허설 1회 이상
- 정책 문서 공개 경로 확정(이용약관/개인정보/자동결제/해지·환불)
- 장애 알림 최소 체계(500, DB, 결제실패 급증, 배치 실패)
- 데이터 정합성 일일 점검 항목 확정
- 스테이징 결제/인프라 실측 근거 축적(이미 billing 문서에서 `NO-GO` 근거 존재)

### 오픈 직후 1~2주 내 필요
- 운영 지표 알림 자동화
- 관리자 운영 UI 고도화(복구 도구 UI 접근성)
- 공지 템플릿/상태 공지 페이지 운영 프로세스 정착

### 나중에 고도화 가능
- 관리자 2FA/세분화된 권한 관리
- 업로드 악성코드 스캔 파이프라인
- SLA 대시보드/고급 관측성(분산트레이싱)

## 현재 결론
- 결제 도메인 복구 도구와 핵심 권한/보안 가드는 `일부 구축` 상태다.
- 운영 필수 관점에서 가장 큰 공백은 `백업/복구 체계`, `정책 문서 공개`, `인프라 실측 알림 체계`다.

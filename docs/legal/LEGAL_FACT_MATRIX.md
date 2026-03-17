# LEGAL_FACT_MATRIX

- 작성일: 2026-03-14
- 기준: 현재 SafeToSpend 코드베이스(라우트/서비스/모델/템플릿) 정적 점검
- 목적: 정책 문서(개인정보처리방침/이용약관/면책고지)에 반영 가능한 사실과 미확인 사항 분리

| 항목명 | 코드 근거 파일/경로 | 확인된 사실 | 반영 문서 | 비고 |
|---|---|---|---|---|
| 1) 회원가입/로그인 방식 | `routes/web/auth.py`, `services/auth.py`, `routes/api/auth.py` | 웹은 이메일+비밀번호 기반 가입/로그인. API는 access/refresh 토큰 발급/갱신 제공. | 개인정보처리방침, 이용약관 | 소셜 로그인(구글/카카오 등) 구현 근거 미확인 |
| 2) 계정 정보 | `domain/models.py` (`users`) | 계정 기본값은 이메일, 비밀번호 해시, 플랜 관련 상태/생성시각. | 개인정보처리방침, 이용약관 | 이름/전화번호/주민등록번호 수집 근거 미확인 |
| 3) 비밀번호 처리 방식 | `domain/models.py` (`set_password`, `check_password`) | 비밀번호는 `werkzeug.security` 해시 방식으로 저장/검증. 평문 비밀번호 DB 저장 근거 없음. | 개인정보처리방침 | 해시 알고리즘 세부 파라미터 정책 문서화는 별도 필요 |
| 4) 세션/쿠키/CSRF | `app.py`, `core/security.py`, `routes/web/support.py` | 세션 쿠키(`HttpOnly`, `SameSite`, 환경별 `Secure`) 사용. 웹 상태변경 요청 CSRF 검증 적용. | 개인정보처리방침, 이용약관 | 쿠키 배너/선택 관리 기능 구현 근거 미확인 |
| 5) 로그/보안 이벤트 | `services/security_audit.py`, `services/rate_limit.py`, `services/api_tokens.py` | 보안감사 로그에 이벤트/경로/메서드/사용자ID/추가정보 기록 가능. 로그인/토큰 처리 시 IP, User-Agent 사용. | 개인정보처리방침 | 로그 보관기간 명시 정책은 코드상 미확인 |
| 6) 거래/계좌 데이터 | `domain/models.py` (`transactions`, `bank_account_links`, `user_bank_accounts`), `routes/web/bank.py`, `services/import_popbill.py` | 거래내역, 계좌연동 정보 처리. `bank_account_links`는 은행코드+계좌번호 저장, `user_bank_accounts`는 fingerprint/last4 중심 저장. | 개인정보처리방침, 이용약관 | 계좌번호 저장 최소화 정책(어떤 테이블에 어떤 값 저장)은 문서에 분리 기재 필요 |
| 7) 영수증/증빙 업로드 | `domain/models.py` (`evidence_items`, `receipt_items`), `services/evidence_vault.py` | 파일 메타(파일키/원본명/MIME/크기/SHA-256) 저장. 서버 로컬 `uploads/evidence` 저장. 증빙 보관 기본값 7년(`EVIDENCE_RETENTION_DAYS` 기본 365*7). | 개인정보처리방침, 이용약관, 면책고지 | 증빙 자동정리 CLI(`purge-evidence`) 존재 |
| 8) OCR/AI 처리 | `services/receipt_parser.py`, `services/llm_safe.py` | 영수증 텍스트/이미지/PDF 파싱 시 OpenAI Responses API 호출 가능(`https://api.openai.com/v1/responses`). | 개인정보처리방침, 이용약관, 면책고지 | OpenAI 데이터 처리 조건(보관/국외이전 법적 문구)은 별도 법률 검토 필요 |
| 9) 외부 연동(계좌) | `services/popbill_easyfinbank.py`, `services/import_popbill.py`, `routes/web/bank.py` | 팝빌 EasyFinBank 연동으로 계좌/거래 조회 기능 제공. | 개인정보처리방침, 이용약관 | 위탁/제공 법적 지위(수탁/제공 구분) 최종 문구는 법률 검토 필요 |
| 10) 외부 연동(결제) | `routes/web/billing.py`, `services/billing/*`, `services/billing/toss_client.py`, `templates/billing/register_start.html` | 토스페이먼츠 정기결제(결제수단 등록/결제승인/웹훅) 연동. 가격 상수: 베이직 6,900원, 프로 12,900원, 추가 계좌 3,000원, 결제 실패 유예 3일. | 개인정보처리방침, 이용약관 | 결제/구독 상태머신은 구현됨. 해지 요청 UI 경로는 코드에서 명확히 확인되지 않음 |
| 11) 업그레이드/추가계좌 과금 정책 | `services/billing/pricing.py`, `services/billing/service.py`, `templates/pricing.html`, `templates/bank/index.html` | 업그레이드 전체 결제/추가계좌 일할 계산 로직 존재. 템플릿에 “기존 결제분 환불 없음” 안내 문구 존재. | 이용약관 | “환불 없음”은 일부 화면/운영초안 문구 기준. 환불 예외 규정은 별도 법률 검토 필요 |
| 12) 문의/지원 데이터 | `domain/models.py` (`inquiries`), `routes/web/support.py`, `templates/support/*` | 로그인 사용자 문의(제목/내용/상태/관리자답변/조회시각) 처리 및 조회 제공. | 개인정보처리방침, 이용약관 | 공개 이메일/전화 대신 인앱 문의 경로(`/support`) 확인됨 |
| 13) 탈퇴/삭제 처리 | `routes/web/profile.py` (`/dashboard/account/delete`), `services/auth.py` | 비밀번호+확인문구 검증 후 계정 및 다수 연관 데이터 삭제, 물리 파일 삭제 시도(best-effort) 수행. | 개인정보처리방침, 이용약관 | 일부 물리파일 즉시 삭제 실패 시 후속 정리 안내 존재 |
| 14) 세금/건보 추정 기능 성격 | `services/risk.py`, `services/tax_official_core.py`, `services/nhis_runtime.py`, `templates/calendar/tax_buffer.html`, `templates/nhis.html` | 세금/건보료는 입력값·공식기준 스냅샷 기반 “추정” 결과를 제공하며 상태(exact/high/limited/blocked)를 함께 제공. | 이용약관, 면책고지 | 확정 신고세액/실제 고지액과 차이 가능 |
| 15) 제3자 제공/처리위탁 고지용 사실 | 코드 전반 점검 (`routes/`, `services/`, `templates/`) | 외부 API 호출 근거는 토스/팝빌/OpenAI 확인. 그 외 제3자 제공/위탁 관계를 확정할 계약 정보는 코드상 미확인. | 개인정보처리방침, LEGAL_REVIEW_NOTES | 미확인 항목은 확정 문구 금지 |
| 16) 공개 페이지 필수 운영자 정보 | 코드 전반 점검 | 운영사명/사업자등록번호/주소/대표자/공식 대표 연락처/개인정보보호책임자 정보는 코드상 확정 불가. | 개인정보처리방침, 이용약관, LEGAL_REVIEW_NOTES | 허위 기재 금지. 현재 확인 가능한 문의 경로는 `/support` |

## 티켓1 결론

- 정책 문서에 바로 반영 가능한 사실(확인됨)과 법률/운영 확인이 필요한 사실(미확인)을 분리했다.
- 다음 티켓 문서(개인정보처리방침/이용약관/면책고지)는 위 표의 “확인된 사실”만 본문 확정 문구로 사용한다.

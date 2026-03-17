# SafeToSpend 서비스 개요 보고서

작성일: 2026-03-08  
대상 독자: 신규 팀원, 운영자, AI 코딩 에이전트

## 1) 한 줄 요약
SafeToSpend는 프리랜서/1인 사업자의 월 거래를 빠르게 정리하고, 세금 보관/증빙/건보료(추정)까지 한 흐름으로 처리하는 Flask 기반 웹 서비스입니다.

## 2) 이 서비스가 "정확히" 무엇인가
SafeToSpend는 단순 가계부가 아니라, 다음 3가지를 하나로 묶은 월간 운영 도구입니다.
- 거래 정리 도구: 수입/지출/증빙 상태를 월 단위로 빠르게 확정
- 세무 준비 도구: 세무사 전달 전에 누락/품질 문제를 줄이고 패키지 ZIP 생성
- 현금흐름 안전 도구: 세금/건보료(추정)를 미리 계산해 "지금 써도 되는 돈"을 보수적으로 안내

즉, 핵심 목적은 "기록"보다 "마감/전달/리스크 방지"입니다.

## 3) 누구를 위한 서비스인가
- 프리랜서/1인 사업자
- 월말에 증빙 정리가 밀리는 사용자
- 세무사에게 자료를 전달할 때 누락/반려를 줄이고 싶은 사용자
- 건보료/세금 때문에 실제 사용 가능 현금이 불안정한 사용자

## 4) 서비스가 해결하는 문제
- 거래가 흩어져 월말에 한꺼번에 정리해야 하는 부담
- 수입/지출/증빙 분류 누락으로 세무 전달 품질이 떨어지는 문제
- 세금/건보료를 늦게 인지해 현금흐름이 흔들리는 문제
- “어디서 입력해야 하는지” 혼동(특히 NHIS/자산 입력)

## 5) 핵심 사용자 흐름 (실사용 기준)
1. 회원가입/로그인 + 온보딩
2. 거래 가져오기(CSV/계좌 동기화)
3. 리뷰 화면에서 수입/지출/증빙 라벨 정리
4. Overview에서 이번 달 우선순위(할 일/보관 권장액) 확인
5. Tax Buffer에서 보관금 조정
6. Package에서 세무 전달 ZIP 생성
7. NHIS 화면에서 건보료(추정) 확인 + 입력 보완

## 6) 사용자가 체감하는 핵심 결과물
- 이번 달 남은 돈(세금/건보료 보관 반영)
- 세금 보관 권장액 + 부족 여부
- 증빙 누락 목록(필수/검토)
- 세무사 전달용 패키지 ZIP
- 월 건보료(현재/11월) 추정과 영향 분해

## 7) 주요 화면/URL 맵
| 영역 | URL | 템플릿 | 라우트/핵심 파일 |
|---|---|---|---|
| 랜딩 | `/` | `templates/landing.html` | `routes/web/main.py` |
| 로그인/가입 | `/login`, `/register`, `/onboarding` | `templates/login.html`, `register.html`, `onboarding.html` | `routes/web/auth.py` |
| 인박스(가져오기) | `/inbox`, `/inbox/import` | `templates/inbox*.html` | `routes/web/inbox.py` |
| 월간 요약 | `/overview` | `templates/overview.html` | `routes/web/overview.py` |
| 캘린더(월/일/연) | `/dashboard/calendar`, `/dashboard/day/<ymd>`, `/dashboard/year` | `templates/calendar/month.html`, `day.html`, `year.html` | `routes/web/web_calendar.py` |
| 리뷰/증빙 | `/dashboard/review` 계열 | `templates/calendar/review*.html` | `routes/web/calendar/review.py`, `receipt.py` |
| 세금 보관 | `/dashboard/tax-buffer` | `templates/calendar/tax_buffer.html` | `routes/web/calendar/tax.py` |
| 증빙 보관함 | `/dashboard/vault` | `templates/vault/index.html`, `templates/calendar/vault.html` | `routes/web/vault.py`, `routes/web/calendar/vault.py` |
| 세무 패키지 | `/dashboard/package` | `templates/package/index.html` | `routes/web/package.py` |
| 건보료(통합) | `/dashboard/nhis` | `templates/nhis.html` | `routes/web/profile.py` |
| 자산 입력(보조) | `/dashboard/assets`, `/dashboard/assets/quiz` | `templates/assets.html`, `assets_quiz.html` | `routes/web/profile.py` |
| 계좌 연동 | `/bank` | `templates/bank/index.html` | `routes/web/bank.py` |
| 문의 | `/support` | `templates/support/*.html` | `routes/web/support.py` |
| 관리자 운영 | `/admin/ops` | `templates/admin/ops.html` | `routes/web/admin.py`, `services/admin_ops.py` |

## 8) 실제 사용 시나리오 (예시)
1. 사용자가 월초에 은행 CSV를 업로드
2. 서비스가 인박스/리뷰에서 자동 분류 후보를 보여줌
3. 사용자는 "필수 영수증 누락"부터 우선 처리
4. overview에서 세금/건보 보관 권장액을 확인
5. tax-buffer에서 실제 보관 금액을 맞춤 조정
6. package에서 품질 점검 후 세무사 전달 ZIP 다운로드
7. nhis에서 건보료 변동 가능성(특히 11월 반영)을 점검하고 입력 보완

## 9) 백엔드 구조 요약
- 앱 프레임워크: Flask (`app.py`)
- 라우팅: `routes/web/*`, `routes/api/auth.py`
- 비즈니스 로직: `services/*`
- 데이터 모델: `domain/models.py` (SQLAlchemy)
- 템플릿: `templates/*` (Jinja2)
- 보안/공통: `core/*` (인증, CSRF, 확장, 시간 유틸)

폴더 역할 한 줄 정리:
- `routes/`: HTTP 입력/응답, 검증, 화면 연결
- `services/`: 계산/동기화/정책/운영 로직
- `domain/`: DB 스키마(모델)
- `templates/`: 사용자 화면
- `scripts/`: 운영/검증/스모크 도구
- `docs/`: 정책/테스트/운영 문서

## 10) 핵심 데이터 모델 (자주 보는 것만)
- `User`: 계정, 플랜
- `Transaction`: 원장 거래(입금/출금)
- `IncomeLabel`, `ExpenseLabel`: 거래 분류 결과
- `EvidenceItem`: 증빙 첨부/상태
- `TaxProfile`: 세금 관련 사용자 프로필(JSON)
- `NhisUserProfile`, `NhisBillHistory`: 건보 입력/고지 이력
- `AssetProfile`, `AssetItem`: 자산 진단 입력/항목
- `NhisRateSnapshot`, `AssetDatasetSnapshot`: 외부 기준/데이터 스냅샷

## 11) 계산 엔진 요약
### 7-1. 세금/요약
- `services/risk.py`
  - `compute_overview(...)`: 월 화면 요약 수치
  - `compute_tax_estimate(...)`: 세금 추정치

### 7-2. 건보료(NHIS)
- `services/nhis_estimator.py`: 건보료 계산 핵심
- `services/assets_estimator.py`: NHIS + 자산/소득 입력을 묶어 피드백 생성
- `services/nhis_profile.py`: 사용자 입력 저장
- `services/nhis_unified.py`: NHIS/자산 출처 병합 로딩

### 7-3. 공식 기준 관리
- 레퍼런스 스냅샷: `services/reference/nhis_reference.py`, `tax_reference.py`
- 유효성 게이트: `services/official_refs/guard.py`
- 레이트 스냅샷 갱신: `services/nhis_rates.py`
- 운영 상태 집계: `services/admin_ops.py`

## 12) 서비스 경계 (무엇을 하고, 무엇을 하지 않는가)
하는 것:
- 월간 거래 정리/증빙 상태 관리/세무 전달 준비
- 세금/건보료 추정치를 기반으로 보수적 현금흐름 가이드 제공

하지 않는 것:
- 법적 확정 세액/보험료를 보장하는 공식 신고 대행
- 투자/절세 "행동 유도" 자동화
- 사용자 승인 없는 자동 코드 수정/운영 반영

## 13) 보안/안정성 포인트
- CSRF 검증: 웹 폼 POST 계열
- API 인증: `/api/*` Bearer 토큰 미들웨어
- 입력 정규화: `services/input_sanitize.py`
- Next URL 안전 처리: 내부 경로 허용 방식
- 실패 시 정책: 가능하면 사용자 친화 메시지 + 500 방지
- 관리자/운영 경로 분리: `/admin/*` 권한 체크

## 14) 운영 관점에서 중요한 사실
- 사용자 요청 경로와 스냅샷/공식 기준 검증 상태가 연결되어 있음
- `/dashboard/nhis`는 현재 “건보료 기준 화면” 역할
- `/dashboard/assets*`는 일부 기능이 남아 있으나 NHIS와 역할 중복 이력이 있음
- 운영 모니터링은 `/admin/ops`에서 확인 가능

## 15) AI/신규 개발자 빠른 온보딩 가이드
### “어디부터 읽으면 되는가”
1. `app.py` (앱 초기화/보안/블루프린트 등록)
2. `routes/__init__.py` (전체 URL 지형도)
3. `routes/web/profile.py` (NHIS/Assets 핵심)
4. `services/risk.py`, `services/nhis_estimator.py`, `services/assets_estimator.py`
5. `domain/models.py` (저장 구조)
6. `templates/nhis.html`, `templates/calendar/month.html`, `templates/overview.html`

### “기능별 진입 파일”
- 가져오기: `routes/web/inbox.py`, `services/import_csv.py`
- 리뷰/증빙: `routes/web/calendar/review.py`, `routes/web/calendar/receipt.py`
- 패키지: `routes/web/package.py`, `services/tax_package.py`
- NHIS: `routes/web/profile.py` + `templates/nhis.html`
- 운영: `services/admin_ops.py`, `templates/admin/ops.html`

## 16) 로컬 실행/확인(기본)
```bash
source .venv/bin/activate
flask --app app run
```

주요 확인 URL:
- `http://127.0.0.1:5000/overview`
- `http://127.0.0.1:5000/dashboard/calendar`
- `http://127.0.0.1:5000/dashboard/nhis`
- `http://127.0.0.1:5000/admin/ops` (관리자 권한 필요)

## 17) 현재 구조의 강점/주의점
### 강점
- 기능이 월간 운영 흐름(가져오기→리뷰→보관→패키지)에 맞게 연결됨
- NHIS/세금/증빙이 단일 서비스 내에서 이어짐
- 관리자 운영 지표와 검증 문서가 축적돼 있음

### 주의점
- NHIS/Assets 간 역할 중복 이력이 있어 UI 문구와 저장 출처를 일관되게 유지해야 함
- 공식 기준 스냅샷/검증 상태에 따라 일부 화면 표시 정책이 달라질 수 있음
- 템플릿 단일 파일(`templates/nhis.html`)이 커서 변경 시 회귀 점검이 중요함

---
이 문서는 “서비스를 빠르게 이해하기 위한 진입 보고서”입니다.  
정책/수치 근거는 `docs/OFFICIAL_REFERENCE_REGISTRY.md`, `docs/REFERENCE_DATA.md`, `docs/VERIFICATION_REPORT.md`를 함께 확인하세요.

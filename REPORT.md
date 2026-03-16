# 공식 정보 수집/변화 감지/자동수정 기능 조사 보고서

작성일: 2026-03-07  
대상 레포: `SafeToSpend`

## 1) Executive Summary
- 결론: **공식 정보 수집 로직은 일부 존재**합니다. 다만 항목별로 방식이 다릅니다.
- 건보료 기준(NHIS)은 `services/nhis_rates.py`에서 외부 사이트를 `requests`로 조회하고, DB 스냅샷(`nhis_rate_snapshots`)에 저장합니다.
- 자산 보조 데이터(차량/부동산)는 `services/assets_data.py`에서 외부 사이트를 조회해 DB 스냅샷(`asset_dataset_snapshots`)을 갱신합니다.
- 세율(종합소득세/지방소득세)은 외부 실시간 수집이 아니라 `services/reference/tax_reference.py` **정적 스냅샷 기반**입니다.
- 사이트 변화 감지는 **부분적으로 존재**합니다(형식 변화 플래그/경고 표시). 하지만 해시 기반 감시, 주기 모니터, 이메일/웹훅 알림은 없습니다.
- 사이트 변경 시 크롤러 코드를 자동 작성/수정하는 기능은 **존재하지 않습니다**.
- 런타임 자기수정은 보안/신뢰성상 금지되어야 하며, 현실적 대안은 **변화 감지 + 변경점 요약 + 패치 초안 제시(자동 적용 X)** 입니다.
- 우선순위: (1) watchdog 스크립트 도입 (2) 관리자 화면 경고 연계 (3) 파서 설정 분리 + 패치 초안 도구.

## 2) 현재 ‘공식 정보 수집’ 존재 여부 결론(있음/없음) + 핵심 근거 파일/함수

### 2-1. 존재함 (외부 수집)
1. NHIS 기준값 수집
- 파일/함수:
  - `services/nhis_rates.py:204` `fetch_sources(...)`
  - `services/nhis_rates.py:494` `ensure_active_snapshot(...)`
  - `services/nhis_rates.py:472` `refresh_nhis_rates(...)`
- 방식: `requests.get(...)` + 정규식 파싱 + 안전 범위 clamp + DB upsert
- 호출 트리거:
  - `/dashboard/nhis` GET 경로에서 호출 (`routes/web/profile.py:1301`)
  - 자산 피드백 계산 중 호출 (`services/assets_estimator.py:703`)
  - 기타 런타임 경로 (`services/nhis_runtime.py:50`, `services/health_insurance.py:115`)
  - 수동 CLI (`app.py:96` `flask --app app refresh-nhis-rates`)

2. 자산 보조 데이터셋 수집
- 파일/함수:
  - `services/assets_data.py:205` `fetch_asset_datasets(...)`
  - `services/assets_data.py:312` `ensure_asset_datasets(...)`
- 방식: `requests.get(...)` + 키워드 점수 기반 형식 변화 감지 + DB upsert
- 호출 트리거:
  - `/dashboard/nhis`의 통합 피드백 계산 경로 (`services/assets_estimator.py:602`)
  - `/admin/assets-data` 조회 시 상태 계산 (`routes/web/profile.py:1168`)

### 2-2. 존재하지 않음 (외부 수집 없음)
1. 세율(종합소득세/지방소득세) 실시간 수집
- 파일/함수:
  - `services/reference/tax_reference.py` (정적 스냅샷)
  - `services/risk.py:318` `compute_tax_estimate(...)`에서 스냅샷 참조
- 결론: 런타임 외부 수집 없음, 코드 내 기준 테이블 기반

2. 레퍼런스 전체 주기 스케줄러(레포 내부)
- APScheduler/Celery/cron 내장 로직: 검색 기준 미발견
- 결론: 요청 시(stale 시도) + 수동 CLI 중심

## 3) 항목별 현황 표(요율/세율/하한/상한 등)

| 항목 | 현재 데이터 소스 | 수집 방식 | 트리거(요청 시/스케줄) | 캐시/폴백 | 리스크 |
|---|---|---|---|---|---|
| NHIS 보험료율/장기요양/점수당금액/재산공제/소득반영 규칙 | 외부 URL(MOHW/EasyLaw/Law.go) + 내부 기본값 | `requests` + 정규식/키워드 파싱 (`services/nhis_rates.py`) | **요청 시** stale/fallback 조건에서 시도 + 수동 CLI | `nhis_rate_snapshots` 활성 스냅샷, 실패 시 마지막 스냅샷/기본값 유지 | 첫 요청 지연 가능성, 페이지 구조 변경 시 파싱 부정확 위험 |
| NHIS 재산점수표(구간 테이블) | 로컬 JSON(`data/nhis_property_points_2026.json`) | 파일 로드 (`services/nhis_rules.py`) | 런타임 로드 | 파일 없으면 선형 fallback 테이블 | 공식표 변경 누락 시 추정 오차 누적 가능 |
| 자산 보조 데이터(차량/부동산) | 외부 URL(Law.go, RealtyPrice) + 내부 기본 payload | `requests` + 키워드 점수/오류페이지 감지 (`services/assets_data.py`) | **요청 시** stale/fallback 조건에서 시도 | `asset_dataset_snapshots` 활성 스냅샷 + fallback | 키워드 기반 감지는 오탐/미탐 가능, 구조 변경 대응 한계 |
| 종합소득세 누진세율/누진공제 | 내부 스냅샷 (`services/reference/tax_reference.py`) | 수동 업데이트(코드/문서) | 요청 시 계산만 수행 (외부 수집 없음) | 스냅샷 고정 + 테스트 검증 | 최신 개정 반영이 운영 절차에 의존 |
| 지방소득세 비율(10%) | 내부 스냅샷 (`services/reference/tax_reference.py`) | 수동 업데이트 | 요청 시 계산만 수행 | 스냅샷 고정 | 법/지침 변경 시 사람이 놓치면 반영 지연 |

## 4) 변화 감지/알림 기능 존재 여부 + 설계안

### 4-1. 현재 존재 여부
- **부분적으로 존재함**
- NHIS:
  - `services/nhis_rates.py:407~409`에서 `format_warnings`, `format_drift_detected` 생성
  - `/dashboard/nhis`에서 경고 문구로 노출 (`routes/web/profile.py:1451`)
- 자산 데이터셋:
  - `services/assets_data.py:164~203` 형식 변화 감지
  - `payload_json.format_drift_detected` 저장 및 `/admin/assets-data` 표시 (`templates/admin/assets_data.html:33`)
- 관리자 운영 대시보드:
  - `/admin/ops` 신선도 경고(`warn`) 및 실패 연속 카운트 반영 (`services/admin_ops.py:258~355`, `templates/admin/ops.html`)

### 4-2. 현재 없는 부분
- 해시/시그니처 기반 “정밀 변화 감시” 없음
- 주기 실행 watchdog(일 1회/주 1회) 스크립트 없음
- 이메일/슬랙/웹훅 같은 push 알림 없음 (관리자 페이지 pull 확인 방식)

### 4-3. 최소 설계안(필수 2안)

A) 안전형(권장)
- 주기: 1일 1회(운영 크론)
- 방식:
  - 대상 URL 목록별 핵심 구간 해시 저장
  - 직전 해시와 다르면 `changed=true` 기록
  - 관리자 `/admin/ops`에 “Reference Watch 경고 배지” 표시
- 실패 처리:
  - 네트워크 실패 시 `failing=true`만 기록
  - 앱 런타임 계산은 기존 스냅샷/캐시로 계속 동작

B) 적극형(선택)
- 지표 중심 감시:
  - 파싱 성공률
  - 필수 필드 누락률
  - 최근 N회 실패 연속 횟수
- 임계치 초과 시 관리자 경고 레벨 상향(`warn_reason=failure` 확장)

## 5) 자동 수정(자기수정) 기능 존재 여부 + 금지 근거 + 대안

### 5-1. 존재 여부 조사 결론
- **존재하지 않음**
- 근거:
  - `autofix`, `self-heal`, `auto patch`, `repair parser`, `selector discovery` 관련 구현 미발견
  - 크롤링 관련 로직은 `services/nhis_rates.py`, `services/assets_data.py`에 한정
  - LLM 사용(`services/llm_safe.py`)은 영수증 JSON 추출용이며 코드 파일 수정/패치 적용 기능 없음

### 5-2. 자동 수정 금지 근거
- 런타임 자기수정은 아래 리스크가 큼:
  - 악성 입력/오탐으로 잘못된 코드 적용
  - 테스트 미통과 상태 배포
  - 책임 추적 어려움(누가/왜 수정했는지)
  - 서비스 신뢰도 하락(예측 불가 동작)

### 5-3. 현실적 대안(필수 2안)

A) 설정 기반 파서(권장)
- 파서 코드는 고정, 사이트별 selector/정규식은 설정(JSON/YAML)으로 분리
- 변화 감지 시 “어느 selector가 실패했는지”를 리포트로 출력
- 운영자는 설정만 수정해 복구(코드 변경 최소화)

B) 패치 초안 생성(자동 적용 X)
- 변화 감지 로그 + 최신 HTML(민감정보 제거) 기반으로 selector 후보를 계산해 “패치 초안” 출력
- 자동 커밋/자동 반영 금지
- 사람 검토 + 테스트 통과 후 PR 머지

## 6) 추천 실행안(우선순위 1~3)

1. `scripts/reference_watchdog.py` 추가 (운영 도구)
- URL/관심구간/해시 비교/JSON 로그 출력
- 앱 런타임과 분리 실행(크론/수동)

2. `/admin/ops`에 Reference Watch 상태 카드 추가
- `last_checked_at`, `changed`, `failing`, `fail_streak` 표시
- 경고 배지로 운영자 즉시 인지

3. 파서 설정 분리 + 패치 초안 생성기 도입
- 1차: 설정 분리(코드 고정)
- 2차: 후보 selector 제안 스크립트(자동 적용 금지)

## 7) 부록: 조사한 파일/검색 키워드 목록

### 7-1. 주요 조사 파일
- `services/nhis_rates.py`
- `services/assets_data.py`
- `services/admin_ops.py`
- `services/assets_estimator.py`
- `services/nhis_runtime.py`
- `services/health_insurance.py`
- `services/reference/nhis_reference.py`
- `services/reference/tax_reference.py`
- `services/llm_safe.py`
- `routes/web/profile.py`
- `routes/web/admin.py`
- `templates/admin/ops.html`
- `templates/admin/assets_data.html`
- `templates/admin/nhis_rates.html`
- `app.py`
- `scripts/verify_reference_math.py`

### 7-2. 검색 키워드
- 네트워크/크롤링: `requests`, `httpx`, `urllib`, `aiohttp`, `BeautifulSoup`, `bs4`, `lxml`, `playwright`, `selenium`, `feedparser`
- 공식 도메인: `law.go.kr`, `nhis.or.kr`, `mohw.go.kr`, `nts.go.kr`, `easylaw.go.kr`
- 레퍼런스/요율: `reference`, `snapshot`, `rate`, `premium`, `ltc`, `211.5`, `7.19`, `20160`, `4591740`, `세율`, `누진공제`
- 감시/알림: `format_drift_detected`, `format_warnings`, `monitor`, `alert`, `warn_reason`, `freshness`
- 자동수정: `autofix`, `self-heal`, `auto patch`, `repair parser`, `selector discovery`, `LLM code`

---

## 남아있는 불확실성 / 추가 확인 필요 항목
- 레포 외부(인프라 크론/배치)에서 `flask refresh-nhis-rates`를 주기 실행 중인지 여부는 코드만으로 확정 불가
- 외부 페이지의 구조 변경 빈도/오탐률(키워드 기반 감지 정확도)은 운영 로그 누적 분석이 필요
- `docs/REFERENCE_UPDATE_RUNBOOK.md`의 “런타임 외부 조회 금지”와 실제 코드(요청 시 stale refresh)의 정책 일치 여부는 의사결정이 필요

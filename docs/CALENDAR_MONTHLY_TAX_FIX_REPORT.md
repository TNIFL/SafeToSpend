# CALENDAR_MONTHLY_TAX_FIX_REPORT

작성일: 2026-03-14  
범위: 이슈1(캘린더 월별 세금 고정)만 수정

## A. 문제 요약
- 사용자 제보: `/dashboard/calendar`에서 월을 바꿔도 `세금 추정치(이번 달)`이 같은 값(예: 27,225원)으로 보이는 케이스가 존재.
- 기존 실데이터 재검증: 7개 케이스 중 3개 재현(42.86%).

## B. 재현 조건 요약
- 고정 재현 핵심 조건:
  - `compute_tax_estimate`가 `limited_proxy` 경로.
  - `taxable_income_input_source == income_hybrid_total_income_proxy`.
  - 월별 거래 편차가 있어도 연간 override 값이 동일.
- 재현 케이스(수정 전): CASE_A, CASE_B, CASE_E.

## C. 실제 원인 분기
- 캘린더는 `routes/web/web_calendar.py`에서 `compute_tax_estimate(...).buffer_target_krw`를 렌더.
- 기존 `services/risk.py` 경로에서 아래 분기가 월 신호를 약화:
  1. income override 적용 시 `income_included`를 `annual_total_income / 12`로 덮어씀.
  2. 과세표준 대체 입력도 `income_hybrid_total_income_proxy`(연간 고정값)를 우선 사용.
  3. 결과적으로 `tax_due_est_krw`(월 표시값)가 월별 거래 편차와 무관하게 고정될 수 있음.

## D. 수정 내용 요약
- 파일: `services/risk.py`
  - `compute_tax_estimate(..., prefer_monthly_signal: bool = False)` 옵션 추가.
  - `prefer_monthly_signal=True`일 때:
    - annual override로 `income_included`를 월 고정값(`annual/12`)으로 덮어쓰지 않음.
    - 과세표준 프록시 해석에서 annual override 우선을 끄고 월 이익 연환산 프록시를 사용하도록 조정.
  - `compute_risk_summary(..., prefer_monthly_signal: bool = False)` 옵션 추가.
  - `compute_overview`에서 `compute_risk_summary`/`compute_tax_estimate` 모두 `prefer_monthly_signal=True` 사용.
- 파일: `routes/web/web_calendar.py`
  - 캘린더 월 화면 호출에 `prefer_monthly_signal=True` 적용.
- 파일: `routes/web/calendar/review.py`
  - 정리하기(review) 경로의 세금 계산 호출을 월 반영 모드로 통일.
- 파일: `routes/web/calendar/tax.py`
  - 세금보관함(tax_buffer) 경로의 세금 계산 호출을 월 반영 모드로 통일.
- 의도:
  - 캘린더/요약/정리하기/세금보관함의 “이번 달” 세금 추정치 의미를 일치.
  - 기존 세금 엔진/신뢰도 체계(exact/high/limited/blocked)는 유지.

## E. 테스트 결과
- 실행:
  - `.venv/bin/python -m unittest tests.test_calendar_monthly_tax_bugfix tests.test_tax_estimate_service`
- 결과:
  - `Ran 20 tests ... OK`
- 추가/보강 테스트:
  - `tests/test_calendar_monthly_tax_bugfix.py`
    - 월별 거래가 다르면(캘린더 모드) 세금값이 달라지는지.
    - 월별 거래가 같으면 동일값 허용되는지.
    - 캘린더 라우트가 `prefer_monthly_signal=True`를 쓰는지.
  - `tests/test_tax_estimate_service.py`
    - 캘린더 모드에서 `income_hybrid_total_income_proxy` 대신 `monthly_profit_annualized_proxy` 경로 진입 검증.

## F. 실데이터 재검증 결과
- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/revalidate_real_data_issues.py --matrix reports/real_data_issue_revalidation_matrix.json --summary reports/real_data_issue_revalidation_summary_after_fix.json`
- 전/후 비교(이슈1):
  - 수정 전: 재현 3/7 (42.86%)
  - 수정 후: 재현 0/7 (0.00%)
- 확인 포인트:
  - CASE_A/B/E에서 `/dashboard/calendar` 렌더값 월별 변화 확인.
  - 추가 수동 대조(CASE_B, 2026-01~03):
    - 캘린더/요약/정리하기/세금보관함이 같은 월별 값을 사용하도록 정렬됨.
    - 예: 2026-02 기준 네 화면 모두 `1,051,580,909원`.

## G. 남은 리스크
- 캘린더 월 카드에서 월 이익 연환산 프록시를 쓰기 때문에 거래 편차가 큰 달은 값 변동폭이 커질 수 있음.
- `scripts/revalidate_real_data_issues.py` summary의 issue1 `final_verdict` 문자열은 ratio 0.0이어도 `"부분 재현됨"`으로 표기되는 집계 규칙 한계가 있음(수치/패턴 필드로 판독 필요).
- blocked 상태 월에서는 화면마다 숫자 대신 “입력 보완 후 계산 가능” 문구가 우선 노출될 수 있음(정책상 정상).

## H. 최종 판정
- 이슈1(캘린더 월별 세금 고정): **해소됨**(캘린더/요약/정리하기/세금보관함 기준).
- 근거:
  - 회귀 테스트 통과.
  - 실데이터 기반 재검증에서 재현 비율 42.86% → 0.00%.

# Seasonal UX Report

## A. 문제 요약

기존 기능은 이미 충분했지만, 사용자는 `지금 왜 이 화면을 써야 하는지`를 시즌 맥락에서 바로 이해하기 어려웠다. 특히 5월과 11월처럼 세금 민감도가 높아지는 시기에도 overview, review, tax_buffer, package가 개별 기능 화면처럼 보이는 문제가 있었다.

이번 Stage 1의 목표는 새 기능을 크게 만들지 않고, 기존 기능을 시즌 체크리스트처럼 묶어 `지금 해야 할 일` 중심으로 재배치하는 것이다.

## B. 시즌 상태 맵

- `may_filing_focus`
  - 4월~6월
  - 메인 맥락: 작년 수입과 비용 정리
- `november_prepayment_focus`
  - 10월~11월
  - 메인 맥락: 상반기 기준 미리 점검
- `off_season`
  - 나머지 기간
  - 메인 맥락: 다음 시즌 전에 월별 정리 습관 만들기

자세한 상태 요소와 우선 행동은 [SEASONAL_UX_STATE_MAP.md](/Users/tnifl/Desktop/SafeToSpend/docs/SEASONAL_UX_STATE_MAP.md)에 정리했다.

## C. overview 배너/체크리스트 반영 결과

- overview에 시즌 허브를 가장 강하게 배치했다.
- 강한 시즌(5월/11월)에는 `지금 할 일 3개`를 카드형 체크리스트로 노출한다.
- 비시즌에는 같은 구조를 유지하되 강도를 약하게 낮춘다.
- overview는 기존 `improvement_cards`보다 상위 목적 카드로 시즌 카드가 먼저 보이도록 바꿨다.

## D. review/tax_buffer/package 시즌 맥락 반영 결과

- review
  - 시즌과 연결된 `영수증 정리/보강` 카드 1개를 상단에 노출
- tax_buffer
  - 시즌과 연결된 `정확도/상반기 점검/버퍼 점검` 카드 1개를 상단에 노출
- package
  - 시즌과 연결된 `전달 자료 점검` 카드 1개를 상단에 노출

이 블록은 강한 배너가 아니라, 현재 하는 일이 시즌 목표와 어떻게 연결되는지 설명하는 작은 컨텍스트 카드다.

## E. 생활 언어 카피 반영 결과

- `작년 수입과 비용 정리`
- `상반기 기준 미리 점검`
- `이미 빠진 세금 확인`
- `일하면서 쓴 비용 반영`

카피 원칙은 [SEASONAL_UX_COPY_GUIDE.md](/Users/tnifl/Desktop/SafeToSpend/docs/SEASONAL_UX_COPY_GUIDE.md)에 고정했다.

## F. 테스트 결과

- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_state_logic tests.test_seasonal_ux_render tests.test_seasonal_ux_copy`
- 관련 기존 회귀:
  - `tests.test_natural_flow_entrypoints`
  - `tests.test_natural_flow_progressive_questions`
  - `tests.test_natural_flow_copy`
- 최종 실행:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_state_logic tests.test_seasonal_ux_render tests.test_seasonal_ux_copy tests.test_natural_flow_entrypoints tests.test_natural_flow_progressive_questions tests.test_natural_flow_copy tests.test_tax_single_step_flow tests.test_receipt_expense_guide_entrypoints`
  - `Ran 25 tests in 0.011s`
  - `OK`
- 문법 확인:
  - `PYTHONPATH=. .venv/bin/python -m py_compile services/seasonal_ux.py routes/web/overview.py routes/web/calendar/review.py routes/web/calendar/tax.py routes/web/package.py`
  - 통과

## G. 남은 리스크

- 11월 UX는 실제 운영 데이터 기준으로 세부 카피를 더 다듬을 여지가 있다.
- 4월/6월/10월/12월 같은 경계월 카피는 후속 실사용 피드백으로 더 보정할 수 있다.
- 클릭률/완료율 계측은 아직 붙이지 않았다.

## H. 다음 단계 연결 포인트

- 시즌 카드 클릭률/완료율 계측
- 자동 추론 강화
- WebKit/Firefox QA 보강
- 세무사 패키지에 reflected/pending/consult 상태 포함 범위 확장

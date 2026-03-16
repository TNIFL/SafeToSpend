# Receipt Tax Effects Report

## A. 문제 요약
- 영수증 비용처리 evaluator / follow-up / reinforcement 플로우는 구현돼 있었지만, 그 판정 결과가 실제 예상세금 계산에는 연결되지 않았다.
- 그 결과 사용자는 영수증 상태가 좋아져도 숫자 변화가 없거나, 반대로 화면만 바뀌고 서버 계산은 그대로인 상태를 구분하기 어려웠다.
- 이번 작업의 목표는 `high_likelihood`만 실제 세금 계산에 반영하고, 나머지 상태는 보류/제외/검토 필요 버킷으로 분리한 뒤, 관련 숫자와 토스트를 서버 계산 결과 기준으로 동기화하는 것이다.

## B. 세금 반영 source of truth
- 반영 단위:
  - `transaction` 단위
- 금액 source of truth:
  - OCR 금액이 아니라 `Transaction.amount_krw`
- 상태별 규칙:
  - `high_likelihood`: `reflected_expense_krw`
  - `needs_review`: `pending_review_expense_krw`
  - `do_not_auto_allow`: `excluded_expense_krw`
  - `consult_tax_review`: `consult_tax_review_expense_krw`
- 중복 방지:
  - 동일 `transaction_id`는 최신 평가 1회만 집계
  - 이미 `ExpenseLabel.status in ('business', 'personal')`로 수동 확정된 거래는 영수증 evaluator 집계에서 건너뛴다.

## C. 상태별 금액 집계 규칙
- 구현 파일:
  - [receipt_tax_effects.py](/Users/tnifl/Desktop/SafeToSpend/services/receipt_tax_effects.py)
- 핵심 함수:
  - `compute_receipt_tax_effects_for_month(...)`
  - `summarize_receipt_tax_effect_entries(...)`
- 반환 버킷:
  - `reflected_expense_krw`
  - `pending_review_expense_krw`
  - `excluded_expense_krw`
  - `consult_tax_review_expense_krw`
  - `reflected_transaction_count`
  - `pending_transaction_count`
- 집계 조건:
  - Evidence 파일, receipt draft/meta, follow-up 답변, reinforcement 데이터 중 하나라도 있어야 “영수증 맥락이 있는 거래”로 본다.

## D. 세금 계산 연결 방식
- 구현 파일:
  - [risk.py](/Users/tnifl/Desktop/SafeToSpend/services/risk.py)
- 연결 방식:
  - 기존 `expense_business_base_krw`에 `receipt_reflected_expense_krw`만 더해 `expense_business_krw`를 계산한다.
  - `needs_review`, `do_not_auto_allow`, `consult_tax_review`는 예상세금 계산에 넣지 않는다.
- 추가 반환 필드:
  - `expense_business_base_krw`
  - `receipt_reflected_expense_krw`
  - `receipt_pending_expense_krw`
  - `receipt_excluded_expense_krw`
  - `receipt_consult_tax_review_expense_krw`
  - `tax_due_before_receipt_effects_krw`
  - `buffer_target_before_receipt_effects_krw`
  - `tax_delta_from_receipts_krw`
  - `buffer_delta_from_receipts_krw`
- before/after 계산:
  - 동일 월 입력으로 “영수증 반영 0원” 스냅샷과 “영수증 반영 포함” 스냅샷을 둘 다 계산한다.
  - 숫자 애니메이션과 토스트는 이 before/after 서버값만 사용한다.

## E. 토스트/애니메이션 반영 범위
- 토스트:
  - [base.html](/Users/tnifl/Desktop/SafeToSpend/templates/base.html)
  - `receipt_effect_toast=1` + `receipt_effect_event=1`일 때만 공용 알림 브리지로 1회 표시
  - `toast_and_center`로 즉시 토스트 + 알림센터 적재
  - 이후 review -> calendar / tax_buffer 이동에는 `receipt_effect_toast`를 전파하지 않아 중복 토스트를 막는다.
- 숫자 애니메이션:
  - 공용 JS: [tax-number-animate.js](/Users/tnifl/Desktop/SafeToSpend/static/js/tax-number-animate.js)
  - 적용 화면:
    - [review.html](/Users/tnifl/Desktop/SafeToSpend/templates/calendar/review.html)
    - [tax_buffer.html](/Users/tnifl/Desktop/SafeToSpend/templates/calendar/tax_buffer.html)
    - [month.html](/Users/tnifl/Desktop/SafeToSpend/templates/calendar/month.html)
  - 적용 숫자:
    - 정리하기: 세금 보관 권장, 업무 경비, 추정 순이익, 총 보관 권장액
    - 세금보관함: 추가 납부 예상세액, 업무 경비, 추정 순이익, 총 보관 권장액
    - 캘린더: 세금 추정치, 이번 달 남은 돈
  - reduced motion:
    - `prefers-reduced-motion: reduce`면 즉시 값만 갱신

## F. 테스트 결과
- 실행 명령:
```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_receipt_tax_effects \
  tests.test_receipt_tax_effects_integration \
  tests.test_tax_amount_animation_render \
  tests.test_tax_estimate_service \
  tests.test_tax_accuracy_cases \
  tests.test_calendar_monthly_tax_bugfix
```
- 기대 검증 포인트:
  - `high_likelihood`만 reflected
  - `needs_review`는 pending만 증가
  - reflected 상태 변경 시 예상세금 delta가 음수(감소)로 갱신
  - review / tax_buffer / month 템플릿에 애니메이션 data 속성 존재
  - base 공용 토스트 브리지와 애니메이션 JS 로드 확인

## G. 남은 리스크
- `consult_tax_review`는 계속 자동 반영하지 않는다.
- `needs_review`는 보강 전까지 숫자에 반영되지 않는다.
- 현재 검증은 단위/통합/템플릿 렌더 기준이며, 실제 브라우저 E2E는 별도다.
- 기존 수동 `ExpenseLabel.business`와 영수증 reflected 집계가 충돌하지 않도록 수동 확정 거래를 집계에서 건너뛰지만, 이후 수동/자동 상태 병합 정책은 문서로 더 고정할 필요가 있다.

## H. 다음 단계 연결 포인트
- 세무사 패키지 확장:
  - reflected / pending / consult 금액과 보강 메모를 패키지 요약에 포함할 수 있다.
- E2E 검증:
  - review 저장 -> review 토스트
  - review 링크 -> calendar/tax_buffer 애니메이션
  - refresh 후 숫자 일치
- 검토 우선순위:
  - pending / consult 금액이 큰 거래를 정리하기 상단으로 끌어올리는 정렬이 가능하다.

## I. 최종 판정
- 세금 체감 반영 연결 구현 완료
- 현재 범위는 “high_likelihood 영수증만 실제 비용 반영 + 나머지 상태 버킷 분리 + 토스트/숫자 애니메이션 연결”까지다.
- 다음 단계는 세무사 패키지 보강정보 포함 또는 브라우저 E2E 검증으로 이어지는 것이 자연스럽다.

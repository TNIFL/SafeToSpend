# RECEIPT EXPENSE RULE ENGINE IMPLEMENTATION REPORT

## A. 문제 요약
- 영수증 비용처리 안내 UX는 먼저 만들어졌지만, 실제 화면에서 쓰는 판단 결과는 보수적 휴리스틱 설명 계층에 가까웠다.
- 이번 작업의 범위는 자동 비용처리 확정이 아니라, 공식 근거 기반 4단계 상태값 evaluator v1을 서비스 코드로 구현하는 것이다.
- source of truth는 아래 문서 세트로 고정했다.
  - `docs/RECEIPT_EXPENSE_OFFICIAL_SOURCES.md`
  - `docs/RECEIPT_EXPENSE_RULES_TABLE.md`
  - `docs/RECEIPT_EXPENSE_INPUT_SCHEMA.md`
  - `docs/RECEIPT_EXPENSE_OUTPUT_CONTRACT.md`
  - `docs/RECEIPT_EXPENSE_TEST_CASES.md`

## B. 입력 정규화 구현 결과
- 구현 파일: `services/receipt_expense_rules.py`
- 정규화 계층:
  - `ReceiptExpenseInput` dataclass
  - `normalize_receipt_expense_input(...)`
- 반영 필드:
  - `merchant_name`
  - `approved_at`
  - `amount_krw`
  - `payment_method`
  - `source_text_raw`
  - `source_text_normalized`
  - `candidate_transaction_id`
  - `counterparty`
  - `memo`
  - `weekend_flag`
  - `late_night_flag`
  - `receipt_type`
  - `business_context_note`
  - `attendee_note`
  - `evidence_kind`
  - `focus_kind`
- 정규화 처리:
  - 공백 정리
  - 문자열 기본 normalize
  - 금액 int 변환
  - `approved_at` 파싱 실패 fallback
  - `weekend_flag`, `late_night_flag` 자동 유도
  - 입력 누락 시 안전한 기본값 사용

## C. evaluator core 구현 결과
- 구현 파일: `services/receipt_expense_rules.py`
- evaluator 함수:
  - `evaluate_receipt_expense(...)`
- 반환 상태값:
  - `high_likelihood`
  - `needs_review`
  - `do_not_auto_allow`
  - `consult_tax_review`
- v1에서 반영한 핵심 패턴:
  - 교통비
  - 도서/교육/인쇄
  - 업무용 소모품
  - 거래처 식사/접대비 후보
  - 본인 식비/음료
  - 고가 전자기기/가구
  - 경조사비/선물
  - 주말/심야 결제
  - 혼합 가능 지출
- 보수적 정책:
  - 교통비, KTX, 도서/교육, 업무용 소모품만 좁게 `high_likelihood`
  - 카페/식비는 기본 `needs_review`
  - 본인 식사/개인 소비는 `do_not_auto_allow`
  - 고가 장비/전자기기/경조사비/선물은 `consult_tax_review`
- 출력 필드:
  - `level`
  - `label`
  - `summary`
  - `why`
  - `guide_anchor`
  - `follow_up_questions`
  - `evidence_requirements`
  - `official_source_refs`
  - `confidence_note`

## D. follow-up question 구현 결과
- `needs_review` 또는 `consult_tax_review` 위주로 다음 질문을 생성한다.
- 대표 질문:
  - 거래처와의 식사인가요?
  - 주말/심야 이동이 업무와 관련 있나요?
  - 업무용 자산인가요, 소모품인가요?
  - 업무 관련 경조사비인가요?
  - 업무 관련 미팅이나 외근 중 지출인가요?
- 질문은 화면에서 바로 행동으로 이어질 수 있도록 짧게 유지했다.

## E. 안내 UX 연결 결과
- 연결 파일:
  - `services/receipt_expense_guidance.py`
  - `templates/calendar/partials/receipt_expense_hint.html`
- 기존 보수적 프리셋 레이어를 evaluator wrapper로 교체했다.
- 현재 review / receipt wizard는 `build_receipt_expense_inline_guidance(...)`를 통해 evaluator 결과를 사용한다.
- partial은 이제 아래를 렌더한다.
  - label
  - summary
  - why
  - `follow_up_questions` (최대 2개)
  - `evidence_requirements` 요약
  - 기존 guide anchor 링크
  - 기존 안전 고지 문구

## F. 테스트 결과
- 엔진 테스트:
  - `tests/test_receipt_expense_rules_engine.py`
- 통합 테스트:
  - `tests/test_receipt_expense_rules_integration.py`
- 기존 UX 회귀 테스트 정합 수정:
  - `tests/test_receipt_expense_inline_explanations.py`
- 검증 명령은 `docs/DEV_TESTING.md`에 추가했다.

## G. 남은 리스크
- 접대비, 경조사비, 고가 장비는 여전히 보수적으로 내려간다.
- OCR/메모 품질이 낮으면 `needs_review` 비율이 높을 수 있다.
- 현재는 rule-based v1이므로 거래 업종, 사업자 유형, 자산 처리 기준까지 깊게 들어가지는 않는다.
- 실제 세무 판단을 대체하지 않는다.

## H. 다음 단계 연결 포인트
- 다음 단계에서 추가할 수 있는 것:
  - evaluator 결과를 기반으로 follow-up 답변 수집 UI 추가
  - 거래처 식사/경조사비/자산 취득 후보에 대한 세부 질문 폼
  - OCR/영수증 파서가 `business_context_note`, `attendee_note`, `evidence_kind`를 더 풍부하게 채우는 작업
  - 사용자 응답을 다시 evaluator에 재주입하는 2차 판정

## I. 최종 판정
- 규칙 엔진 v1 구현 상태: `규칙 엔진 v1 구현 완료`
- 판단 근거:
  - 입력 정규화 계층 구현
  - 4단계 상태값 evaluator 구현
  - follow-up question / evidence requirements 반환
  - 현재 안내 UX와 연결
  - 대표 케이스 회귀 테스트 추가

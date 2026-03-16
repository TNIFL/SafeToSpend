# RECEIPT EXPENSE FOLLOW-UP FLOW REPORT

## A. 문제 요약
- 규칙 엔진 v1은 `follow_up_questions`를 생성했지만, 사용자가 답변을 저장하고 그 답변으로 2차 판정을 다시 받는 흐름은 없었다.
- 이번 작업의 범위는 영수증 비용처리 follow-up 질문의 `답변 저장 -> 재평가 -> UI 반영 -> 재진입 복원`까지다.
- 자동 승급은 좁게 유지했고, 접대비/경조사비/고가 자산은 여전히 보수적으로 처리한다.

## B. 답변 저장 구조
- 저장 모델: `ReceiptExpenseFollowupAnswer`
- 반영 파일:
  - `domain/models.py`
  - `migrations/versions/e6b4d1a2c9f3_add_receipt_expense_followup_answers.py`
- 저장 기준:
  - `user_pk`
  - `transaction_id`
  - `evidence_item_id`
  - `question_key`
  - `answer_value`
  - `answer_text`
  - `answered_at`
  - `answered_by`
- 제약:
  - `uq_receipt_expense_followup_user_tx_question`
  - 같은 거래/질문 조합은 update로 덮어쓴다.
- 구조 선택 이유:
  - `EvidenceItem.note` 같은 비정형 텍스트에 섞지 않고, 질문/답변을 독립적으로 관리해야 재평가와 복원이 안정적이다.
  - 거래 1건 기준으로 질문 세트를 확장해도 모델 구조를 유지할 수 있다.

## C. evaluator 재평가 로직
- 구현 파일: `services/receipt_expense_rules.py`
- 핵심 추가 함수:
  - `normalize_follow_up_answers(...)`
  - `extract_follow_up_answers_from_form(...)`
  - `validate_follow_up_answers_payload(...)`
  - `evaluate_receipt_expense_with_follow_up(...)`
  - `save_receipt_follow_up_answers_and_re_evaluate(...)`
- 반영 질문 key:
  - `business_meal_with_client`
  - `weekend_or_late_night_business_reason`
  - `asset_vs_consumable`
  - `ceremonial_business_related`
  - `personal_meal_exception_reason`
  - `mixed_spend_business_context`
- 재평가 원칙:
  - `needs_review -> high_likelihood`
    - 제한적 허용
    - 예: 거래처 식사 답변 + 목적 메모 + 위험 시간대 아님
  - `consult_tax_review`
    - 고가 자산/경조사비는 답변이 있어도 유지
  - `do_not_auto_allow`
    - 개인 식사류는 설명이 약하면 유지
  - 불충분/모순 답변
    - 상태 유지
- 재계산 항목:
  - `level`
  - `summary`
  - `why`
  - `follow_up_questions`
  - `applied_follow_up_answers`
  - `evidence_requirements`
  - `confidence_note`

## D. UI 연결 범위
- 반영 파일:
  - `routes/web/calendar/review.py`
  - `services/receipt_expense_guidance.py`
  - `templates/calendar/partials/receipt_expense_hint.html`
  - `templates/calendar/review.html`
  - `templates/calendar/receipt_confirm.html`
  - `templates/calendar/receipt_match.html`
  - `templates/calendar/partials/receipt_wizard_confirm.html`
  - `templates/calendar/partials/receipt_wizard_match.html`
- 연결 화면:
  - review의 영수증/비용처리 카드
  - 영수증 wizard 확인 단계
  - 영수증 wizard 매칭 단계
- 사용자는 아래를 바로 볼 수 있다.
  - 현재 상태값
  - 왜 그렇게 판단되는지
  - 반영된 답변
  - 아직 필요한 추가 질문과 증빙 요구사항
- 저장 후 동작:
  - 같은 화면으로 redirect
  - 다음 진입 시 기존 답변 복원

## E. 상태 전이 규칙
- 대표 전이:
  1. 거래처 식사 후보 + 거래처 식사 확인 + 목적 메모
     - `needs_review -> high_likelihood`
  2. 주말/심야 교통비 + 업무 관련 사유 메모
     - `needs_review -> high_likelihood`
  3. 본인 식사 후보 + 짧거나 빈약한 설명
     - `do_not_auto_allow` 유지
  4. 고가 전자기기 + 업무용 자산 답변
     - `consult_tax_review` 유지
  5. 경조사비/선물 + 업무 관련 답변
     - `consult_tax_review` 유지

## F. 테스트 결과
- 규칙 테스트:
  - `tests/test_receipt_expense_followup_rules.py`
- 통합 테스트:
  - `tests/test_receipt_expense_followup_integration.py`
- 기존 엔진/UX 회귀 포함 실행:
  - `tests.test_receipt_expense_rules_engine`
  - `tests.test_receipt_expense_rules_integration`
  - `tests.test_receipt_expense_inline_explanations`
- 실행 결과:
  - `Ran 39 tests ... OK`

## G. 남은 리스크
- 접대비/경조사비/고가 자산은 여전히 보수적이다.
- 자유서술 답변은 키워드 기반 반영이라 장문 해석의 한계가 있다.
- 확인 단계에서 사용자가 본문 폼 값을 수정한 뒤 follow-up 폼만 먼저 저장하면, 아직 저장되지 않은 수정값은 follow-up 재평가에 반영되지 않는다.
- 실제 세무 판단을 대체하지 않는다.

## H. 다음 단계 연결 포인트
- follow-up 답변을 더 잘 받기 위한 다음 단계:
  - 질문별 선택지 세분화
  - 답변 입력 후 증빙 업로드 유도
  - 답변 품질이 낮을 때 추가 질문 단계 분기
  - 장문 메모 대신 구조화된 참석자/거래처/목적 입력 UI
- 엔진 측 다음 단계:
  - `applied_follow_up_answers`를 패키지/정리 리포트에 재사용
  - 업종/사용자 사업 유형별 rule refinement

## I. 최종 판정
- 구현 상태: `follow-up 재평가 플로우 구현 완료`
- 판단 근거:
  - 저장 모델 추가
  - evaluator 2차 판정 구현
  - review / wizard UI 저장/복원 연결
  - 상태 전이 회귀 테스트 통과

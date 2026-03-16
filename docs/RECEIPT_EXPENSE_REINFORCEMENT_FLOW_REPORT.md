# Receipt Expense Reinforcement Flow Report

## A. 문제 요약
- follow-up 질문 답변까지는 저장되고 2차 재평가가 가능했지만, 실제 비용처리 판단을 더 보강할 구조화된 추가 설명/참석자/관계/용도/보강 파일 메타 저장 계층이 없었다.
- 그 결과 `needs_review` 상태에서 사용자가 무엇을 더 보강해야 하는지, 무엇을 이미 보강했는지, 남은 부족 항목이 무엇인지 화면에서 일관되게 확인하기 어려웠다.

## B. 보강 저장 구조
- 별도 테이블 `receipt_expense_reinforcements`를 추가했다.
- 선택 이유:
  - `receipt_expense_followup_answers`는 질문/답변 단위 저장에 맞다.
  - 보강 정보는 거래 단위의 문맥 스냅샷과 파일 메타를 같이 보관해야 하므로 1행 update 구조가 더 안정적이다.
- 저장 필드:
  - `user_pk`
  - `transaction_id`
  - `evidence_item_id`
  - `business_context_note`
  - `attendee_names`
  - `client_or_counterparty_name`
  - `ceremonial_relation_note`
  - `asset_usage_note`
  - `weekend_or_late_night_note`
  - `supporting_file_key`
  - `supporting_file_name`
  - `supporting_file_mime_type`
  - `supporting_file_size_bytes`
  - `supporting_file_uploaded_at`
  - `updated_at`
  - `updated_by`
- unique 정책:
  - `(user_pk, transaction_id)` 1행 유지, 재입력 시 update 우선.
- migration:
  - `fa13c7d9e2b4_add_receipt_expense_reinforcements.py`
  - 실제 적용 확인: `FLASK_APP=app.py .venv/bin/flask db upgrade`

## C. reinforcement requirements/remaining gaps 생성 규칙
- evaluator 결과에 아래 필드를 추가했다.
  - `reinforcement_requirements`
  - `reinforcement_summary`
  - `remaining_gaps`
  - `reinforcement_readiness`
  - `applied_reinforcement`
- category별 기본 보강 요구사항:
  - 거래처 식사/접대비 후보
    - 필수: `business_context_note`, `attendee_names`
    - 선택: `client_or_counterparty_name`, `supporting_file`
  - 주말·심야 결제
    - 필수: `weekend_or_late_night_note`
    - 선택: `business_context_note`
  - 고가 전자기기/가구
    - 필수: `asset_usage_note`, `business_context_note`
    - 선택: `supporting_file`
  - 경조사비/선물
    - 필수: `ceremonial_relation_note`, `business_context_note`
    - 선택: `client_or_counterparty_name`, `supporting_file`
  - 개인/업무 혼합 가능 지출
    - 필수: `business_context_note`
- `reinforcement_readiness` 값:
  - `not_needed`
  - `none`
  - `partial`
  - `sufficient`
- 보수적 원칙:
  - 고가 자산/경조사비/선물/개인 식비는 보강돼도 쉽게 완화하지 않는다.
  - 보강 파일 업로드만으로는 승급하지 않는다.

## D. 저장 + 재평가 서비스 계층
- `services/receipt_expense_rules.py`에 추가:
  - `normalize_reinforcement_payload(...)`
  - `extract_reinforcement_payload_from_form(...)`
  - `validate_reinforcement_payload(...)`
  - `load_receipt_reinforcement_map(...)`
  - `save_receipt_reinforcement_and_re_evaluate(...)`
- 서비스 책임:
  1. 보강 payload 검증
  2. 보강 텍스트 저장/업데이트
  3. 선택 업로드 파일 저장 및 메타 연결
  4. 기존 follow-up answers 로드
  5. evaluator 재실행
  6. 결정 결과 반환
- 파일 저장은 기존 증빙 저장 경로(`store_evidence_file_multi`)를 재사용한다.
- 기존 원본 EvidenceItem 파일 정책은 유지하고, 보강 파일 메타만 reinforcement row에 연결한다.

## E. UI 연결 범위
- review 카드
- receipt confirm
- receipt match
- modal wizard confirm/match partial
- 공용 partial: `templates/calendar/partials/receipt_expense_hint.html`
- 화면에서 보이는 항목:
  1. 현재 판정 상태
  2. 반영된 follow-up 답변
  3. 반영된 보강 정보
  4. 남은 부족 항목
  5. 보강 저장 폼
- 보강 입력 항목:
  - 업무 관련 설명 메모
  - 참석자
  - 거래처/상대방
  - 관계/행사 설명
  - 자산/소모품 용도 메모
  - 주말·심야 사유 메모
  - 보강 파일 업로드(선택)
- 재진입 시 기존 보강 정보와 업로드된 파일명이 복원된다.

## F. 상태 전이 테스트 결과
- 실행 명령:
```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_receipt_expense_reinforcement_rules \
  tests.test_receipt_expense_reinforcement_integration \
  tests.test_receipt_expense_followup_rules \
  tests.test_receipt_expense_followup_integration \
  tests.test_receipt_expense_rules_engine \
  tests.test_receipt_expense_rules_integration \
  tests.test_receipt_expense_inline_explanations \
  tests.test_receipt_expense_guidance_page \
  tests.test_receipt_expense_guide_entrypoints
```
- 결과:
  - `Ran 54 tests in 0.157s`
  - `OK`
- 추가 스모크:
```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from services.receipt_expense_rules import evaluate_receipt_expense_with_follow_up
base = evaluate_receipt_expense_with_follow_up(
    payload={
        'merchant_name': '스타벅스',
        'memo': '거래처 미팅 커피',
        'amount_krw': 18000,
        'approved_at': '2026-03-12 14:30',
    },
    follow_up_answers={
        'business_meal_with_client': {'answer_value': 'yes', 'answer_text': 'A사 미팅'}
    },
)
reinforced = evaluate_receipt_expense_with_follow_up(
    payload={
        'merchant_name': '스타벅스',
        'memo': '거래처 미팅 커피',
        'amount_krw': 18000,
        'approved_at': '2026-03-12 14:30',
    },
    follow_up_answers={
        'business_meal_with_client': {'answer_value': 'yes', 'answer_text': 'A사 미팅'}
    },
    reinforcement_data={
        'business_context_note': 'A사 제안 미팅 중 음료 결제',
        'attendee_names': 'A사 김팀장, 박대리',
        'client_or_counterparty_name': 'A사',
    },
)
print({'before_level': base['level'], 'before_gaps': base['remaining_gaps'], 'after_level': reinforced['level'], 'after_gaps': reinforced['remaining_gaps'], 'after_readiness': reinforced['reinforcement_readiness']})
PY
```
- 스모크 결과:
  - `before_level=needs_review`
  - `after_level=high_likelihood`
  - `after_gaps=[]`
  - `after_readiness=sufficient`

## G. 남은 리스크
- 접대비/경조사비/고가 자산은 여전히 보수적이다.
- 보강 파일이 있어도 자동 확정은 아니다.
- 자유서술형 메모는 rule-based 해석 한계가 있다.
- confirm 단계 본문 수정값과 보강 저장이 별도 submit이므로, 본문 수정 미저장 상태에선 보강 재평가에 그 값이 반영되지 않는다.

## H. 다음 단계 연결 포인트
- 세금 체감 반영 연결
  - 보강 완료도가 높아진 영수증을 세금 절감 체감 UI와 연결할 수 있다.
- 세무사 패키지 확장
  - 참석자/관계/용도 메모를 패키지 manifest 또는 인덱스에 포함할지 결정 가능하다.
- 검토 우선순위 정렬
  - `remaining_gaps`와 `reinforcement_readiness`를 기반으로 검토 우선순위를 재정렬할 수 있다.

## I. 최종 판정
- 추가 증빙 보강 플로우 구현 완료
- 현재 범위는 “보강 정보 저장 + 재평가 + 남은 부족 항목 표시”까지다.
- 이후 단계는 세금 체감 반영 연결 또는 세무사 패키지에 보강정보 포함 여부를 결정하는 작업으로 자연스럽게 이어질 수 있다.

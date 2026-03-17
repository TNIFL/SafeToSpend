# 영수증 비용처리 규칙 엔진 준비 보고서

## A. 안내 페이지 진입성 개선 결과
- 전역 진입점: `templates/base.html` footer에 `비용처리 안내` 링크 추가
- 상황별 진입점:
  - `templates/calendar/review.html` 헤더 액션
  - `templates/calendar/tax_buffer.html` 헤더 액션
  - `templates/calendar/partials/receipt_expense_hint.html`의 `왜 이렇게 보나요?` anchor 링크
- 사용자는 최소 3곳 이상에서 `/guide/expense`로 진입 가능하다.

## B. 공식 근거 레지스트리 결과
- 문서: `docs/RECEIPT_EXPENSE_OFFICIAL_SOURCES.md`
- 기준 출처:
  - 소득세법 제27조
  - 소득세법 제33조
  - 소득세법 제35조
  - 소득세법 제160조의2
  - 소득세법 시행령 제208조의2
- 비공식 출처는 규칙 근거에서 제외한다.

## C. 규칙표 초안 결과
- 문서: `docs/RECEIPT_EXPENSE_RULES_TABLE.md`
- canonical 상태값:
  - `high_likelihood`
  - `needs_review`
  - `do_not_auto_allow`
  - `consult_tax_review`
- 택시, KTX, 도서, 교육, 문구는 보수적 `high_likelihood` 후보
- 카페, 거래처 식사, 주말/심야, 혼합 가능 지출은 `needs_review`
- 본인 식사, 생활형 소비는 `do_not_auto_allow`
- 고가 전자기기, 가구, 경조사비는 `consult_tax_review`

## D. 입력 스키마 결과
- 문서: `docs/RECEIPT_EXPENSE_INPUT_SCHEMA.md`
- 최소 입력 필드:
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
- 거래 매칭 정보와 OCR 원문을 분리해서 고정했다.

## E. 출력 계약 결과
- 문서: `docs/RECEIPT_EXPENSE_OUTPUT_CONTRACT.md`
- 출력 필드:
  - `level`
  - `label`
  - `summary`
  - `why`
  - `guide_anchor`
  - `follow_up_questions`
  - `evidence_requirements`
- 현재 `/guide/expense` anchor와 UI 라벨 구조에 맞춰 설계했다.

## F. 테스트 케이스 결과
- 문서: `docs/RECEIPT_EXPENSE_TEST_CASES.md`
- 대표 케이스 10종 고정
  - 택시
  - KTX
  - 도서
  - 유료 강의
  - 다이소/문구
  - 스타벅스/카페
  - 본인 식사
  - 거래처 식사
  - 애플스토어 고가 결제
  - 경조사비 관련 증빙

## G. 남은 리스크
- 현재는 규칙 엔진 준비 단계다. 실제 엔진은 아직 없음.
- 거래 목적, 참석자, 업무 맥락 같은 사용자 입력이 없으면 `needs_review` 비율이 높게 남을 수 있다.
- 접대비·경조사비·고가 장비는 여전히 세무 검토 의존도가 높다.

## H. 다음 구현 단계
1. 입력 스키마 기준으로 OCR/거래/사용자 메모를 하나의 규칙 입력 객체로 조립
2. `RECEIPT_EXPENSE_RULES_TABLE.md` 기반의 1차 규칙 evaluator 구현
3. `RECEIPT_EXPENSE_OUTPUT_CONTRACT.md` 형식으로 UI에 결과 반환
4. `RECEIPT_EXPENSE_TEST_CASES.md` 10종을 우선 회귀 테스트로 고정

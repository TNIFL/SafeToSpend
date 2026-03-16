# 영수증 비용처리 안내 UX 보고서

## A. 문제 요약
- 사용자는 영수증이 올라오면 곧바로 "비용처리 된다/안 된다"로 이해하기 쉽다.
- 현재 단계에서는 비용처리 엔진보다 먼저, 서비스가 어떤 톤과 구조로 안내해야 안전한지 고정하는 것이 목적이다.

## B. 정보구조(IA)
1. 상단 요약: 이 페이지가 비용처리 확정이 아니라 보조 안내라는 점
2. 빠른 구분: 가능성 높음 / 추가 확인 / 자동 인정 안 함 / 세무 검토 권장
3. 자주 헷갈리는 사례
4. 영수증 올릴 때 같이 남기면 좋은 정보
5. 최종 주의 문구
6. 공식 근거

## C. 카피 구조 원칙
- 짧게 쓴다.
- 단정 대신 가능성 중심으로 쓴다.
- 전문용어보다 사용자가 이해할 수 있는 단어를 쓴다.
- 한 줄짜리 "왜 이런 판단인지"를 붙인다.

### 라벨 구조
| 상태 | 기본 라벨 | 설명 톤 |
| --- | --- | --- |
| high | 비용처리 가능성이 높은 편이에요 | 비교적 설명되기 쉬운 항목 |
| review | 추가 확인이 필요해요 | 업무/개인 혼합 가능성 안내 |
| block | 자동으로 인정하지 않아요 | 개인·가사 관련 위험 안내 |
| consult | 세무 검토가 필요할 수 있어요 | 자산성/특수 규정 검토 안내 |

## D. 사용자 안내 페이지 반영 결과
- 실제 페이지: `/guide/expense`
- 페이지 구성
  - 빠른 구분 카드
  - 4단계 상세 섹션
  - 자주 헷갈리는 사례
  - 영수증 업로드 팁
  - 최종 주의 문구
  - 공식 근거 링크
- 반영 파일
  - `routes/web/guide.py`
  - `templates/guide/expense-guide.html`

## D-1. 비용처리 안내 진입 전략

### 이전 상태 점검
| 위치 | 현재 상태 | 판단 |
| --- | --- | --- |
| 전역 진입점 | 없음 | 사용자가 직접 URL을 알지 않으면 찾기 어려움 |
| review/receipt wizard | `왜 이렇게 보나요?` 링크만 있음 | 상황별 진입은 가능하지만 발견성이 약함 |
| 세금/정리 관련 화면 | 없음 | 비용처리 판단 기준을 되짚기 어려움 |

### 확정한 진입 전략
| 우선순위 | 위치 | 링크 라벨 | 필요한 이유 |
| --- | --- | --- | --- |
| 1 | 공통 footer | 비용처리 안내 | 어떤 화면에 있든 언제든 다시 볼 수 있어야 함 |
| 2 | 정리하기(review) 헤더 | 비용처리 안내 | 실제 영수증/지출 정리 순간에 바로 접근 가능해야 함 |
| 3 | 세금보관함 헤더 | 어떤 영수증이 비용처리되나요? | 세금 보관 판단과 비용처리 기준을 연결해 볼 수 있어야 함 |
| 4 | receipt wizard 인라인 카드 | 왜 이렇게 보나요? | 현재 거래 판단 이유를 anchor와 함께 즉시 확인할 수 있어야 함 |

### 반영 후 상태
- 전역 1곳 + 상황별 3곳으로 최소 3개 이상 진입점 확보
- 숨겨진 개발 경로가 아니라 실제 사용자 동선 안에서 접근 가능
- receipt wizard에서는 일반 링크 대신 현재 판정 anchor로 바로 이동

## E. 인라인 설명 반영 결과
- review 리스트의 `receipt_required`, `receipt_attach`, `expense_confirm` 카드에 상태 라벨과 짧은 이유를 붙인다.
- 영수증 wizard 화면
  - 업로드 단계
  - 자동 인식 확인 단계
  - 결제 내역 연결 단계
  에도 동일한 상태 라벨과 이유를 노출한다.
- 인라인 설명은 모두 `왜 이렇게 보나요?` 링크로 안내 페이지 해당 섹션에 연결된다.
- 반영 파일
  - `templates/calendar/review.html`
  - `templates/calendar/tax_buffer.html`
  - `templates/calendar/evidence_upload.html`
  - `templates/calendar/receipt_confirm.html`
  - `templates/calendar/receipt_match.html`
  - `templates/calendar/partials/receipt_wizard_upload.html`
  - `templates/calendar/partials/receipt_wizard_confirm.html`
  - `templates/calendar/partials/receipt_wizard_match.html`
  - `templates/calendar/partials/receipt_expense_hint.html`

## F. 고지 문구 반영 결과
- 서비스의 분류 결과는 보조 판단입니다.
- 최종 필요경비 인정 여부는 실제 거래 사실, 증빙, 법령, 과세관청 판단에 따라 달라질 수 있습니다.
- 개인지출 또는 가사 관련 경비는 필요경비에 산입되지 않을 수 있습니다.

## G. 테스트 결과
- 실행 결과
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_guide_entrypoints`
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_guidance_page`
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_inline_explanations`
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_review_detail_fields`
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_review_detail_render`
- 확인 범위
  - `/guide/expense` 렌더
  - 전역/상황별 진입점 3곳 이상 존재
  - 핵심 섹션과 고지 문구 존재
  - 라벨 4종 중 적절한 상태 출력
  - 가이드 링크 존재
  - review 템플릿 연결 유지

## H. 남은 리스크
- 지금 단계의 설명은 규칙 엔진이 아니라 보수적 프리셋이다.
- 실제 거래 사실, 참석자, 메모, 적격증빙 여부가 빠지면 설명 품질이 낮아질 수 있다.
- 접대비·경조사비·고가 장비는 후속 규칙 엔진에서 추가 질문이 필요하다.

## I. 다음 단계 연결 포인트
- 규칙 엔진이 내려줄 값
  - `level`
  - `label`
  - `short_reason`
  - `why`
  - `guide_anchor`
- 추가 질문이 필요한 케이스
  - 거래처 식사/접대비 후보
  - 주말·심야 결제
  - 고가 장비/전자기기
  - 경조사비·선물
- 현재 UX는 위 상태값을 그대로 받아 확장 가능하도록 설계한다.

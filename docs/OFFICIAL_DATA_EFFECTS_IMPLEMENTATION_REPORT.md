# 공식 자료 effect 구현 보고서

## 범위

- 홈택스 `hometax_withholding_statement`의 원천징수 성격 값을 세금 보관 권장액 계산에 연결
- 홈택스 `hometax_tax_payment_history`의 납부세액 합계를 `official_paid_tax_krw` 후보로 읽을 수 있게 확장
- NHIS `nhis_payment_confirmation`을 기준일/신뢰도/최근 공식 납부금액 참고 상태로 연결
- NHIS `nhis_eligibility_status`를 가입 상태/재확인 UX 보조 자료로 연결
- 반영 기준축은 전용 trust field만 사용
- 실제 calendar route 파일은 `routes/web/calendar/tax.py`, `routes/web/calendar/review.py`가 아니라 `routes/web/web_calendar.py`

## 세금 반영 범위

- 직접 반영 대상: `hometax_withholding_statement`
- 직접 반영 후보 확대: `hometax_tax_payment_history`
  - `official_paid_tax_krw` 후보로만 사용
- 직접 반영 불가: `hometax_business_card_usage`
  - 참고 정보만 유지

## NHIS 반영 범위

- 참고 상태 대상: `nhis_payment_confirmation`
- 참고 상태 보강 대상: `nhis_eligibility_status`
- 이번 단계 금지:
  - 건보료 완전 확정
  - 현재 달 계산값 직접 덮어쓰기

## trust field 기준축 사용 규칙

반영 강도 판단은 아래 전용 필드만 사용한다.

- `trust_grade`
- `verification_status`
- `structure_validation_status`

`extracted_payload_json`, `extracted_key_summary_json`의 임시 trust 값으로 반영 강도를 결정하지 않는다. 숫자 추출은 gate 통과 후 payload에서 읽는다.

## 숫자 시각 피드백 정책

| 화면 | 대상 숫자 | before 값 | after 값 | delta 표시 | 애니메이션 조건 | reduced motion 대체 | 금지 표현 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `overview` | 예상세금, 세금 보관 권장액 | `tax_due_before_official_data_krw`, 공식 자료 반영 전 보관 기준값 | `tax_due_after_official_data_krw`, 반영 후 보관 권장값 | 반영 후 값이 실제로 달라질 때만 | `official_tax_effect_status=applied` 이고 delta가 0이 아닐 때만 | 애니메이션 없이 최종값과 delta를 즉시 렌더 | `확정`, `보증`, `100% 정확` |
| `tax_buffer` | 예상세금, 세금 보관 권장액, 공식 자료 반영 delta | `tax_due_before_official_data_krw`, 기존 보관 기준값 | `tax_due_after_official_data_krw`, 공식 자료 반영 후 보관 기준값 | 예 | `overview`와 동일. 단 `reference_only`면 애니메이션 금지 | 애니메이션 없이 before/after/delta 카드만 렌더 | `확정`, `보증`, `100% 정확` |
| `review` | 이번 달 공식 자료 반영 delta 요약 | `tax_due_before_official_data_krw` | `tax_due_after_official_data_krw` | notice 안에서만 요약 | 애니메이션 없음 | 동일 | `확정`, `보증`, `100% 정확` |
| `overview`, `tax_buffer`, `review` 의 NHIS 영역 | 최근 공식 납부금액 참고, 기준일, 재확인 권장 | 없음 | 최근 공식 납부금액 참고값만 표시 | 금지 | NHIS는 숫자 애니메이션 금지 | 기준일/참고 금액/재확인 안내 즉시 표시 | `건보료 확정`, `보증`, `100% 정확` |

### 시각 피드백 원칙

- 값이 실제로 안 바뀌면 애니메이션을 켜지 않는다.
- `reference_only`는 강한 강조 대신 약한 배지와 설명 위주로 유지한다.
- `review_needed`, `stale`, `none`은 변화 강조보다 검토 필요 또는 재확인 안내를 우선한다.
- 구조 검증과 기관 확인은 같은 뜻이 아니라는 안내를 notice에 계속 유지한다.
- NHIS는 기준일, 신뢰도, 최근 공식 납부금액 참고만 보여 주고 직접 계산 확정처럼 보이게 만들지 않는다.

## 구현 결과

### Ticket 2

- `services/official_data_effects.py`
  - `select_best_official_tax_documents(...)`
  - `build_official_tax_effect_state(...)`
  - `collect_official_tax_effects_for_user_month(...)`
  - `build_official_tax_effect_notice_context(...)`
- `services/nhis_effects.py`
  - `is_nhis_snapshot_stale(...)`
  - `build_nhis_effect_state(...)`
  - `collect_nhis_effects_for_user(...)`
  - `build_nhis_effect_notice_context(...)`

### Ticket 3

- `services/official_data_parsers.py`에 `parse_hometax_tax_payment_history(...)`를 추가했다.
- 납부내역 fixture에서 기준일, 납부일, 납부세액 합계, 세목 요약, 귀속기간을 읽는다.
- 필수 금액/기준일/세목 누락 시 `needs_review`로 닫는다.

### Ticket 4

- `services/official_data_parsers.py`에 `parse_nhis_eligibility_status(...)`를 추가했다.
- `services/nhis_effects.py`가 자격 상태 자료를 reason/recheck 판단에만 사용한다.
- 직접 금액 반영이나 건보료 확정 반영은 하지 않는다.

### Ticket 5

- `services/risk.py`에서 공식 자료 반영 전/후 세금 값을 함께 계산한다.
- `services/official_data_effects.py`가 원천징수와 납부내역을 함께 보고 `official_withheld_tax_krw`, `official_paid_tax_krw`를 나눠 계산한다.
- 추가 필드
  - `tax_due_before_official_data_krw`
  - `tax_due_after_official_data_krw`
  - `official_withheld_tax_krw`
  - `official_paid_tax_krw`
  - `tax_delta_from_official_data_krw`
  - `official_tax_reference_date`
  - `official_tax_effect_status`
  - `official_tax_effect_strength`
  - `official_tax_effect_reason`
  - `official_tax_effect_source_count`

- NHIS는 `reference_available / stale / review_needed / none` 상태로만 연결한다.
- 기준일, 최근 공식 납부금액, 재확인 여부만 노출한다.
- 자격 상태 자료가 있으면 reason에 가입 상태/최근 변동일 설명을 보강한다.
- 건보료 계산값을 바로 덮어쓰지 않는다.

### Ticket 6

- `templates/partials/official_data_effect_notice.html`가 문서 종류 요약을 함께 렌더한다.
- 세금 notice는 `원천징수 반영 / 납부내역 반영 / 사업용 카드 참고`를 구분한다.
- NHIS notice는 `납부확인 참고 / 자격자료 참고`를 구분한다.

### Ticket 7

- 숫자 시각 피드백 정책을 문서와 회귀 절차에 고정한다.
- `overview`, `tax_buffer`, `review`는 같은 공식 자료 effect 상태를 공유하되 화면별로 강조 강도를 다르게 쓴다.
- 애니메이션은 `applied + delta != 0`에서만 허용하고 reduced motion 환경에서는 즉시 최종값을 보여 준다.

### Ticket 8

- `services/official_data_effects.py`
  - `build_official_tax_visual_feedback(...)`
  - `build_official_tax_visual_feedback_for_overview(...)`
  - `build_official_tax_visual_feedback_for_tax_buffer(...)`
- `services/nhis_effects.py`
  - `build_nhis_visual_feedback(...)`
- 숫자 시각 피드백용 before/after/delta/animate 여부를 서비스 계층에서 미리 계산한다.

### Ticket 9

- `routes/web/overview.py`
  - `official_tax_visual_feedback`
  - `nhis_visual_feedback`
- `routes/web/web_calendar.py`
  - `official_tax_visual_feedback`
  - `nhis_visual_feedback`
- notice용 상태와 숫자 피드백용 상태를 분리해 템플릿에 전달한다.

### Ticket 10

- `templates/overview.html`
  - 공식 자료 숫자 변화 패널 추가
  - 예상세금 / 세금 보관 권장액 before/after/delta 표시
- `templates/calendar/tax_buffer.html`
  - 공식 자료 숫자 변화 패널 추가
  - NHIS 참고 상태 스트립 추가
- `templates/calendar/review.html`
  - review surface용 요약 notice 연결
- `templates/partials/official_data_effect_notice.html`
  - review용 delta 요약과 세금 보관함 이동 CTA 추가

### Ticket 11

- `static/js/official-data-number-animate.js`
  - `applied + delta != 0`에서만 숫자 애니메이션
  - `reference_only / stale / review_needed / none`은 애니메이션 금지
  - reduced motion 환경에서는 즉시 최종값 렌더

## 사용자 UX 반영 위치

- `templates/overview.html`
- `templates/calendar/tax_buffer.html`
- `templates/calendar/review.html`
- `templates/partials/official_data_effect_notice.html`
- `static/js/official-data-number-animate.js`

## 숫자 시각 피드백 구현 결과

- `overview`
  - 메인 카드 아래에 공식 자료 숫자 변화 패널을 추가했다.
  - 예상세금과 세금 보관 권장액의 before/after/delta를 같은 덩어리에서 보여 준다.
- `tax_buffer`
  - KPI 카드 안에서 공식 자료 기준 숫자 변화 패널을 추가했다.
  - NHIS는 참고 스트립으로만 붙이고 숫자 애니메이션은 하지 않는다.
- `review`
  - 숫자 카드 대신 delta 요약 notice와 `세금 보관함에서 자세히 보기` CTA만 추가했다.

## 애니메이션 조건 / reduced motion 처리

- 애니메이션 대상
  - `overview` 예상세금
  - `overview` 세금 보관 권장액
  - `tax_buffer` 예상세금
  - `tax_buffer` 세금 보관 권장액
- 애니메이션 조건
  - `official_tax_effect_status=applied`
  - delta가 실제로 0이 아님
- 애니메이션 제외
  - `reference_only`
  - `review_needed`
  - `stale`
  - `none`
  - NHIS 모든 숫자
- reduced motion
  - `prefers-reduced-motion: reduce`이면 JS가 즉시 최종값으로 렌더한다.

## verification 연계 규칙

- 기준축
  - `trust_grade`
  - `verification_status`
  - `structure_validation_status`
- 세금 숫자 피드백
  - `applied` + `verification_status=succeeded` 또는 `trust_grade=A`
    - `confidence_label = 신뢰도 높음`
    - `verification_badge = 기관 확인 메타 있음`
    - 강한 표현 허용
  - `applied` + 구조 검증 통과 + 기관 확인 전(`trust_grade=B`)
    - `confidence_label = 보수 반영`
    - `verification_badge = 구조 검증 통과`
    - 숫자 반영은 유지하되 강도는 한 단계 낮춘다
  - `reference_only`
    - `confidence_label = 참고용`
    - `verification_badge = 참고 자료`
    - 강한 반영 표현 금지
  - `stale` / `review_needed`
    - `confidence_label = 재확인 필요`
    - 강한 반영 표현 금지
    - 재확인 또는 검토 안내 우선
- NHIS 참고 피드백
  - verification이 있어도 `참고 신뢰도 높음/보통`까지만 허용
  - NHIS는 계속 참고 정보, 기준일, 최근 공식 납부금액, 상태 설명 중심
  - 세금처럼 확정형 숫자 표현이나 애니메이션은 쓰지 않는다

## verification 연계 view-model

- `official_tax_visual_feedback`
  - `confidence_label`
  - `verification_badge`
  - `verification_hint`
  - `verification_level`
  - `is_high_confidence_effect`
- `official_tax_effect_notice`
  - 위 5개 필드를 notice 요약에도 동일하게 반영
- `nhis_visual_feedback`
  - `confidence_label`
  - `verification_badge`
  - `verification_hint`
  - `verification_level`
  - `is_high_confidence_effect`
- `nhis_effect_notice`
  - NHIS도 같은 필드명을 쓰되 참고 톤만 유지한다

## 새 document_type 2종

- `hometax_tax_payment_history`
  - 최소 추출값: 기준일, 최근 납부일, 납부세액 합계, 세목 요약, 귀속기간
  - effect 범위: `official_paid_tax_krw` 후보
- `nhis_eligibility_status`
  - 최소 추출값: 기준일, 가입자 유형, 자격 상태, 취득일/상실일, 최근 변동일
  - effect 범위: `nhis_effect_reason`, `nhis_recheck_required` 보조

## 금지 해석 유지

- 납부내역 1종만으로 신고 완료/세액 완전 확정으로 말하지 않는다.
- NHIS 자격 자료만으로 건보료 완전 확정으로 말하지 않는다.
- 구조 검증과 기관 확인을 같은 뜻으로 설명하지 않는다.

## 금지 해석

- 비용 확정
- 신고 완료
- 건보료 완전 확정
- 기관이 법적으로 보장한 값
- 구조 검증과 기관 확인을 같은 뜻으로 해석

## 테스트 기대값이 바뀐 이유

- 세금 보관 권장액은 이제 공식 자료 반영 후 값이 기본값이다.
- 대신 반영 전 값과 delta를 함께 남겨 화면과 테스트에서 before/after를 설명한다.
- NHIS는 참고 상태/기준일 중심 문구만 허용해서, 설명에 `확정` 계열 표현을 넣지 않는다.
- parser registry/parser 테스트는 새 fixture 2종과 partial fixture 2종을 추가하면서 row count와 supported document_type 기대값이 늘었다.
- effect notice 렌더 테스트는 문서 종류 요약(`원천징수 반영`, `납부내역 반영`, `자격자료 참고`)을 확인하도록 바뀌었다.
- 숫자 시각 피드백 테스트는 `official_tax_visual_feedback`, `nhis_visual_feedback`, `data-od-*` 속성, review delta CTA를 확인하도록 바뀌었다.

## 남은 리스크

- C 등급 자료의 숫자 직접 반영은 이번 단계에서 보수적으로 막아 두었다.
- 운영 데이터에서 문서 종류가 늘어나면 effect 정책 세분화가 더 필요하다.
- 홈택스 납부내역 양식 변형이 많으면 초기에는 `needs_review`로 더 자주 닫힐 수 있다.
- NHIS 자격 상태 문구 체계가 더 다양하면 상태 normalize 규칙이 후속으로 필요할 수 있다.
- 브라우저별 숫자 애니메이션 체감 속도는 후속 미세 조정이 필요할 수 있다.

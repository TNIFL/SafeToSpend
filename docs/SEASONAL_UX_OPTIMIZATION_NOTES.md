# Seasonal UX Optimization Notes

## 1. 이번 단계 해석 기준
- 방향성 해석 가능 최소 기준
  - `shown >= 30`
  - `clicked >= 10`
  - `completed >= 5`
- 위 기준 미달이면
  - 대규모 priority 재배치 금지
  - 시즌별 구조 변경 금지
  - CTA/카피/anchor와 같은 저위험 미세조정만 허용

## 2. 이번 단계에서 가장 먼저 줄여야 할 마찰
### `offseason_monthly_review@review`
- 현재 신호
  - `shown = 4`
  - `clicked = 1`
  - `landed = 1`
  - `completed = 0`
- 현재 가설
  - 같은 review 화면으로 다시 들어오지만, landed 뒤에 무엇을 먼저 처리해야 하는지 충분히 직접적이지 않았다.
  - 카드가 기대하는 행동은 “pending/reinforcement 정리”인데, CTA는 generic self-link처럼 읽혔다.
- 이번 단계 조치
  - CTA를 `반영 대기 항목부터 정리하기`로 수정
  - anchor를 실제 작업 영역 `#review-worklist`로 연결
  - summary를 “반영 대기 항목부터 처리” 기준으로 더 직접화

### `offseason_accuracy@tax_buffer`
- 현재 신호
  - `shown = 5`
  - `clicked = 0`
- 현재 가설
  - `정확도`라는 추상적 표현이 현재 화면에서 무엇을 해야 하는지 바로 떠오르지 않았다.
  - 같은 화면에서 숫자를 다시 보는 행동과 profile 입력 보완 행동이 분리돼 보여서 CTA 해석이 약했다.
- 이번 단계 조치
  - CTA를 `3.3%·빠진 세금 확인하기`로 유지/강화
  - same-screen context CTA를 `예상세금·보관액 바로 보기`로 변경
  - summary도 “이미 빠진 세금 / 지금 보이는 숫자” 중심으로 더 직접화

### `offseason_package_ready@package`
- 현재 신호
  - shown은 있으나 클릭/완료 표본이 부족하다.
- 현재 가설
  - package 화면의 same-screen CTA가 “아래 점검표 보기” 수준이라 마지막 준비 행동이 덜 분명하게 느껴질 수 있다.
- 이번 단계 조치
  - CTA를 `세무사 보내기 전 마지막 점검 보기`로 명확화
  - anchor를 readiness 블록으로 유지

## 3. 완료율 0 카드의 friction note
### review 카드
- 카드 기대 행동
  - pending/reinforcement 대상 저장 완료
- landed 이후 실제 행동
  - lane/tab 전환
  - follow-up 저장
  - reinforcement 저장
- 사용자에게 부족했던 설명
  - “어디부터 손대야 하는지”
  - “같은 화면 안에서 실제 작업 구역이 어디인지”

### tax_buffer 카드
- 카드 기대 행동
  - 3.3% / 이미 빠진 세금 입력 보완 또는 현재 KPI 재확인
- landed 이전 마찰
  - 클릭 자체가 적다.
- 사용자에게 부족했던 설명
  - CTA가 너무 추상적이어서 무엇을 얻게 되는지 약했다.

## 4. 아직 유보하는 것
- 5월/11월 카드 우선순위 조정
- season별 구조 재설계
- overview 카드 대규모 재배치
- 자동 추론 기반 priority 변경

## 5. 다음 단계 후보
- 저위험 자동 추론 강화 v1에서 먼저 볼 입력 신호
  - `receipt_pending_count`
  - `reinforcement_pending_count`
  - `tax_accuracy_gap`
  - `package_ready`
  - `receipt_pending_expense_krw`
- 다음 단계에서도 지켜야 할 원칙
  - 수동 micro-priority를 기본값으로 유지
  - 자동 추론은 priority를 뒤집지 않고 소폭 가중치만 추가
  - explainability 메타(`priority_base`, `priority_effective`, `priority_adjustment_reason`)를 같이 남길 것

## 6. 자동 추론 강화 v1에서 쓰지 않는 신호
- 거래 패턴만으로 3.3% 여부 추정
- 거래 패턴만으로 면세/과세유형 추정
- 거래 패턴만으로 기납부세액 수준 추정

이 신호들은 지금 단계에서 설명 가능하지 않고, 잘못 맞으면 우선순위를 공격적으로 흔들 수 있으므로 금지한다.

## 7. 현재 단계 성격
- 이번 단계는 **수동 priority 미세조정 + same-screen friction 완화 단계**다.
- 자동 추론 로직은 아직 넣지 않는다.

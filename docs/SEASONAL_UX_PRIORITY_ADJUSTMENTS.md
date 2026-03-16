# Seasonal UX Priority Adjustments

## A. 현재 데이터 상태
- 이번 판단의 근거 데이터:
  - `shown = 22`
  - `clicked = 1`
  - `landed = 1`
  - `completed = 0`
  - `has_enough_data = false`
- 현재 표본은 방향성 있는 재정렬을 하기엔 부족하다.
- 이번 단계는 **수동 priority 미세조정 + CTA/카피 조정만** 허용한다.

## B. 이번 단계 분류
### `adjust_now`
- `offseason_accuracy@tax_buffer`
  - 이유: shown이 반복됐지만 clicked가 0건이라 CTA/카피 마찰이 뚜렷하다.
- `offseason_monthly_review@review`
  - 이유: clicked/landed는 있었지만 completed가 0건이라 same-screen 행동 유도가 약하다.
- `offseason_package_ready@package`
  - 이유: same-screen CTA가 generic해서 “지금 뭘 보면 되는지”가 약하다.

### `observe_more`
- `offseason_accuracy@overview`
- `offseason_package_ready@overview`
- `offseason_package_ready@package`
  - 이유: shown은 있으나 클릭/완료 표본이 너무 적다.
  - 이번 단계에서는 카피/anchor만 다루고 priority 재배치는 유보한다.

### `do_not_touch_yet`
- `may_*` 카드 전체
- `november_*` 카드 전체
- `season_focus` 판정 로직
  - 이유: 현재 관측 데이터가 `off_season`뿐이라 시즌별 우선순위 조정 근거가 없다.

## C. 이번 단계 실제 조정
### 1. CTA / 카피 조정
- `offseason_accuracy`
  - CTA:
    - 이전: `정확도 올리기`
    - 이전 단계 변경 후 유지: `3.3%·빠진 세금 확인하기`
  - summary:
    - 이전: 추상적인 정확도 개선 안내
    - 현재: `3.3%와 이미 빠진 세금만 확인해 두면 다음 시즌 숫자가 덜 흔들려요.`
- `offseason_monthly_review@review`
  - CTA:
    - 이전: `대기 항목 바로 보기`
    - 변경: `반영 대기 항목부터 정리하기`
  - anchor:
    - `#review-worklist`
  - summary:
    - 변경: `반영 대기 {n}건부터 열어 보고, follow-up이나 보강이 필요한 항목부터 처리하면 돼요.`
- `offseason_accuracy@tax_buffer` same-screen context
  - CTA:
    - 이전: `아래 부족분 보기`
    - 변경: `예상세금·보관액 바로 보기`
  - anchor:
    - `#tax-buffer-kpis`
- `offseason_package_ready@package` same-screen context
  - CTA:
    - 이전: `아래 점검표 보기`
    - 변경: `세무사 보내기 전 마지막 점검 보기`
  - anchor:
    - `#package-readiness`

### 2. priority 숫자 미세조정
- `offseason_monthly_review`
  - 이전 priority: `1`
  - 변경 priority: `0`
  - 이유:
    - 현재 데이터에서 유일하게 clicked/landed가 발생한 카드다.
    - 오프시즌 기본 행동을 “이번 달 정리 먼저”로 고정하되, 카드 구조를 뒤집지 않는 범위의 미세 상향만 적용했다.
- `offseason_accuracy`
  - 유지: `2`
- `offseason_package_ready`
  - 유지: `3`

## D. 아직 유보한 카드
- overview 오프시즌 카드 전체 재정렬
- 5월/11월 카드 priority 변경
- package 카드 대폭 카피 수정
- season별 source_screen 우선순위 분기

## E. 남은 리스크
- `completed = 0`이라 완료 마찰 해석은 아직 가설 수준이다.
- `review` 카드의 priority를 1단계 올렸지만, 더 큰 재배치는 아직 근거가 없다.
- `tax_buffer`와 `package`는 same-screen CTA를 다듬었을 뿐, 실제 클릭 개선은 다음 데이터 적재 후 다시 봐야 한다.

## F. 다음 단계 입력 포인트
- 저위험 자동 추론 강화 v1에서는 아래 신호만 사용한다.
  - `receipt_pending_count`
  - `reinforcement_pending_count`
  - `tax_accuracy_gap`
  - `package_ready`
- 단, 이 수동 미세조정 결과를 기본 우선순위로 삼고, 자동 추론은 그 위에서 소폭 보정만 해야 한다.

## G. 최종 판정
- **수동 priority 미세조정 완료**
- 다음 단계는 `off_season` 기준 저위험 자동 추론 강화 v1로 넘어가도 된다.

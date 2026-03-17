# Seasonal UX Event Schema

## 목적
시즌 카드가 얼마나 보이고(shown), 눌리고(clicked), 목표 화면에 도착하며(landed), 실제 행동 완료로 이어지는지(completed) 공통 스키마로 기록한다.

## 이벤트 이름
- `seasonal_card_shown`
- `seasonal_card_clicked`
- `seasonal_card_landed`
- `seasonal_card_completed`

## 공통 필드
- `season_focus`
- `card_type`
- `cta_target`
- `source_screen`
- `priority`
- `completion_state_before`
- `completion_state_after`
- `month_key`
- `route`
- `extra`

## firing 시점
- `seasonal_card_shown`
  - 시즌 카드가 실제로 렌더된 요청에서 카드별 1회 기록
  - overview는 허브 카드 1~3개 각각 기록
  - review / tax_buffer / package는 시즌 컨텍스트 카드 1개만 기록
- `seasonal_card_clicked`
  - 사용자가 시즌 카드 CTA를 눌러 `web_overview.seasonal_card_click` 추적 라우트를 통과할 때 기록
- `seasonal_card_landed`
  - 클릭 후 target 화면이 실제로 열렸을 때 기록
  - `seasonal_card_land=1` query와 pending session context를 함께 사용
- `seasonal_card_completed`
  - target 화면 도착이 아니라 실제 행동 완료 지점에서만 기록

## 중복 방지 원칙
- 같은 요청에서 같은 카드 `shown`은 1회만 기록
- `clicked`는 CTA 추적 라우트 통과 시점 1회만 기록
- `landed`는 pending session context를 소비하면서 1회만 기록
- `completed`는 active session context가 남아 있을 때만 기록하고, 완료 후 즉시 clear
- `clicked = completed`로 해석하지 않는다

## 카드별 완료 정의
- `may_accuracy`
  - 완료 기준: `tax_profile`, `tax_income_classification_quick_save`, `tax_basic_step_save` 중 하나의 저장 성공
- `may_receipt_cleanup`
  - 완료 기준: review follow-up 저장 또는 reinforcement 저장 성공
- `may_package_ready`
  - 완료 기준: package ZIP 생성/다운로드 성공
- `november_halfyear_check`
  - accuracy gap이 있어 profile 입력으로 연결될 때만 completion action 부여
  - 이미 done 상태에서 tax_buffer 보기만 하는 경우 shown/clicked/landed만 기록
- `november_receipt_reinforce`
  - 완료 기준: review follow-up 저장 또는 reinforcement 저장 성공
- `november_buffer_check`
  - 완료 기준: `tax_buffer_adjust` 저장 성공
- `offseason_monthly_review`
  - 완료 기준: review follow-up 저장 또는 reinforcement 저장 성공
- `offseason_accuracy`
  - accuracy gap이 있어 profile 입력으로 연결될 때만 completion action 부여
- `offseason_package_ready`
  - 완료 기준: package ZIP 생성/다운로드 성공

## 해석 예시
- `shown` 높고 `clicked` 낮음
  - 카드 카피 또는 우선순위 문제 가능
- `clicked` 높고 `landed` 낮음
  - 링크/리다이렉트/권한 문제 가능
- `landed` 높고 `completed` 낮음
  - 연결 화면 입력 마찰이 높거나 완료 정의가 너무 빡빡할 가능성

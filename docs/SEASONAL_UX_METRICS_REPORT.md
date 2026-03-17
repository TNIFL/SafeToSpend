# Seasonal UX Metrics Report

## A. 데이터 적재 상태
- 집계 기준 파일: `reports/seasonal_ux_metrics_audit_latest.json`
- 해석 기준 파일: `reports/seasonal_ux_metrics_interpretation.json`
- 현재 스냅샷
  - `seasonal_ux_rows = 24`
  - `shown = 22`
  - `clicked = 1`
  - `landed = 1`
  - `completed = 0`
  - 관측 시즌: `off_season`만 존재
- 결론
  - 계측은 정상 동작하고 있음
  - 다만 priority 재배치를 판단하기엔 표본이 부족함

## B. 이벤트 스키마
출처: [SEASONAL_UX_EVENT_SCHEMA.md](/Users/tnifl/Desktop/SafeToSpend/docs/SEASONAL_UX_EVENT_SCHEMA.md)

핵심 이벤트:
- `seasonal_card_shown`
- `seasonal_card_clicked`
- `seasonal_card_landed`
- `seasonal_card_completed`

핵심 공통 필드:
- `season_focus`
- `card_type`
- `cta_target`
- `source_screen`
- `priority`
- `completion_state_before`
- `completion_state_after`
- `month_key`

## C. 카드별 퍼널 해석
### 전체
- CTR: `4.55%`
- landed rate: `100%`
- completion rate from click: `0%`

### by_card 요약
- `offseason_monthly_review@review`
  - `shown=4`, `clicked=1`, `landed=1`, `completed=0`
  - 의미: review 화면 시즌 컨텍스트는 최소한의 클릭 유도는 있었지만, 완료 행동으로 이어질 만큼 강하지는 않았음
- `offseason_accuracy@tax_buffer`
  - `shown=5`, `clicked=0`
  - 의미: 숫자 정확도 카드는 보였지만, 현재 CTA가 추상적이거나 이미 같은 화면에 있어 행동 유도가 약했을 가능성
- `offseason_package_ready`
  - `shown=6`, `clicked=0`
  - 의미: 아직 표본이 적어서 우선순위 하향 결론은 유보

### by_screen 요약
- `review`
  - 현재까지 유일한 클릭/도착이 발생한 화면
- `overview`, `tax_buffer`, `package`
  - shown은 있으나 클릭이 없어서 더 많은 데이터가 필요함

## D. 수동 우선순위 / 카피 / CTA 조정 결과
출처: [SEASONAL_UX_PRIORITY_ADJUSTMENTS.md](/Users/tnifl/Desktop/SafeToSpend/docs/SEASONAL_UX_PRIORITY_ADJUSTMENTS.md)

적용한 조정:
- priority 숫자 변경 없음
- `offseason_accuracy` CTA 구체화
  - `정확도 올리기` -> `3.3%·빠진 세금 확인하기`
- same-screen CTA friction 완화
  - review -> `대기 항목 바로 보기`
  - tax_buffer -> `아래 부족분 보기`
  - package -> `아래 점검표 보기`
- self-link CTA는 해당 화면의 실제 작업 영역 anchor로 연결

## E. 완료율 낮은 카드 마찰 지점
- `offseason_monthly_review@review`
  - 클릭 후 같은 review 화면으로 다시 들어오는 구조라, 무엇을 해야 하는지가 약했을 가능성
  - 조치: generic CTA 대신 작업 영역 anchor + 더 구체적 라벨
- `offseason_accuracy`
  - 카드가 보이지만 `정확도 올리기`는 너무 추상적이었을 수 있음
  - 조치: 실제 입력 내용을 드러내는 CTA로 수정

## F. 남은 리스크
- 5월/11월 데이터가 아직 없어 시즌별 차이를 판단할 수 없음
- completed가 아직 0건이라 completion friction은 가설 수준이 강함
- browser / device별 CTR 차이는 아직 별도 QA가 없음
- 현재는 off-season-only 데이터라 5월/11월 우선순위 변경은 보류함

## G. 다음 단계 연결 포인트
- 다음 자동 추론 강화 전 우선할 것
  - review pending 많은 사용자에게 review 카드 우선 배치 신호가 실제로 쌓이는지 확인
  - accuracy 카드 CTA 변경 후 클릭률이 올라가는지 확인
- 데이터가 더 쌓이면 진행할 것
  - season_focus별 priority 재배치
  - overview와 detail screen의 카드 순서 분리
  - 5월/11월 별도 CTA 실험

## H. 최종 판정
- 현재 단계는 **데이터 해석 + 저위험 수동 조정 단계**다.
- priority 대규모 재배치는 아직 하지 않았다.
- 다음 단계는 운영 데이터가 조금 더 쌓인 뒤 자동 추론 강화로 넘길 수 있다.

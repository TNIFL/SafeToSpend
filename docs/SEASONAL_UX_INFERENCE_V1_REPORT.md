# Seasonal UX Inference V1 Report

## A. 문제 요약
- 시즌 카드는 이미 상태 기반으로 나오고 있었지만, 카드 간 우선순위는 대부분 정적이었다.
- 현재 운영 표본이 적기 때문에 공격적인 개인화는 금지하고, 이미 있는 안전한 상태 신호만으로 우선순위를 조금 더 정교하게 만드는 것이 이번 단계 범위다.

## B. 허용 신호 / 금지 신호
### 허용 신호
- `receipt_pending_count`
- `reinforcement_pending_count`
- `tax_accuracy_gap`
- `package_ready`
- `receipt_pending_expense_krw`

이 신호들은 모두 이미 계산된 상태값 또는 입력 누락 여부라서, 사용자가 입력하지 않은 세무 상태를 새로 추정하지 않는다.

### 금지 신호
- 거래 패턴만 보고 3.3% 여부 확정
- 거래 패턴만 보고 면세/과세유형 확정
- 거래 패턴만 보고 기납부세액 수준 확정

이 신호들은 현재 단계에서 설명 불가하고, 잘못 맞으면 공격적 개인화로 이어질 수 있다.

## C. priority 자동 조정 규칙
- 기본 원칙
  - 기존 수동 priority를 `priority_base`로 유지
  - 자동 추론은 최대 한 단계만 올린다.
  - 신호가 없으면 그대로 둔다.

### review 계열 카드
- 대상
  - `may_receipt_cleanup`
  - `november_receipt_reinforce`
  - `offseason_monthly_review`
- 사용 신호
  - `receipt_pending_count`
  - `reinforcement_pending_count`
  - `receipt_pending_expense_krw`
- 조정 방식
  - backlog가 있으면 `priority_effective = priority_base - 1`

### tax accuracy 계열 카드
- 대상
  - `may_accuracy`
  - `november_halfyear_check`
  - `offseason_accuracy`
- 사용 신호
  - `tax_accuracy_gap`
- 조정 방식
  - gap이 있으면 `priority_effective = priority_base - 1`

### package 계열 카드
- 대상
  - `may_package_ready`
  - `november_package_ready`
  - `offseason_package_ready`
- 사용 신호
  - `package_ready`
  - `receipt_pending_count`
  - `reinforcement_pending_count`
- 조정 방식
  - package가 ready이고 backlog가 없으면 `priority_effective = priority_base - 1`

## D. explainability 방식
- 카드에 아래 메타를 붙인다.
  - `priority_base`
  - `priority_effective`
  - `priority_adjustment_score`
  - `priority_adjustment_reason`
  - `priority_adjustment_reasons`
- same-screen context에도 핵심 explainability 메타를 유지한다.
- 계측에는 `priority_effective`가 들어가므로 실제 렌더 우선순위와 분석값이 맞는다.

## E. 예시
### 올라가는 케이스
- `may_receipt_cleanup`
  - pending 4건 + reinforcement 1건 + pending_expense 존재
  - 결과: review 카드가 accuracy 카드와 같은 우선순위대까지 올라간다.

### 유지되는 케이스
- `offseason_accuracy`
  - `tax_accuracy_gap = false`
  - 결과: 기본 priority 유지

### 제한적으로만 올라가는 케이스
- `offseason_package_ready`
  - package ready + pending 0
  - 결과: 한 단계만 올라감
  - card type / state 구조는 유지

## F. 테스트 결과
- `tests.test_seasonal_ux_inference_v1`
  - 허용/금지 신호 선언
  - review priority boost
  - accuracy priority boost
  - package priority boost
  - no-signal 유지
  - forbidden signal 무시
  - explainability 메타 유지
- `tests.test_seasonal_ux_render`
  - render wiring과 explainability 필드 존재 확인

## G. 남은 리스크
- 실제 운영 데이터에 따라 boost 강도는 다시 조정할 수 있다.
- 11월 시즌 데이터가 아직 충분하지 않다.
- 브라우저별 체감 영향은 별도 QA가 필요하다.

## H. 다음 단계 연결 포인트
- 다음 단계에서는 아래를 검토할 수 있다.
  - season/card/source_screen별 가중치 분리
  - 운영 CTR/completion 데이터 기반 미세 튜닝
  - overview/detail screen 분리 우선순위

## I. 최종 판정
- **저위험 자동 추론 강화 v1 완료**
- 현재 단계는 low-risk / explainable / reversible 범위를 지켰다.

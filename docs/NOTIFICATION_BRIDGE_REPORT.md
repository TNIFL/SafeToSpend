# Notification Bridge Report

## A. 문제 요약
- 기존 구조는 우측 상단 토스트와 알림센터가 분리되어 있었다.
- 사용자는 토스트를 “알림”으로 인식하지만, 중요한 토스트가 알림센터에 남지 않아 재확인이 어려웠다.
- 이번 작업의 목적은 토스트 UX를 유지하면서, 중요한 메시지만 알림센터에 적재되도록 공용 브리지를 추가하는 것이다.

## B. 기존 분리 구조
### 1) 토스트 경로
- `templates/nhis.html`
  - `showToast(...)`로 페이지 로컬 토스트 스택(`nhis-toast-stack`)에만 표시.
- `templates/calendar/tax_buffer.html`
  - `toast` 쿼리 파라미터 기반 상단 토스트 렌더.
- `templates/calendar/review.html`
  - `toast=receipt_applied` 기반 상단 토스트 렌더.

### 2) 알림센터 경로
- `templates/base.html`
  - `pushNotice(...)`로 `localStorage(sts_notice_items_v1)` 적재.
  - 영수증 queue polling 결과(done/failed)만 알림센터에 누적.

### 3) 핵심 갭
- NHIS/세금보관함/정리하기 토스트는 알림센터 브리지 없이 소멸.
- 결과적으로 “즉시 보임(토스트)”과 “나중에 재확인(센터)”가 연결되지 않았다.

## C. 공용 브리지 설계
- 공용 API:
  - `window.SafeToSpendNotify.notify(message, level, options)`
- 지원 모드:
  - `toast_only`
  - `toast_and_center`
  - `center_only`
- 주요 옵션:
  - `persist_to_center`
  - `dedupe_key`
  - `title`
  - `detail`
  - `source`
  - `suppress_toast`

### 알림 레벨 정책
- `success`, `info`, `warning(warn)`, `error` 레벨을 허용.
- 알림센터 필터는 기존 UI를 유지하기 위해 `success/failure` 2축으로 유지:
  - `warning/error -> failure`
  - `success/info -> success`

### 중복 방지 정책
- 센터 적재:
  - `dedupe_key`가 있으면 기존처럼 key 기준 중복 차단.
  - `dedupe_key`가 없으면 동일 메시지/상세/kind의 단기 중복(15초) 차단.
- 인라인 토스트:
  - 동일 시그니처 단기 중복(2.5초) 차단.

## D. 브리지 적용 범위
### 1) toast_and_center (중요 토스트)
- `templates/nhis.html`
  - 저장 완료/저장 확인 필요/flash 기반 주요 처리 결과를 브리지 적재.

### 2) center_only (이미 페이지 상단 토스트가 있는 경우)
- `templates/calendar/tax_buffer.html`
  - 보관/납부 기록 완료 토스트를 알림센터에도 적재.
- `templates/calendar/review.html`
  - 영수증 반영 완료 토스트를 알림센터에도 적재.

### 3) 기존 center_only 유지
- `templates/base.html`
  - 영수증 queue polling(`pushNotice`)은 기존처럼 센터 적재 유지.

## E. 테스트 결과
- 테스트 파일
  - `tests/test_notification_bridge.py`
  - `tests/test_notification_center_render.py`
- 실행 명령
  - `.venv/bin/python -m unittest tests.test_notification_bridge tests.test_notification_center_render`
- 확인 포인트
  - 공용 `notify` API 노출 여부
  - `toast_only / toast_and_center / center_only` 분기
  - NHIS/세금보관함/정리하기 브리지 호출 여부
  - 알림센터 title/level/source 메타 렌더 유지 여부
- 실행 결과
  - `Ran 7 tests in 0.003s`
  - `OK`

## F. 남은 toast_only 항목
- NHIS 페이지의 보조 힌트성 토스트:
  - 입력 중 안내(“저장 중…”, “현재 입력 유지” 등)
  - 실험/시뮬레이션 안내(금융소득 경계 체크 결과 등)
  - 자동 모드 전환 안내
- 원칙:
  - 나중에 다시 볼 필요가 낮은 힌트성 메시지는 `toast_only` 유지.

## G. 남은 리스크
- 모든 페이지 로컬 토스트를 일괄 변환하지는 않았다.
  - 이번 범위는 “중요 토스트 우선 브리지”에 한정.
- 알림센터 필터가 현재 `성공/실패` 2축이라 `info/warning` 세분 필터는 미제공.
- 동일 메시지 반복 작업에서 `dedupe_key` 설계가 과도하면 센터 누락 체감이 있을 수 있어, 운영 로그를 보고 조정이 필요하다.

## H. 최종 판정
- **대부분 해소됨**
- 근거:
  - 공용 `notify` 브리지가 도입되어 중요 토스트를 토스트/센터로 동시 처리 가능.
  - NHIS/세금보관함/정리하기의 핵심 토스트가 센터에도 남도록 연결됨.
  - 회귀 테스트로 분기/렌더/연결 경로를 고정함.
- 남은 과제:
  - 아직 `toast_only`로 남겨둔 비핵심 토스트 범위는 운영 피드백 기반으로 조정 가능.

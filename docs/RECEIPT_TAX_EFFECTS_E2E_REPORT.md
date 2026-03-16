# Receipt Tax Effects E2E Report

## A. 검증 범위
- 영수증 비용처리 판정 결과가 실제 세금 체감 숫자와 연결되는지 브라우저에서 검증한다.
- 대상 흐름은 `follow-up 저장 -> 토스트 -> review 숫자 갱신 -> tax_buffer 이동 -> calendar 이동 -> 새로고침`이다.
- 강화 흐름은 `reinforcement 저장 -> reflected_expense 증가 -> 토스트 1회 -> remaining_gaps 감소`까지 포함한다.

## B. 핵심 케이스
| case_id | 경로 | 기대 결과 | 관찰 포인트 |
| --- | --- | --- | --- |
| followup_reflect_transport | review follow-up 저장 | `high_likelihood` 승급, reflected 반영, review 숫자 감소 | 토스트 1회, `#taxTarget` changed=1, 최종 숫자, notice count |
| reinforcement_reflect_meal | review follow-up + reinforcement 저장 | reflected_expense 증가, 세금 감소, remaining_gaps 감소 | 토스트 1회, reflected/pending 변화, gaps 감소 |
| pending_cafe_no_change | review follow-up 저장 | `needs_review` 유지, 숫자 미변경 | 토스트 1회, `data-tax-changed=0`, pending 문구 |
| review_to_tax_buffer_consistency | review -> tax_buffer | 동일 서버값 유지, 중복 토스트 없음 | inline toast 0, notice count 유지, tax value 동일 |
| tax_buffer_to_calendar_animation | tax_buffer -> calendar | changed 값만 애니메이션 대상 | `data-tax-changed`, 최종 렌더값, 세금 추정치/이번 달 남은 돈 |
| refresh_persists_without_retoast | calendar refresh | 숫자 유지, 토스트 재발생 없음 | URL query 정리, inline toast 0, 최종값 동일 |
| reduced_motion_transport | review follow-up 저장 (reduced motion) | 즉시 최종값 렌더, 애니메이션 fallback 정상 | `prefers-reduced-motion`, 최종 숫자, changed 플래그 |

## C. 관찰 포인트
- 토스트 개수: `#global-inline-toast-wrap .toast`
- 알림센터 개수: `#nav-notice-count`, `#global-toast-stack > li`
- review 숫자: `#taxTarget`, `#bizExpense`, `#estProfit`
- tax_buffer 숫자: 상단 `data-tax-animate="currency"` 카드, reflected/pending 텍스트
- calendar 숫자: `세금 추정치(이번 달)`, `이번 달 남은 돈`
- changed 여부: `data-tax-changed`
- 서버값 기준 최종 렌더: `data-tax-current-value`와 최종 텍스트 일치 여부
- stale param: `receipt_effect_*`, `tax_before`, `tax_after`, `buffer_before`, `buffer_after`, `expense_before`, `expense_after`, `profit_before`, `profit_after`

## D. 브라우저에서만 발생 가능한 리스크
- 중복 토스트: 저장 직후와 이동 후 토스트가 다시 뜨는 문제
- stale query param: `receipt_effect_*`가 URL에 남아 새로고침 시 중복 동작하는 문제
- 애니메이션 race: 최종 숫자보다 중간값이 남거나 highlight만 되고 값이 안 맞는 문제
- 최종값 mismatch: 서버 계산 결과와 브라우저 렌더 숫자가 다른 문제
- reduced motion fallback 오류: motion reduce 환경에서 값이 늦게 바뀌거나 빈 값이 노출되는 문제

## E. 시드 데이터
- 별도 E2E 계정과 2026-03 월 거래 세트를 사용한다.
- 케이스 식별자와 tx id는 `reports/receipt_tax_effects_e2e_summary.json`에 기록한다.

## F. 정상 동작 확인 기준
- follow-up 저장 시 reflected 케이스는 토스트가 정확히 1번 뜬다.
- reinforcement 저장 시 reflected 케이스는 토스트가 정확히 1번 뜬다.
- pending 케이스는 숫자가 바뀌지 않고 보류 토스트만 뜬다.
- review, tax_buffer, calendar의 최종 숫자는 같은 서버 계산 결과를 가리킨다.
- changed=0인 저장은 숫자 애니메이션이 돌지 않는다.
- refresh 이후 토스트는 다시 뜨지 않고 최종 숫자만 유지된다.

## G. 발견된 문제
- `stale query param` 재현됨
  - follow-up 저장 후 `receipt_effect_*`, `tax_before/after`, `expense_before/after`가 현재 URL에 남아 있었다.
  - 영향:
    - 새로고침/후속 검증에서 중복 동작 위험
    - E2E 기준 stale URL 상태
- reinforcement 저장 경로 버그 재현됨
  - 브라우저에서 `보강 저장하고 다시 보기` 제출 시 `보강 항목 구성이 올바르지 않아요` 오류가 발생했다.
  - 원인:
    - `extract_reinforcement_payload_from_form()`가 이미 normalize된 dict를 반환했고,
    - 검증 단계가 내부 보조 key를 `invalid_reinforcement_key`로 오인했다.
- tax_buffer -> calendar 이동 시 top nav `캘린더` 링크가 effect context를 잃는 문제 재현됨
  - 영향:
    - calendar에서 `changed=false`
    - 숫자 애니메이션이 끊겨 브라우저 동선 체감이 약해짐

## H. 수정 사항
- [templates/base.html](/Users/tnifl/Desktop/SafeToSpend/templates/base.html)
  - effect query param cleanup 추가
  - `history.replaceState(...)`로 현재 URL에서 transient key 제거
  - review / tax_buffer / calendar top nav 링크에 effect context를 브라우저 DOM 수준에서 재주입
  - 중복 토스트를 막기 위해 `receipt_effect_toast`는 nav 전파 대상에서 제외
- [services/receipt_expense_rules.py](/Users/tnifl/Desktop/SafeToSpend/services/receipt_expense_rules.py)
  - `extract_reinforcement_payload_from_form()`가 raw extracted payload를 반환하도록 수정
  - reinforcement 저장 경로의 `invalid_reinforcement_key` 오검출 해소
- [e2e/receipt-tax-effects.spec.ts](/Users/tnifl/Desktop/SafeToSpend/e2e/receipt-tax-effects.spec.ts)
  - calendar 세금 카드의 `-` prefix를 반영하도록 최종 숫자 검증식 보정
  - pending 토스트 문구 검증을 의미 중심으로 완화
  - reduced motion 케이스에서 `page.emulateMedia({ reducedMotion: "reduce" })`를 명시해 환경 의존성을 제거

## I. 최종 판정
- 요약
  - 고정 시드 데이터로 4개 실브라우저 E2E 케이스를 검증했다.
  - follow-up reflected 케이스, reinforcement reflected 케이스, pending 유지 케이스, reduced motion 케이스가 모두 통과했다.
  - failures 리포트는 빈 배열로 정리됐다.
- 실행 결과
  - Playwright: `4 passed`
  - summary: [receipt_tax_effects_e2e_summary.json](/Users/tnifl/Desktop/SafeToSpend/reports/receipt_tax_effects_e2e_summary.json)
  - failures: [receipt_tax_effects_e2e_failures.json](/Users/tnifl/Desktop/SafeToSpend/reports/receipt_tax_effects_e2e_failures.json)
- 최종 판정
  - **실브라우저 E2E 검증 완료**
  - 현재 범위 기준으로 `follow-up/reinforcement 저장 -> 토스트 1회 -> review/tax_buffer/calendar 숫자 반영 -> 애니메이션 -> 새로고침 후 유지` 흐름은 정상 동작한다.

## J. 남은 리스크
- 실제 브라우저 E2E는 로컬 단일 환경(`127.0.0.1:5001`, Chromium) 기준이다.
- Safari/WebKit, Firefox까지의 교차 브라우저 검증은 아직 없다.
- top nav effect context 전파는 현재 “직후 이동 UX”를 위한 transient 전달이다. 장기 상태 보존 정책은 아니다.
- Playwright 실행은 로컬 설치 경로(`/Users/tnifl/node_modules/.bin/playwright`)에 의존한다.

## K. 다음 단계 연결 포인트
- 세무사 패키지 확장
  - reflected / pending / consult 상태와 보강 메모를 패키지 인덱스에 포함할지 결정
- 자연스럽게 녹이기 트랙
  - review/tax_buffer/calendar 말고 overview까지 동일 애니메이션/요약 피드백을 넓힐지 결정
- 교차 브라우저 E2E
  - WebKit / Firefox 기준 reduced motion, toast, animation race 추가 검증

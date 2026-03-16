# Billing Subscription Flow Redesign Report

## 1) 실제 수정 파일 목록
- `services/billing/service.py`
- `routes/web/billing.py`
- `templates/billing/register.html`
- `templates/billing/register_start.html`
- `templates/billing/success.html`
- `templates/billing/processing.html`
- `tests/test_billing_service_layer.py`
- `tests/test_billing_checkout_pipeline.py`
- `tests/test_billing_checkout_routes.py`
- `tests/test_billing_register_callback_routes.py`
- `docs/DEV_TESTING.md`

## 2) 루프 원인과 제거 방법
- 원인 A: 재사용 intent(`ready_for_charge`)가 결제수단 재판정 과정에서 `registration_required`로 다시 강등될 수 있었음.
- 원인 B: confirm 경로가 intent 결합 결제수단보다 전역 active 조회에 의존해, 등록 직후에도 “결제수단 없음” 분기로 돌아갈 수 있었음.
- 제거:
  - `resolve_checkout_billing_method`를 confirm 경로에 고정 적용.
  - `start_checkout_intent`에서 `ready_for_charge` 보호 및 bound method 우선 처리.
  - `resume_checkout_intent_after_registration`에서 `ready_for_charge`/`charge_started` 재진입 멱등 처리 강화.

## 3) registration -> processing -> confirm 자동 연결 구조
- `checkout/start`에서 결제수단 미보유 시 곧바로 등록 런치 템플릿을 렌더.
- 등록 성공 콜백에서 관련 intent를 `ready_for_charge`로 복구.
- 로그인 세션이 살아 있으면 `register/success`에서 자동으로 `checkout/processing`으로 이동.
- `processing` 페이지는 얇은 auto-submit 폼으로 confirm POST를 1회 호출(실패 시 fallback 버튼 제공).
- GET 콜백에서 직접 charge 실행은 금지 유지.

## 4) return_to + 토스트 구조
- `checkout/start`의 sanitize된 `next`를 intent pricing snapshot의 `return_to`로 저장.
- 결제 결과 페이지(`payment/success`, `payment/fail`)는 연결된 intent의 `return_to`로 복귀.
- 외부 URL/`/dashboard/billing/*` 경로는 복귀 대상에서 차단하고 `/pricing`으로 fallback.
- 결과 알림은 `_billing_result_notice_once` 세션 키로 1회성 dedupe.

## 5) CTA 통일 상태
- `pricing/mypage/bank/package` 주요 결제 CTA는 기존과 동일하게 `POST /dashboard/billing/checkout/start` 단일 진입점 유지.
- 결제수단 미보유 시 주력 흐름에서 등록 런치로 바로 이어지고, 등록 성공 후 자동 confirm으로 연결.

## 6) 별도 결제수단 페이지 역할 정리
- `templates/billing/register.html` 문구를 “보조 관리(변경/재등록)” 용도로 재정리.
- 주력 구독 시작은 요금제/마이페이지/은행/패키지 CTA에서 시작하도록 안내.

## 7) 회귀 테스트 결과
- 실행 명령:
  - `.venv/bin/python -m unittest tests.test_billing_service_layer`
  - `.venv/bin/python -m unittest tests.test_billing_checkout_pipeline`
  - `.venv/bin/python -m unittest tests.test_billing_checkout_routes`
  - `.venv/bin/python -m unittest tests.test_billing_register_callback_routes`
  - `.venv/bin/python -m unittest discover -s tests -p 'test_billing_*.py'`
- 결과:
  - billing 전용 테스트 110개 통과, 1개 skip.
  - 신규/보강 포인트:
    - intent 강등 방지
    - intent bound method 우선 사용
    - register success -> processing 자동 연결
    - duplicate confirm no-op
    - return_to sanitize redirect

## 8) 아직 남은 항목
- 스테이징 실결제 실측(E2E)
- 프록시/APM/CSP 실측
- 최종 실오픈 GO/NO-GO 판정

## 비고
- 이번 단계는 구독 UX 루프 제거 및 자동 연결 구현 단계다.
- 본 문서 범위만으로 “실결제 오픈 가능” 판정은 하지 않는다.

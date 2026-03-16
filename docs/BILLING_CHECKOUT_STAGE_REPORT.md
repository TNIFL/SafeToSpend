# Billing Checkout Stage Report

작성일: 2026-03-10  
범위: checkout 단계(최초 구독/업그레이드/추가 계좌 일할 결제) 구현 결과 정리

## 1) 실제 수정 파일 목록
- `routes/web/billing.py`
- `routes/web/main.py`
- `services/billing/reconcile.py`
- `services/billing/projector.py`
- `templates/billing/payment_success.html`
- `templates/billing/payment_fail.html`
- `templates/pricing.html`
- `templates/mypage.html`
- `templates/bank/index.html`
- `templates/package/index.html`
- `tests/test_billing_checkout_routes.py`
- `tests/test_billing_checkout_pipeline.py`
- `tests/test_billing_reconcile_service.py`
- `tests/test_billing_projector.py`
- `docs/DEV_TESTING.md`

## 2) checkout intent 도입 여부
- `CheckoutIntent` 도메인(`billing_checkout_intents`)을 기준으로 시작/재개/확정 흐름 사용 중.
- 상태 분기: `registration_required` / `ready_for_charge` / `charge_started` / `completed` / `failed` 등.
- `resume_token` 기반으로 등록 후 결제 재개 경로를 유지.

## 3) registration → charge resume 흐름 구현 상태
- 구현됨.
- 등록 성공 콜백(`GET /dashboard/billing/register/success`)은 billing method 등록 + intent 복구(`ready_for_charge`)까지만 수행.
- 등록 성공 GET에서 바로 charge를 실행하지 않음(정책 준수).
- 실제 charge는 별도 POST confirm 경로에서만 실행.

## 4) charge confirm 구현 상태
- 구현됨.
- `POST /dashboard/billing/checkout/confirm` 추가.
- `confirm_checkout_intent_charge()`가
  - intent 잠금/검증
  - billing method 검증
  - payment_attempt 생성
  - 토스 billingKey 결제 요청
  - reconcile 호출
  순으로 처리.
- users entitlement 직접 갱신은 없음(reconcile/projector 경유).

## 5) payment success/fail UX 구현 상태
- 구현됨.
- `GET /dashboard/billing/payment/success`
- `GET /dashboard/billing/payment/fail`
- 두 경로 모두 읽기 UX 전용이며, 새로고침 side effect 없이 상태 조회/안내만 수행.

## 6) CTA 연결 상태
- 구현됨(POST form + CSRF).
- 연결 페이지:
  - `pricing`(베이직 시작/프로 시작/프로 업그레이드/추가 계좌 구매)
  - `mypage`(플랜별 시작/업그레이드/추가 계좌)
  - `bank`(free 업그레이드, basic→pro, add-on 구매)
  - `package`(free 업그레이드, basic→pro, add-on 구매)

## 7) 통합 테스트/검증 결과
- 실행 명령:
  - `.venv/bin/python -m unittest tests.test_billing_checkout_routes tests.test_billing_reconcile_service tests.test_billing_projector tests.test_billing_checkout_pipeline`
  - `.venv/bin/python -m unittest tests.test_billing_service_layer tests.test_billing_register_callback_routes`
- 결과:
  - 총 53개 테스트 PASS
  - 신규 추가:
    - `test_billing_checkout_pipeline.py` (initial/upgrade/addon charge→reconcile 경로)
    - `test_billing_checkout_routes.py` 스텁 보강(템플릿 렌더 경로 안정화)
    - reconcile/projector 멱등성 보강 테스트 추가

## 8) 아직 남은 항목
- 정기청구 워커(주기 실행/청구 캘린더 오케스트레이션)
- 3일 grace/retry 자동화 오케스트레이션 완성
- 스테이징 실결제 E2E(실 PG/프록시/APM/CSP 실측)
- 실결제 오픈 전 최종 GO/NO-GO 재판정

## 결론
- checkout 단계 구현은 완료되었고, registration→confirm→reconcile→projector 연결이 동작한다.
- 다만 이 문서는 checkout 단계 완료 보고서이며, **실결제 오픈 가능 판정 문서가 아니다**.

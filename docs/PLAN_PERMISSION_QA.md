# 플랜 권한 최종 QA 보고서 (planimpl03 후속, 최신 점검 반영)

최초 작성일: 2026-03-09  
최신 점검일: 2026-03-12

## 0) 최신 점검 요약 (2026-03-12)
- 재실행 테스트:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_plan_entitlements tests.test_plan_service_guards`
  - 결과: `Ran 9 tests ... OK`
- DB 의존 스모크:
  - `PYTHONPATH=. .venv/bin/python scripts/plan_review_receipt_evidence_smoke.py`
  - 현 환경 결과: 샌드박스 `localhost:5432` 접근 제한으로 미실행(권한 승인 환경에서 재확인 필요)
- 판단:
  - 권한 엔타이틀먼트/서비스 가드 로직의 코드·테스트 기준 유효성은 유지됨.

## 1) 실제 점검/수정 파일
- services/plan.py
- services/import_popbill.py
- services/tax_package.py
- routes/web/bank.py
- routes/web/package.py
- templates/bank/index.html
- templates/package/index.html
- tests/test_plan_entitlements.py
- tests/test_plan_service_guards.py
- scripts/plan_review_receipt_evidence_smoke.py
- docs/DEV_TESTING.md

## 2) 레거시 권한 비교 제거 현황
- 전수 검색 키워드: `user.plan ==`, `users.plan ==`, `is_pro_user(`, `is_pro_plan`, `pro만/프로만`
- 결과:
  - `user.plan == 'pro'` 같은 직접 권한 비교는 없음
  - `is_pro_plan` 템플릿 변수 제거 완료
  - `is_pro_user()`는 `services/plan.py`에 deprecated 래퍼로만 잔존(사용처 없음)
  - `users.plan`은 호환 저장용(`set_user_plan`)으로만 유지

## 3) 서비스 2차 방어 적용 여부
- 적용 완료:
  - `services/import_popbill.py::sync_popbill_for_user()`
    - `ensure_can_link_bank_account(user_pk)` 2차 방어 추가
    - 권한 위반 시 `PopbillImportError` 반환
  - `services/tax_package.py::build_tax_package_zip()`
    - `ensure_can_download_package(user_pk)` 2차 방어 추가
- 의미:
  - 라우트 밖에서 서비스 직접 호출 시에도 정책 위반 우회 방지

## 4) 다운그레이드 계좌 처리 정책과 실제 동작
- 정책:
  - 기존 초과 연결 계좌: 조회/해제(OFF) 허용
  - 신규 추가 및 재활성화(OFF->ON): 차단
- 코드 근거:
  - `routes/web/bank.py::toggle()`
    - OFF는 플랜 무관 허용
    - ON은 `ensure_can_link_bank_account` + `can_activate_more_bank_links` 강제
- UI 안내 보강:
  - `templates/bank/index.html`
    - `active_count > max_linked_accounts` 시 초과 안내 문구 노출

## 5) review/receipt/evidence free 허용 검증 결과
- 자동 검증 스크립트:
  - `scripts/plan_review_receipt_evidence_smoke.py`
- 검증 범위(각 플랜 free/basic/pro 반복):
  - `GET /dashboard/review?month=2026-03` -> 200
  - `GET /inbox/import` -> 200
  - `GET /dashboard/vault?month=2026-03` -> 200
  - `POST /inbox/evidence/<id>/mark` -> 302
  - `POST /inbox/evidence/<id>/upload` -> 302
- 결론:
  - review/receipt/evidence는 free 포함 전 플랜 접근/핵심 액션 허용

## 6) 현재 확정 권한 표
| 기능 | free | basic | pro | 비고 |
|---|---|---|---|---|
| 세금/건보료 보기 | 허용 | 허용 | 허용 | 동일 계산 원칙 유지 |
| CSV/엑셀 업로드 | 허용 | 허용 | 허용 | |
| review 접근 | 허용 | 허용 | 허용 | |
| 영수증 첨부 | 허용 | 허용 | 허용 | |
| 증빙 관리 | 허용 | 허용 | 허용 | |
| 계좌 자동 연동 | 차단 | 허용(기본 1) | 허용(기본 2) | `extra_account_slots` 가산 |
| 패키지 ZIP 다운로드 | 차단 | 허용 | 허용 | 서비스 2차 방어 포함 |
| 자동 동기화 주기 | 없음 | 240분 | 60분 | entitlement 기준 |

## 7) 아직 미완료 항목
- 아래 항목은 본 문서의 플랜 가드 범위를 넘어서는 운영/결제 자동화 영역이며, 별도 문서 기준으로 추적 필요:
  - 실제 결제 연동(정기결제/웹훅)
  - `extra_account_slots` 결제 반영 자동화
  - 자동 동기화 스케줄러(주기 실행 엔진)
  - 첫 연동 3개월 백필 혜택의 과금/정책 연동 자동화

## 8) 최종 판단
- 상태: **핵심은 의도대로 반영됨(추가 보완 필요)**
- 근거:
  - 레거시 직접 권한 비교 제거/정리 완료
  - bank/package 서비스 레벨 2차 방어 적용 완료
  - 다운그레이드 초과 계좌 정책이 코드+UI로 명문화됨
  - free review/receipt/evidence 허용이 실제 라우트 스모크로 확인됨
- 추가 보완 필요:
  - 결제/구독 실시간 반영 및 배치 스케줄러를 붙여 운영 자동화 마무리

# SafeToSpend 인수인계 컴팩트 가이드

최초 작성일: 2026-03-11  
최신 업데이트: 2026-03-12
용도: 새 대화(스레드)에서 즉시 작업 재개

---

## 1) 현재 상태 한 줄 요약
- 개발 진척도: **결제/구독 핵심 구조는 구현됨** (`registration → checkout → reconcile → projector → recurring`).
- 오픈 판정: **개발 진행 GO / 실서비스 오픈 NO-GO** (스테이징 실브라우저+인프라 실측 부족).
- 세금/건보 정확도: **조건부 신뢰**, 현재 실사용자 분포는 `exact/high 0%`로 입력/스냅샷 보강이 최우선.

---

## 2) 작업 시 절대 고정 원칙
- 정책 임의 변경 금지 (free/basic/pro/추가계좌, 업그레이드, 해지, grace 3일).
- 저장 금지 데이터 절대 금지:
  - raw 카드번호/CVC/카드비밀번호/민감 인증정보
  - authKey 장기 저장
  - billingKey 평문 저장
  - 비밀키 DB 저장
  - raw querystring 장기 저장
- 권한 반영 원칙:
  - `users.plan_code / plan_status / extra_account_slots`는 **projection**
  - 최종 반영은 `reconcile -> projector` 경로만
- `success URL`/`webhook` 단독 신뢰 금지.
- 멱등성 키 유지:
  - `order_id`, `payment_key`, `transmission_id/event_hash`, `source_type+source_id`

---

## 3) 표준 작업 방식 (항상 유지)
- 티켓 **번호 순서대로** 진행.
- 이전 티켓 완료 기준/검증 통과 전 다음 티켓 금지.
- 추측 금지: 코드/DB/테스트/문서 근거 기반.
- 가능하면 최소 수정, 회귀 테스트 추가.
- 보고는 매 티켓 동일 포맷 사용:
  1. 변경 대상 파일
  2. 문제 원인
  3. 수정 목표
  4. 구현 요구사항
  5. 완료 기준
  6. 검증 방법
  7. 남은 리스크

---

## 4) 지금까지의 핵심 결과 (압축)

### 4-1. 플랜/권한
- 문서: `docs/PLAN_PERMISSION_QA.md`
- 상태:
  - 레거시 직접 plan 비교 정리
  - bank/package 서비스 2차 가드 적용
  - 다운그레이드 초과 계좌 정책(조회 허용, 신규/재활성화 제한) 정리
  - free/basic/pro의 review/receipt/evidence 접근 검증 보강

### 4-2. 결제/구독 코어
- 문서:
  - `docs/BILLING_CHECKOUT_STAGE_REPORT.md`
  - `docs/BILLING_SUBSCRIPTION_FLOW_REDESIGN_REPORT.md`
  - `docs/BILLING_RECURRING_STAGE_REPORT.md`
  - `docs/BILLING_RECURRING_VERIFICATION.md`
- 상태:
  - `billing registration` 구현
  - `checkout intent` + `confirm` 구현
  - `reconcile` + `projector` 구현
  - `운영 복구 CLI` 구현(reconcile/replay/reproject)
  - recurring/due selection/grace/retry/past_due/cancel_effective 구현
  - loop(등록 후 재등록 무한반복) 완화 구조 반영

### 4-3. 운영 준비
- 문서:
  - `docs/OPERATIONS_READINESS_REPORT.md`
  - `docs/PRELAUNCH_OPERATIONS_CHECKLIST.md`
  - `docs/BACKUP_AND_RECOVERY_RUNBOOK.md`
- 상태:
  - 정책 문서 초안 4종 작성
  - DB 백업/복구 리허설 성공
  - 파일 백업/복구 샘플 리허설 성공
  - 문의/공지 최소 구조 정리

### 4-4. 세금/건보 감사
- 문서:
  - `docs/TAX_NHIS_LOGIC_AUDIT.md`
  - `docs/TAX_NHIS_99_ACCURACY_REPORT.md`
  - `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`
  - `docs/TAX_NHIS_REQUIRED_INPUTS.md`
- 결론:
  - 세금: 공식 코어/회귀 테스트는 안정, 실사용은 `missing_taxable_income` 병목이 지배적
  - 건보: 공식 구조/회귀 테스트는 안정, 실사용은 `missing_snapshot` 병목이 지배적
  - 기존 NHIS 골든 불일치 1건(`test_case_d_cap_clamp`)은 해소됨
  - 전체 판정: “특정 조건(입력 충족 + 스냅샷 준비)에서만 99% 근접 운영 가능”

### 4-5. 실사용 분포 스냅샷 (2026-03-12)
- 근거: `reports/accuracy_level_audit_latest.json` (97명)
- 세금:
  - exact_ready 0 (0.00%)
  - high_confidence 0 (0.00%)
  - limited 2 (2.06%)
  - blocked 95 (97.94%)
- 건보:
  - exact_ready 0 (0.00%)
  - high_confidence 0 (0.00%)
  - limited 0 (0.00%)
  - blocked 97 (100.00%)

---

## 5) 현재 최종 판정 (중요)
- 기준 문서: `docs/BILLING_GO_NO_GO_REPORT.md`
- 판정:
  - 다음 개발 단계: **GO**
  - 실서비스 오픈: **NO-GO**
- 차단 핵심:
  1. 스테이징 실브라우저 결제 E2E 증거 부족
  2. webhook/duplicate/refresh/세션없는 callback 실측 부족
  3. 프록시/APM/CSP 민감값 노출 실측 부족
  4. 정책 문서 실제 공개 경로 미완성

---

## 6) 새 스레드 시작 시 권장 읽기 순서 (빠른 온보딩)
1. `docs/BILLING_GO_NO_GO_REPORT.md`
2. `docs/BILLING_SUBSCRIPTION_FLOW_REDESIGN_REPORT.md`
3. `docs/BILLING_RECURRING_VERIFICATION.md`
4. `docs/OPERATIONS_READINESS_REPORT.md`
5. `docs/PRELAUNCH_OPERATIONS_CHECKLIST.md`
6. `docs/TAX_NHIS_99_ACCURACY_REPORT.md`
7. `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`
8. `docs/TAX_NHIS_LOGIC_AUDIT.md`

---

## 7) 즉시 실행 가능한 검증 커맨드 (로컬)
- billing 핵심 테스트:
  - `.venv/bin/python -m unittest discover -s tests -p 'test_billing_*.py'`
- recurring 검증:
  - `.venv/bin/python -m unittest tests.test_billing_recurring tests.test_billing_reconcile_service`
- 세금/건보 감사 관련:
  - `.venv/bin/python -m unittest tests.test_tax_required_inputs tests.test_tax_estimate_service tests.test_tax_nhis_result_meta tests.test_nhis_input_paths tests.test_nhis_required_inputs tests.test_nhis_reference_rules tests.test_nhis_official_golden tests.test_tax_accuracy_cases tests.test_tax_nhis_ui_copy`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 200 --output reports/accuracy_level_audit_latest.json`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py --limit 200 --output reports/accuracy_input_gap_latest.json`

---

## 8) 다음 스레드에서 바로 요청하면 좋은 작업 템플릿
- 예시:
  - “`THREAD_HANDOFF_COMPACT.md` 기준으로 티켓 1부터 순서대로 진행”
  - “각 티켓 완료 시 1~7 포맷으로 보고”
  - “정책 변경 금지 / 민감정보 저장 금지 / 테스트 근거 필수”

---

## 9) 현재 남은 핵심 TODO (압축)
- 스테이징 실브라우저 결제 2개 이상 PASS 증거 확보
- webhook/duplicate/refresh/세션없는 callback 실측
- 프록시/APM/CSP 실측(민감값 노출 0 확인)
- 정책 문서 실제 공개 라우트/템플릿 연결
- 세금 `blocked/limited` 사용자의 필수 입력 회수율 개선(과세표준/소득분류/기납부)
- 건보 `missing_snapshot` 해소(스냅샷 준비율/업데이트 성공률 개선)

---

## 10) 한계/주의
- 일부 검증은 인프라 권한 없으면 로컬에서 대체 불가.
- “문서상 완료”와 “실측 완료”를 분리해서 판단해야 함.
- 실오픈 GO는 반드시 실측 근거가 채워진 뒤에만 가능.

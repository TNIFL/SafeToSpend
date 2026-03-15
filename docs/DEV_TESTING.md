# DEV TESTING GUIDE

로컬 개발/검증 전용 가이드입니다. 운영 환경에서는 사용하면 안 됩니다.

## 1) 서버 실행 전 준비
- 의존성 설치: `pip install -r requirements.txt`
- DB 마이그레이션: `flask --app app db upgrade`
- (선택) 업로드 용량 조정:
  - 파일 1개 제한: `MAX_UPLOAD_BYTES` (기본 20MB)
  - 요청 전체 제한: `MAX_REQUEST_BYTES` (기본 100MB)

## 2) 표준 테스트 계정 시드
- 계정 upsert:
  - `python scripts/dev_seed.py`
- 기존 테스트 데이터까지 초기화하고 시작:
  - `python scripts/dev_seed.py --reset-data`

권장 실행 순서(재현 안정성):
- 스모크/회귀 점검 전에는 항상 `python scripts/dev_seed.py --reset-data`를 먼저 실행하세요.
- 거래를 직접 비우는 스크립트는 `transactions`보다 `evidence_items`를 먼저 정리해야 FK 오류를 피할 수 있어요.
- `--reset-data` 이후 검증 스크립트는 병렬 실행하지 말고 순차 실행하세요. (초기화/삽입 타이밍이 겹치면 중복 해시 오류가 날 수 있어요)

고정 테스트 계정(로컬 전용, 운영 금지)
- 이메일: `test+local@safetospend.local`
- 비밀번호: `Test1234!`

## 3) 서버 실행
- `flask --app app run --debug`

### 보안 관련 환경변수(권장)
- `APP_ENV=production` 에서는 `SECRET_KEY`를 반드시 운영용 값으로 설정해야 합니다.
- 기본값으로 운영 기동을 허용해야 하는 긴급 상황(권장하지 않음):
  - `ALLOW_INSECURE_DEV_SECRET_KEY=1`
- 프록시 환경에서만 XFF 신뢰:
  - `TRUST_PROXY_X_FORWARDED_FOR=1`
- 레이트리밋 Redis 백엔드(선택):
  - `RATE_LIMIT_REDIS_URL=redis://localhost:6379/0`

## 4) 샘플 CSV import
1. 로그인: `/login`
2. 가져오기 화면: `/inbox/import`
3. 파일 업로드: `sample_data/sample_bank.csv`
4. 자동 인식이 되면 바로 가져오기 완료, 아니면 매핑 화면에서 저장/가져오기 진행

## 5) 검증 URL
- `/dashboard/review`
- `/dashboard/tax-buffer`
- `/dashboard/package`
- `/dashboard/reconcile`
- `/dashboard/profile`
- `/dashboard/nhis`
- `/dashboard/account`
- `/dashboard/review/receipt-new?month=2026-03&focus=receipt_required&q=&limit=30`
- `/preview#upload`
- `/support`
- `/support/my`
- `/admin/inquiries` (관리자 전용)

## 5-1) 영수증 배치 워커(다중 업로드 백그라운드 처리)
1. 워커 실행(별도 터미널):
   - `python scripts/receipt_worker.py`
2. 대기 중 항목만 한 번 처리하고 종료:
   - `python scripts/receipt_worker.py --once`
3. 최대 처리 건수 제한 실행(예: 10건):
   - `python scripts/receipt_worker.py --max-items 10`
4. 멈춘 processing 항목 자동 복구 기준 조정(기본 15분):
   - `python scripts/receipt_worker.py --stale-minutes 5`
5. 상태 로그(하트비트) 간격 조정(기본 60초):
   - `python scripts/receipt_worker.py --heartbeat-seconds 30`
6. 연속 오류 허용 횟수(기본 10회, 초과 시 종료):
   - `python scripts/receipt_worker.py --max-errors 5`

### 운영 권장 실행(상시)
- 로그 폴더 생성:
  - `mkdir -p logs`
- 상시 실행(예: sleep 1.5초, stale 3분):
  - `nohup env PYTHONPATH=. .venv/bin/python scripts/receipt_worker.py --sleep 1.5 --stale-minutes 3 --heartbeat-seconds 60 >> logs/receipt_worker.log 2>&1 &`
- 실행 확인:
  - `tail -f logs/receipt_worker.log`
- 중지:
  - `pkill -f \"scripts/receipt_worker.py\"`

권장값:
- `--sleep 1.0~2.0`
- `--stale-minutes 3~5`
- `--heartbeat-seconds 30~60`
- `--max-errors 5~20` (process manager로 재시작할 때 권장)

## 6) 빠른 체크리스트
0. 계산 로직 회귀 테스트(세금/NHIS):
- `.venv/bin/python -m unittest tests.test_tax_official_core tests.test_tax_estimate_service tests.test_tax_reference_rules tests.test_tax_package_tax_metrics tests.test_tax_nhis_result_meta tests.test_tax_accuracy_cases tests.test_tax_nhis_ui_copy tests.test_nhis_official_golden tests.test_nhis_reference_rules tests.test_nhis_input_paths tests.test_nhis_accuracy_cases`

0-1. 입력 회수/가드 회귀 테스트(최신):
- `.venv/bin/python -m unittest tests.test_nhis_guard_ready tests.test_nhis_required_input_flow tests.test_nhis_required_inputs tests.test_tax_required_input_flow tests.test_tax_required_inputs tests.test_tax_nhis_ui_guard_behavior`
- `.venv/bin/python -m unittest tests.test_tax_input_draft tests.test_tax_input_modes tests.test_new_user_tax_input_gate`

0-2. 정확도 분포/입력 갭 집계(로컬 DB 연결 필요):
- `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_post_inline_save.json`
- `PYTHONPATH=. .venv/bin/python scripts/tax_input_gap_audit.py --limit 300 --output reports/tax_input_gap_audit_post_completion_improvement.json`
- `PYTHONPATH=. .venv/bin/python scripts/nhis_snapshot_gap_audit.py --limit 300 --output reports/nhis_snapshot_gap_audit_post_completion_improvement.json`
- `PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py --limit 300 --output reports/accuracy_input_gap_report_post_completion_improvement.json`

0-3. 입력 퍼널 계측/집계(로컬 DB 연결 필요):
- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_input_funnel_instrumentation`
- `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --days 30 --limit 5000 --output reports/input_funnel_audit_post_inline_save.json`

0-4. 법률 문서 페이지/링크 노출 점검:
- `.venv/bin/python -m unittest tests.test_legal_pages`
- 수동 확인 URL: `/privacy`, `/terms`, `/disclaimer`

0-5. 영수증 비용처리 규칙 엔진 v1 회귀 테스트:
- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_rules_engine tests.test_receipt_expense_rules_integration`
- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_guidance_page tests.test_receipt_expense_inline_explanations tests.test_receipt_expense_guide_entrypoints`

0-6. 영수증 비용처리 follow-up 재평가 플로우 회귀 테스트:
- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_followup_rules tests.test_receipt_expense_followup_integration`
- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_rules_engine tests.test_receipt_expense_rules_integration tests.test_receipt_expense_inline_explanations`

1. 원천징수 반영:
- `/dashboard/profile`에서 원천징수 `없음` 저장 후 `/dashboard/tax-buffer` 수치 확인
- 원천징수 `있음` 저장 후 새로고침해서 수치 감소 확인

2. 건보료 반영:
- `/dashboard/profile`에서 건강보험 월 납부액 입력
- `/dashboard/review`, `/dashboard/tax-buffer`에서
  - `건보료 보관(이번 달)`
  - `이번 달 총 보관 권장액`
  확인

3. 패키지 확인:
- `/dashboard/package`에서 preflight 카드/안내 확인
- ZIP 다운로드 후 요약/정리표/raw/첨부 구조 확인

4. 미입력 fallback:
- 프로필 일부를 `모름`으로 둬도 화면이 깨지지 않고 안내 문구만 보이는지 확인

5. 영수증 다중 업로드 배치:
- `/dashboard/review/receipt-new?...`에서 3~5장 업로드
- 업로드 직후 배치가 생성되고 상태가 `대기/분석 중/완료/실패`로 갱신되는지 확인
- 실패 항목 `재시도` 후 워커가 다시 처리하는지 확인

5-1. 영수증 재시도 전제 데이터 강제 생성(권장):
- 재시도 가능한 실패 항목 생성:
  - `python scripts/dev_seed_receipt_retry_case.py`
- 출력된 `batch_id`, `item_id`로 상태/재시도 버튼 확인:
  - `/dashboard/review/receipt-new?month=YYYY-MM&focus=receipt_required&q=&limit=30&batch_id=<batch_id>`
- 목적:
  - `file_key`가 있는 실패 항목을 만들어 “재시도 버튼”/일괄 재시도 동작을 안정적으로 검증하기 위함

6. 지연/복구 체크:
- 워커를 잠시 멈춘 상태에서 다중 업로드 후 `처리 지연` 안내와 `영수증 처리함 보기` 버튼 노출 확인
- 같은 상태에서 `지금 처리 재개` 버튼 클릭 시 대기 항목 1건이 즉시 처리되는지 확인
- 같은 상태에서 자동 재개가 약 3~5초 간격으로 진행되는지(진행률이 한 건씩 증가) 확인
- `처리 멈추기` 클릭 시 대기 항목이 중단되고, 필요 시 `재시도`로 다시 시작 가능한지 확인
- 실패 항목이 2건 이상이면 `실패 항목 일괄 재시도` 버튼이 보이는지 확인
- `파일 누락/형식 오류/인식 실패/일시 오류` 배지와 가이드 문구가 노출되는지 확인

7. /preview 계산 일관성 체크(메인 엔진 기준):
- 업로드 파일: `sample_data/preview_case_a.csv`
- 업로드 후 `/preview#upload`에서 3지표 확인:
  - 이번 달 보관 권장액(추정)
  - 이번 달 할 일
  - 세무사 전달 패키지
- 같은 데이터를 계정에서 import 후 `/dashboard/tax-buffer?month=2026-03` 확인
- 두 화면 수치가 큰 틀에서 일치하는지 확인(차이는 원천징수/경비 확정 상태 영향 가능)
- `왜 이렇게 나왔나요?`에서 수입/경비/원천징수/건보료 반영 설명 확인

8. /preview 이상치 체크(수입 480만원 케이스):
- Case A: `sample_data/preview_case_a.csv` (수입 4,800,000 / 경비 소량 / 원천징수 없음)
- Case B: `sample_data/preview_case_b.csv` (수입 4,800,000 / 지출 2,000,000 / 원천징수 문구 포함)
- `/preview#upload` 업로드 후, 보관 권장액이 비정상적으로 0에 가깝지 않은지 확인
- 수치가 예상보다 낮으면 `왜 이렇게 나왔나요?`에서
  - 원천징수 추정 차감
  - 경비 반영 방식(업무 확정 여부)
  - 기준 월(month_key)
  를 먼저 점검

9. 리뷰 일괄 처리/되돌리기 체크:
- `/dashboard/review?month=2026-03&lane=review&focus=expense_confirm&limit=30` 진입
- 체크박스로 3건 이상 선택 후 `업무로/개인으로/불필요/영수증 첨부/필수로 표시/검토로 표시` 중 하나 실행
- 실행 후 목록/카운트가 즉시 반영되는지 확인
- 상단 `되돌리기 (U)` 또는 `선택 되돌리기`로 상태가 원복되는지 확인
- 최근 작업은 최대 10건까지 되돌릴 수 있는지 확인

10. 대사 리포트 체크:
- `/dashboard/reconcile?month=2026-03` 진입
- 월 합계(수입/업무경비/개인/수입 제외), 필수 누락 건수/금액, 미분류/혼합, 중복 의심이 보이는지 확인
- 각 카드의 `정리하기로 이동` 버튼이 올바른 탭으로 이동하는지 확인

11. 플랜 서버 가드 체크(free/pro):
- `/dashboard/account#plan`에서 기본 플랜 상태 확인
- FREE 상태:
  - `GET /bank/popbill-url` → 403
  - `POST /bank/sync` → `/pricing` 리다이렉트
  - `GET /dashboard/package/download?...` → `/pricing` 리다이렉트
- PRO 상태(플랜 변경 후):
  - `GET /bank/popbill-url` → 200(JSON)
  - `GET /dashboard/package/download?...` → ZIP 다운로드 응답

12. 정기 거래 후보/자동 분류 체크:
- `sample_data/sample_bank.csv`를 가져온 뒤 `/overview` 진입
- `이번 달 예상(정기)` 섹션 노출 확인
- 후보가 보이면 `업무로 자동/개인으로 자동/수입으로 자동/수입 제외로 자동` 버튼 실행
- 실행 후 플래시 메시지(규칙 저장 + 적용 건수) 확인
- `/dashboard/review`에서 해당 거래처 거래가 자동 분류되었는지 확인

13. 이상치/누락 힌트 체크:
- `/dashboard/reconcile?month=YYYY-MM` 진입
- `비정상적으로 큰 거래` 섹션 노출 확인(없으면 0건 안내)
- `누락 가능성 힌트` 섹션 노출 확인(업종이 모름이면 미노출/빈 안내 가능)
- 각 `확인하기` 링크가 `/dashboard/review` 필터로 연결되는지 확인

14. 월말 리마인더(인앱 배너) 토글 체크:
- `/dashboard/account`에서 `월말 정산 리마인더(인앱 배너) 받기` ON/OFF 저장
- ON 상태에서 월말(해당 월 말 3일 전)에는 `/overview` 상단 배너가 노출되는지 확인
- OFF 상태에서 같은 조건에서도 배너가 숨겨지는지 확인

15. 문의/관리자 문의 관리 체크:
- 사용자 문의 작성:
  - 로그인 후 `/support` 진입
  - 제목/내용 입력 후 `문의 보내기`
  - 연속 제출 시 30초 쿨다운 안내가 노출되는지 확인
  - `/support/my` 목록, `/support/my/<id>` 상세에서 상태/내용 확인
  - 목록 페이지 하단 `이전/다음` 페이지 이동이 동작하는지 확인
- 관리자 접근:
  - 고정 관리자 계정으로 로그인
    - 기본: `admin@safetospend.local` / `Admin1234!`
    - 변경 시: `ADMIN_FIXED_EMAIL`, `ADMIN_FIXED_PASSWORD`
  - `/admin/inquiries` 진입 후 목록/상세/답변 저장 확인
  - 관리자 목록도 페이지 하단 `이전/다음` 이동이 동작하는지 확인
  - 같은 문의 상세를 2개 탭에서 열고 한쪽에서 먼저 답변 저장 후, 다른 탭 저장 시 “다른 관리자가 먼저 수정” 안내가 뜨는지 확인
- 권한 차단:
  - 일반 계정으로 `/admin/inquiries` 접근 시 차단 안내 확인
- 설정 누락:
  - `ADMIN_FIXED_EMAIL`이 비어 있거나 잘못된 경우 `/admin/inquiries` 접근 시 500 없이 “관리자 계정 설정 필요(개발용)” 안내 확인
  - 문의 테이블 미적용 상태(개발환경)에서도 `/support`, `/support/my`, `/admin/inquiries`가 500 없이 안내되는지 확인

16. 보안 스모크(로컬/CI 공통):
- 실행:
  - `python scripts/security_smoke.py`
- 확인 항목:
  - 기본 보안 헤더 응답 포함
  - CSRF 없는 웹 POST 차단
  - `/api/*` Bearer 미포함 접근 차단
  - open redirect 방어(`next` 외부 URL 차단)

17. API 로그아웃 E2E(로컬 DB 준비 시):
- 실행:
  - `python scripts/api_logout_e2e_check.py`
- 검증:
  - 토큰 발급(200)
  - `/api/auth/logout` 성공(200)
  - 동일 refresh로 재발급 요청 시 차단(401)
- 테스트 계정 변경(선택):
  - `E2E_TEST_EMAIL`, `E2E_TEST_PASSWORD` 환경변수 사용

18. 건보료/장기요양(추정) 체크:
- 기준 데이터 수동 갱신:
  - `flask --app app refresh-nhis-rates`
- 페이지 진입:
  - `/dashboard/nhis`
- 핵심 카드 확인:
  - `현재 기준 합계(추정)`, `11월 반영 예상 합계(추정)`, `11월 차이(추정)`이 함께 보이는지 확인
- 기준 카드 확인(2026 기준):
  - 건강보험료율 7.19%
  - 장기요양 비율 13.14%
  - 점수당 금액 211.5원
  - 재산 기본공제 1억원

19. 영수증 비용처리 안내 UX 체크:
- 라우트/페이지 렌더:
  - `.venv/bin/python -m unittest tests.test_receipt_expense_guidance_page`
- 인라인 설명/가이드 라벨:
  - `.venv/bin/python -m unittest tests.test_receipt_expense_inline_explanations`
- 진입점 링크:
  - `.venv/bin/python -m unittest tests.test_receipt_expense_guide_entrypoints`
- 수동 확인 URL:
  - `/guide/expense`
  - `/dashboard/review?month=2026-03&lane=review&focus=receipt_attach`
  - `/dashboard/review?month=2026-03&lane=review&focus=expense_confirm`

20. 영수증 비용처리 규칙 엔진 준비 문서 체크:
- 문서 확인:
  - `docs/RECEIPT_EXPENSE_OFFICIAL_SOURCES.md`
  - `docs/RECEIPT_EXPENSE_RULES_TABLE.md`
  - `docs/RECEIPT_EXPENSE_INPUT_SCHEMA.md`
  - `docs/RECEIPT_EXPENSE_OUTPUT_CONTRACT.md`
  - `docs/RECEIPT_EXPENSE_TEST_CASES.md`
  - `docs/RECEIPT_EXPENSE_RULE_ENGINE_PREP.md`
- 확인 포인트:
  - 상태값 4종이 문서 전반에서 일치하는지
  - `/guide/expense` anchor와 `guide_anchor` 계약이 일치하는지
  - 고가 장비/접대비/경조사비가 보수적으로 분류돼 있는지
  - 자동차 부과 폐지
- 고지서 기반(high/medium) 추정:
  - `최근 고지서 건보료` 또는 `부과점수` 입력 후 저장
  - 결과 카드의 건강보험료/장기요양/합계(추정) + 11월 차이(추정) 갱신 확인
- 고지서 없는 간편모드(low) 추정:
  - 가입유형을 `지역가입자`로 두고 연소득 또는 재산세 과세표준 입력
  - `간편 추정` 안내/오차요인 문구 확인
- 규칙 엔진 분해값 확인:
  - `근거 보기 (추정)`에서 아래 값이 보이는지 확인
    - 적용 소득연도 / 적용 재산연도
    - 소득 평가월액 / 소득 점수 / 재산 점수
    - 상한/하한 적용 여부
- 왜/어떻게 섹션:
  - `왜 이 금액인지(소득/재산/차량 영향)` 비율이 표시되는지 확인
  - `바꿔보면 얼마 달라질까?` 섹션과 단일 가정 시뮬레이션이 보이는지 확인
- 공단 모의계산기 수동 대조:
  - 공단 모의계산기(https://www.nhis.or.kr/nhis/minwon/initCtrbCalcView.do)에서 같은 소득/재산/전월세를 입력해 결과를 비교
  - 차이가 있으면 아래 순서로 점검
    1) 재산세 과세표준 입력값(공시가격 아님) 차이
    2) 소득 100%/50% 분류 차이
    3) 하한/상한 적용 여부

## 19) Trust/Ops 스모크 (15분)
1. 공식 기준 not_ready 시 숫자 미노출
- 방법:
  - `data/official_snapshots/manifest.json`의 `valid`를 임시로 `false`로 바꾼 뒤 `/dashboard/nhis?month=2026-03` 진입
- 기대:
  - KPI 숫자는 숨겨지고 “결과 준비 중” 안내/CTA만 보임
  - 500 없이 렌더링됨
- 원복:
  - 테스트 후 manifest를 원래 값으로 되돌리기

2. watchdog 실행 + admin 반영
- 실행:
  - `python scripts/reference_watchdog.py --strict || true`
- 확인:
  - `data/reference_watch/status.json` 갱신
  - `/admin/ops`의 Reference Watch 카드에서 changed/failing/fail_streak 반영

3. 영수증 배치 재시도/중복 방지
- URL:
  - `/dashboard/review/receipt-new?month=2026-03&focus=receipt_required&limit=30`
- 체크:
  - 같은 파일 재업로드 시 중복 안내가 뜨는지
  - 실패 항목 재시도 시 즉시 가능/대기 필요/재시도 제한 메시지가 정상 동작하는지
  - 워커 실행(`python scripts/receipt_worker.py --once`) 후 배치 상태가 `done/done_with_errors`로 안정적으로 수렴하는지

4. 민감 파일 차단
- 체크 파일명 예시:
  - `주민등록증.jpg`, `idcard_front.png`, `familyregister.pdf`
- 기대:
  - 업로드가 차단되고 사용자 안내 문구가 노출됨
  - `/dashboard/package?month=2026-03` ZIP 생성 시 민감 파일명 증빙은 첨부 폴더에서 제외됨

5. 배포 전 자동 게이트
- 실행:
  - `python scripts/predeploy_check.py`
- 기대:
  - `phase1_gate_not_ready`, `reference_watch_fail_streak` 등이 있으면 exit 1로 배포 차단

19. 공식 레퍼런스 수학 검증(2026 스냅샷):
- 단위 테스트 실행:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_nhis_reference_rules tests.test_tax_reference_rules`
- 자동 리포트 생성:
  - `PYTHONPATH=. .venv/bin/python scripts/verify_reference_math.py`
- 결과 확인:
  - `docs/VERIFICATION_REPORT.md`에서 PASS/FAIL, 기준값, 출처 URL 확인
- 수동 확인 URL:
  - `/dashboard/nhis?month=2026-03`
  - 근거 보기(추정)에서 기준 연도/마지막 확인일/소득·재산·장기요양 분해값 확인
    4) 반영주기(현재월 기준 적용 소득연도/재산연도) 차이
    5) 현재 거주 전월세/보유주택 임대 전월세 중복 합산 여부
- fetch 실패 fallback(수동):
  - 네트워크 차단 상태에서 `/dashboard/nhis` 진입
  - 500 없이 `마지막 기준으로 추정` 또는 `기준 데이터 준비 중` 안내 확인

19. 건보료 추정 엔진 스모크 스크립트:
- 실행:
  - `python scripts/check_nhis_estimator.py`
- 확인:
  - `bill/simple/employee` 3개 케이스 출력

20. Billing reconcile/projector 스모크 (20분):
- 단위 테스트:
  - `.venv/bin/python -m unittest tests.test_billing_reconcile_service tests.test_billing_projector tests.test_billing_reconcile_wrappers`
- Postgres 경합 probe:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_pg_concurrency_probe.py --cleanup`
  - `reconcile_projection_race.ok=true`
  - `projector_source_idempotency.ok=true`
- webhook 경로 점검:
  - `.venv/bin/python -m unittest tests.test_billing_webhook_api tests.test_billing_webhook_ingest_service`
- 운영 복구 CLI 등록 점검:
  - `FLASK_APP=app.py .venv/bin/flask billing-reconcile --help`
  - `FLASK_APP=app.py .venv/bin/flask billing-replay-event --help`
  - `FLASK_APP=app.py .venv/bin/flask billing-reproject-entitlement --help`
  - 음수/비정상 금액 없이 결과 출력

19-1. 건보료 UX/로직 정합성 자동 점검:
- 실행:
  - `python scripts/nhis_ux_logic_audit.py`
- 확인:
  - `/dashboard/assets?month=2026-03&skip_quiz=1`, `/dashboard/nhis?month=2026-03` 라우트 스모크(500 없음)
  - 문구 키워드 확인:
    - `매월`, `다음 달 10일까지`, `11월`, `반영`, `월 보험료`, `(추정)`
  - 오해 문구 차단:
    - `11월에만 납부/계산` 같은 문구가 없어야 함
  - 비교 라벨 확인:
    - `현재 적용`, `11월 반영`, `차이`가 함께 노출되는지 확인

19-2. 즉시 피드백(assets) 근거/디버그 자동 점검:
- 실행:
  - `python scripts/nhis_feedback_audit.py`
- 확인:
  - `/dashboard/assets?month=2026-03&skip_quiz=1`
  - `/dashboard/assets?month=2026-10&skip_quiz=1`
  - `/dashboard/assets?month=2026-11&skip_quiz=1`
  - 각 화면에 아래 라벨이 보이는지
    - `현재 적용(YYYY-MM)`
    - `11월 반영(YYYY-11)`
    - `차이 (추정)`
    - `건강보험료(추정)`, `장기요양(추정)`, `합계(추정)`
  - `?debug_nhis=1` 에서(관리자 또는 DEBUG 모드)
    - current/november 점수/금액/적용연도 분해 표시
    - `nov_calc_reused_current`, `fallback_used`, `fallback_reason` 표시
  - 수동 확인 포인트:
  - month=2026-03에서 `현재 적용(2026-03)`, `11월 반영(2026-11)`인지 확인
  - `차이`가 0원일 때 “왜 0원인지” 안내 문구가 표시되는지 확인
  - fallback이 발생하면 “임시 기준 표시” 안내가 보이는지 확인

19-3. assets 가정 시뮬레이션 스모크:
- 실행:
  - `python scripts/nhis_whatis_smoke.py`
- 확인:
  - `GET /dashboard/assets?month=2026-03&skip_quiz=1` 응답이 500 없이 내려오는지
  - HTML에 `월 예상 건보료(추정)`, `바꿔보면 얼마 달라질까?` 문구가 있는지
  - `id="nhis-whatis-payload"` 데이터 payload 마커가 있는지

19-3-1. assets 가정 시뮬레이션 수동 QA:
- URL:
  - `/dashboard/assets?month=2026-03&skip_quiz=1`
- 케이스 A(금융소득 경계):
  - 금융소득을 `8,000,000 ~ 12,000,000` 구간으로 저장
  - `금융소득 1,000만 기준으로 계산해보기` 버튼이 노출되는지 확인
  - 버튼 클릭 시 `가정 결과: 월 △원 (추정)` + `변한 항목: ...`이 함께 보이는지 확인
- 케이스 B(전세 1.2억):
  - 현재 거주 형태를 `전세`로 두고 보증금을 `120000000` 저장
  - `보증금 1,000만 올리면` 클릭 시 총액 증가 확인
  - `원래대로` 클릭 시 저장 직후 기준 총액으로 복귀 확인
- 케이스 C(월세 모드):
  - 현재 거주 형태를 `월세`로 두고 월세 입력 저장
  - `월세 10만 올리면` 클릭 시 총액 변화 확인
  - `월세 10만 내리면` 클릭 시 반대 방향 변화 확인
- 실패 안내:
  - 계산 불가 시 화면에 `지금은 가정 계산을 할 수 없어요. 입력을 저장하면 정확도가 올라가요.` 문구가 표시되는지
  - `?debug_nhis=1`에서만 누락 키(debug) 정보가 추가로 보이는지

19-4. NHIS 엔진 sanity(스케일/분기/회귀):
- 실행:
  - `python scripts/nhis_engine_sanity.py`
- 확인:
  - CASE A(소득만): 월 보험료가 비정상 저금액(수천원대)으로 나오지 않는지
  - CASE B(소득+재산+전월세): points/금액이 CASE A 대비 증가하는지
  - CASE C(2026-03 vs 2026-11): `nov_calc_reused_current=false`, `fallback_used=false`인지
  - 출력은 `PASS/FAIL` + 경고 플래그 요약만 표시(민감정보 없음)

20. 셀프 점검(자동 품질 게이트):
- 실행:
  - `python scripts/self_audit.py`
- 빠른 실행(db upgrade 제외):
  - `python scripts/self_audit.py --no-upgrade`
- 제한 환경 실행(DB 소켓/네트워크 제약 시):
  - `python scripts/self_audit.py --no-db --no-upgrade`
- 자동 점검 항목:
  - 앱 부팅/import 오류
  - 마이그레이션 head 단일 여부 + upgrade 가능 여부
  - 핵심 라우트 스모크(`/, /preview, /inbox/import, /dashboard/review, /dashboard/tax-buffer, /dashboard/package`)
  - 자산 퀴즈 회귀 점검(집 2채/차 2대 저장, 자가 모드 이전 버튼, 다중 입력 UI 슬롯)
  - P0 정적 체크(Settings alias, 증빙 선삭제 위험, 영수증 파싱 예외 가드, 업로드 제한 키 브릿지)
  - 보안 기본 체크(next 오픈리다이렉트 방어, CSRF 가드, 세션 쿠키 플래그)
  - URL/메서드 충돌 점검
- 결과 해석:
  - `PASS`: 정상
  - `WARN`: 즉시 장애는 아니지만 확인 권장
  - `SKIP`: 환경 조건(테스트 유저/DB 등) 때문에 생략
  - `FAIL`: 우선 수정 필요(원인/힌트가 함께 출력됨)

20. 자산 진단(퀴즈 + 단일 페이지) 스모크:
- 최초 진입:
  - `/dashboard/assets` 접속 시 미완료 계정이면 `/dashboard/assets/quiz`로 이동되는지 확인
- 퀴즈 단계 저장:
  - 1~6단계를 `저장하고 다음`으로 진행
  - Step 3에서 `보유 주택 수`, Step 5에서 `차량 대수`를 변경해 반복 입력이 저장되는지 확인
  - 단계별로 `현재/11월 예상 건보료(추정)` 카드가 표시되는지 확인
  - `지금은 건너뛰기`가 동작하고 다음 단계로 넘어가는지 확인
- 완료 후 단일 페이지:
  - `/dashboard/assets?skip_quiz=1`에서 섹션 수정 후 저장
  - 저장 직후 추정 카드(현재/11월/차이/완료율)가 갱신되는지 확인
  - `보유 주택 목록`, `차량 목록`에서 항목 추가/수정/삭제가 되는지 확인
  - `+ 집 추가`, `+ 차량 추가` 버튼 클릭 시 같은 페이지 안에서 입력 카드가 바로 열리고 `취소`가 동작하는지 확인
  - `과거 고지 금액(선택)`에서 연도 행을 2개 이상 추가/저장 후 새로고침해도 유지되는지 확인
  - `month=2026-03&skip_quiz=1` 파라미터에서 저장 후에도 2026-03 기준 즉시 피드백이 유지되는지 확인
  - 즉시 피드백 카드에서 아래 라벨/의미가 보이는지 확인
    - `현재 적용(2026-03) 월 보험료 (추정)`
    - `11월 반영(2026-11) 월 보험료 (추정)`
    - `차이 (추정) = 11월 반영 - 현재 적용`
  - `month=2026-11` 또는 `month=2026-12`에서는
    - `이미 11월 반영 기준이 적용된 기간` 안내가 보이는지 확인
  - 카드/근거 패널에 아래 안내가 보이는지 확인
    - `건보료는 매월 납부(보통 다음 달 10일까지)`
    - `11월은 납부 달이 아니라 새 기준 반영 시점`
- 근거 패널:
  - 차량/부동산 섹션의 `근거 보기`에서 출처/기준연도/업데이트 시각/계산요약이 노출되는지 확인
- 데이터셋 fallback:
  - 네트워크 차단 상태에서 자산 페이지 진입 시 500 없이 `마지막 기준으로 추정` 또는 `기본값 추정` 안내가 보이는지 확인
- 포맷 변경 감지:
  - 공식 데이터 페이지 구조가 바뀐 경우 `/dashboard/assets`, `/dashboard/nhis`에서 `형식 변경 감지` 경고가 표시되는지 확인
  - 관리자 `/admin/assets-data`에서 `포맷 변경 감지` 상태가 보이는지 확인
- 관리자 진단(선택):
  - 관리자 계정으로 `/admin/assets-data` 접속
  - 차량/부동산 데이터셋의 기준연도/업데이트 시각/stale/fallback 상태 확인
  - 관리자 계정으로 `/admin/nhis-rates` 접속
  - NHIS 스냅샷 연도/요율/업데이트 시각/활성 상태 확인

## 7) NHIS 기준값 자동 갱신 운영 메모(cron 예시)
- 하루 1회 새벽 4시 갱신(서버 cron):
  - `0 4 * * * cd /path/to/SafeToSpend && .venv/bin/flask --app app refresh-nhis-rates >> logs/nhis_rates.log 2>&1`
- fetch 실패 시:
  - 기존 스냅샷 유지
  - `/dashboard/nhis`에서 마지막 업데이트 시각과 fallback 안내 확인

## 8) NHIS 규칙 스펙 문서
- 규칙/추정 경계, 공식 링크, 산식, 입출력 계약은 아래 문서를 기준으로 점검
  - `docs/NHIS_RULES_SPEC.md`

## 9) NHIS 고정 케이스 sanity (18만원 튐 회귀 방지)
- 실행: `PYTHONPATH=. .venv/bin/python scripts/nhis_engine_sanity.py`
- 고정 입력: 전세 1.2억, 월세 0, 배당/기타소득 1,200만원(연), 차량 0, 부양 0
- 출력: ①~⑥ 중간값(소득월액/재산점수/건강보험료/장기요양/최종)
- 가드: `total_krw`가 `70,000~150,000원` 범위를 벗어나면 `FAIL`
- `unit_scale_warning` 또는 `duplication_suspected`는 `WARN`으로 표시

## 10) 소득 데이터 흐름 맵(하이브리드)
- 저장 모델:
  - 자동 추정 입력: `asset_profiles.other_income_annual_krw`, `asset_items(kind=home/rent/car).input_json`
  - NHIS 기준 프로필: `nhis_user_profiles` (월/소득/재산/전월세/고지이력)
  - 사용자 확정 소득: `tax_profiles.profile_json.income_hybrid` (연도+scope+입력값 JSON)
- 입력/수정 라우트:
  - `/dashboard/assets` (`routes/web/profile.py::assets_page`)
  - 저장 로직: `services/assets_profile.py::save_assets_page`
- 건보료 계산 사용 경로:
  - `services/assets_estimator.py::build_assets_feedback`
  - `services/nhis_estimator.py` (①~⑥ 추정 계산)
- 세금 계산 사용 경로:
  - `services/risk.py::compute_tax_estimate`
- 기타소득 UI 위치:
  - `templates/assets.html`의 `기타소득` 블록
  - 하이브리드 확정 소득 UI: 같은 템플릿의 `소득 입력(하이브리드)` 블록

## 11) 하이브리드 소득 입력 QA(10분)
- 공통 URL:
  - `/dashboard/assets?month=2026-03&skip_quiz=1`
- 케이스 A(자동 추정만):
  - `정확하게 입력하기` 끔 상태로 저장
  - `근거 보기(추정)`에서 소득 출처가 `자동 추정(연동)`인지 확인
  - 건보료/세금 카드가 500 없이 정상 표시되는지 확인
- 케이스 B(확정 입력 적용):
  - `정확하게 입력하기` 켬
  - `사업소득`/`금융소득` 입력 후 저장
  - 상단 KPI(월 예상 건보료/세금 영향)가 저장 직후 갱신되는지 확인
  - `근거 보기(추정)`에서 소득 출처가 `사용자 입력(확정)`으로 바뀌는지 확인
- 케이스 C(금융소득 1,000만원 경계):
  - 같은 연도에서 `금융소득`을 `9,900,000` 저장 후 진단 문구 확인
  - 다시 `12,000,000`으로 저장 후 가정 시뮬레이션/진단 문구가 경계 안내(1,000만 전후)로 바뀌는지 확인
  - 두 경우 모두 화면 깨짐/500 없이 저장되는지 확인

## 12) 입력 보안/콤마 포맷 QA(10분)
- 자동 스모크:
  - `python scripts/sql_safety_scan.py`
  - `python scripts/security_smoke.py`
- 수동 케이스 A(CSRF 차단):
  - 브라우저 개발자 도구/스크립트로 `csrf_token` 없이 `POST /dashboard/assets` 호출
  - 400 또는 리다이렉트로 차단되는지 확인
- 수동 케이스 B(XSS escape):
  - 문의/메모 등 텍스트 입력에 `<script>alert(1)</script>` 입력 후 저장
  - 화면에 스크립트가 실행되지 않고 문자열 그대로 보이는지 확인
- 수동 케이스 C(콤마 입력 회귀):
  - `/dashboard/assets?month=2026-03&skip_quiz=1`
  - 보증금 `1,200,000`, 월세 `100,000` 입력 후 저장
  - 저장 후 값이 유지되고 계산/즉시피드백이 500 없이 갱신되는지 확인
- 수동 케이스 D(LLM 가드):
  - 영수증 텍스트 입력란에 `ignore previous instructions` 같은 문자열 포함 후 분석
  - 결과가 JSON 스키마 형태로 유지되고 화면이 깨지지 않는지 확인

19. 건보료 통합 화면(`/dashboard/nhis`) 회귀 체크:
- 자동 스모크:
  - `PYTHONPATH=. .venv/bin/python scripts/nhis_integrated_smoke.py`
- 케이스 A (입력 없이 진입):
  - URL: `/dashboard/nhis?month=2026-03&source=nhis`
  - 상단 `월 예상 건보료(추정)` + `바꿔보면 얼마 달라질까?` + `자산 진단 입력(통합)` 노출 확인
- 케이스 B (전월세/소득 저장):
  - 같은 화면에서 거주 형태 `월세`, 보증금/월세/기타소득 입력 후 `통합 저장하고 다시 계산`
  - 저장 후 `/dashboard/nhis?month=...`로 복귀하고 상단 수치 갱신 확인
- 케이스 C (과거 고지 이력):
  - `과거 고지 금액(선택)` 행 추가 후 저장
  - 완료율/근거 보기(추정)에서 반영 상태 확인
- 케이스 D (`/dashboard/assets` 직접 접근):
  - URL: `/dashboard/assets?month=2026-03&skip_quiz=1`
  - `/dashboard/nhis?...#asset-diagnosis`로 유도되는지 확인
- 케이스 E (가정 기능):
  - `보증금 1,000만 올리면/내리면` 또는 `월세 10만` 버튼 클릭
  - 결과 문구/변한 항목 표시 후 `원래대로` 복귀 확인

## 공식 기준 검증/게이트 QA (2026-03)

- 검증 스크립트 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/verify_official_refs.py --target-year 2026`
- 기대 결과:
  - 네트워크/파싱 실패 시 `exit 1`
  - `reports/official_ref_audit_YYYYMMDD.md` 생성
  - `data/official_snapshots/manifest.json` 갱신 (`valid=false`)
- 런타임 게이트 확인:
  - `GET /dashboard/nhis?month=2026-03`
  - `manifest.valid=false`일 때 `계산 불가(공식 입력/데이터 부족)` 안내 표시
  - 건보료/세금 KPI 숫자 미노출 확인
- NHIS 골든 테스트:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_nhis_official_golden`
- TAX 공식 코어 테스트:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_tax_official_core`

## Phase1 하드 게이트 스모크

- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/phase1_smoke.py`
- 기대 결과:
  - 런타임 경로(`ensure_active_snapshot`, `ensure_asset_datasets`)에서 refresh/fetch 호출이 차단됨
  - 게이트 차단 시 `계산 불가(공식 입력/데이터 부족)` 문구 노출
  - 재산 점수표 파일 누락 시 `property_points_table_missing`으로 차단

## Phase2 운영 루틴 스모크 (10분)

- 공식 스냅샷 배치 실행:
  - `PYTHONPATH=. .venv/bin/flask --app app refresh-official-snapshots`
  - `data/official_snapshots/run_log.json` 생성/갱신 확인
  - `data/official_snapshots/manifest.json`의 `refresh.active_snapshots` 갱신 확인
- 워치독 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/reference_watchdog.py`
  - `data/reference_watch/status.json`의 `last_checked_at`/`targets` 갱신 확인
- 관리자 화면 확인:
  - `/admin/ops`에서 `공식 스냅샷 갱신` 카드의 상태/스냅샷 ID/manifest hash 확인
  - `공식 기준 감시`에서 changed/failing 발생 시 상세 리스트 노출 확인
- 패치 초안 생성:
  - `PYTHONPATH=. .venv/bin/python scripts/suggest_parser_patch.py`
  - `reports/parser_patch_suggestion_*.md` 생성 확인(자동 반영 없음)
- 배포 전 게이트:
  - `PYTHONPATH=. .venv/bin/python scripts/predeploy_check.py`
  - `FAIL`이면 배포 중단, `PASS`일 때만 배포 진행

## NHIS UX 스모크 (10분)

- 자동 스모크(권장):
  - `PYTHONPATH=. .venv/bin/python scripts/nhis_integrated_smoke.py`
  - 기대 결과: `PASS: nhis integrated smoke`
- 엣지케이스 단위 테스트:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_income_hybrid_presence`
  - 기대 결과: 금융소득 있음/없음/모름 우회 POST 처리 3케이스 통과

- 대상 URL:
  - `/dashboard/nhis?month=2026-03`
  - `/dashboard/nhis?month=2026-03&debug=1`
  - `/dashboard/nhis?month=2026-03&retry=1`
- 체크 1 (refs_valid=true):
  - Hero/KPI/분해/근거가 정상 렌더되고 KPI 카드별 출처/기준 배지 중복이 제거됐는지 확인
  - 정확도 배지가 한 곳에만 보이고 `기준 보기` 토글이 열리는지 확인
- 체크 2 (refs_valid=false):
  - `결과 준비 중` 상태에서 `입력하러 가기`, `다시 확인` CTA 2개가 보이고 클릭 동작이 정상인지 확인
- 체크 3 (빠른 입력 30초):
  - 기본 노출이 질문 3개(가입유형/거주형태/금융소득)만 보이는지 확인
  - `정확도 올리기(선택)`를 열기 전에는 상세 입력(보유주택/차량/고지이력)이 접혀 있는지 확인
- 체크 4 (debug 고급 옵션):
  - `debug=1`에서만 `입력 출처 바꾸기(고급)`이 노출되는지 확인
  - 일반 URL에서는 해당 전환 UI가 숨겨지는지 확인
- 체크 5 (목록 저장 안정성):
  - 보유 주택/차량의 `목록 저장` 클릭 시 해당 항목만 저장되고 다른 항목 값이 바뀌지 않는지 확인
  - 삭제 클릭 시 선택한 항목만 삭제되는지 확인

## Phase2 스모크 (10분)

- 대상 URL:
  - `/dashboard/review?month=2026-03`
  - `/dashboard/package?month=2026-03`
  - `/admin/ops`

- 체크 1 (1초 확인 카드):
  - 리뷰 상단에 `1초 확인` 카드가 1건만 보이는지 확인
  - `맞아요` 클릭 시 증빙이 연결되고 다음 화면에서 반영되는지 확인
  - `아니에요` 클릭 시 매칭 상세 화면으로 이동하는지 확인
  - `나중에` 클릭 시 보류로 이동되고 보류 탭에서 확인 가능한지 확인

- 체크 2 (되돌리기):
  - `맞아요` 처리 직후 `되돌리기`로 직전 변경이 복구되는지 확인

- 체크 3 (완성률/남은 건수):
  - 리뷰 상단의 `완성률 xx% · 남은 것 n건`이 누락 탭 건수와 크게 어긋나지 않는지 확인
  - 분모 0 케이스에서 `증빙이 필요한 거래가 없어요` 문구가 뜨는지 확인

- 체크 4 (패키지 검증 리포트):
  - 패키지 화면에 `검증 리포트`(누락/중복/이상치 Top 5 + 요약)가 노출되는지 확인
  - 항목 문구가 `의심/참고` 톤으로 표시되는지 확인

- 체크 5 (ZIP 포함):
  - 패키지 다운로드 후 `00_세무사_요약/검증리포트.txt` 파일 포함 여부 확인

- 체크 6 (운영 지표):
  - `/admin/ops`에서 `최근 7일 매칭 실패율` 카드가 표본에 따라 `표본 부족` 또는 `%`로 표시되는지 확인

## Phase3 스모크 (15분)

- 대상 URL:
  - `/dashboard/nhis?month=2026-03`
  - `/dashboard`
  - `/admin/ops`

- 체크 1 (11월 상승 위험도 배지):
  - `/dashboard/nhis?month=2026-03`에서 완료율이 충분한 계정은 Hero에 `11월 상승 위험도` 배지가 보이는지 확인
  - 완료율이 낮거나 공식 기준 준비가 안 된 상태(`refs_valid=false`)에서는 배지가 자동으로 숨겨지는지 확인

- 체크 2 (금융소득 누적 트래커):
  - 가입유형 `지역가입자/모름` + 금융소득 `있음` 상태에서 `금융소득(이자+배당) 누적(추정)` 카드가 보이는지 확인
  - 구간 버튼 클릭 시 안내 문구가 바뀌어도 실제 입력값(`금융소득(연)`)이 자동 변경되지 않는지 확인

- 체크 3 (대시보드 월별 변화 그래프):
  - `/dashboard`의 `월별 변화` details를 열었을 때 그래프 3개(건보료/세금/증빙 완성률)가 조건부로 렌더링되는지 확인
  - 데이터가 부족한 그래프는 canvas 대신 `데이터가 쌓이면 보여드릴게요` 문구로 대체되는지 확인

- 체크 4 (무결성 게이트):
  - 공식 기준이 미준비 상태일 때 건보료 그래프가 숨김 처리되고 안내 문구로 대체되는지 확인
  - 세금 입력 근거가 부족한 달은 세금 그래프 점이 `null`로 처리되어 선이 끊기거나(렌더 정상) 숨김 처리되는지 확인

- 체크 5 (관리자 그래프/권한):
  - 관리자 계정으로 `/admin/ops` 접속 시 파싱 실패율/이용자 수/연동 계좌 그래프가 노출되는지 확인
  - 일반 사용자 계정으로 `/admin/ops` 접속 시 관리자 권한 차단이 유지되는지 확인

## Non-intrusive Guide 스모크 (10분)

- 대상 URL:
  - `/dashboard/calendar?month=2026-03`
  - `/dashboard/review?month=2026-03`
  - `/dashboard/tax-buffer?month=2026-03`
  - `/dashboard/nhis?month=2026-03`
  - `/dashboard/vault?month=2026-03`
  - `/dashboard/reconcile?month=2026-03`
  - `/dashboard/package?month=2026-03`
  - `/dashboard/guide?month=2026-03`

- PASS 조건:
  - 자동 팝업/모달/confirm이 뜨지 않는다.
  - 입력 중 포커스가 강제로 이동하지 않는다.
  - 사이드 가이드는 화면당 1개만 노출된다.
  - 스크롤 시 사이드 가이드가 계속 보인다(데스크톱: 고정, 태블릿/모바일: 상단 sticky).
  - 가이드가 없을 때 레이아웃 공백/점프가 없다.
  - 스크롤이 과도하게 늘어나지 않는다(모바일 포함).

- 화면별 확인:
  - `calendar`: `캘린더 화면 사용법` 사이드 가이드가 보이고 `/dashboard/guide#calendar`로 이동하는지 확인
  - `review`: `정리하기 사용법` 사이드 가이드가 보이고 `/dashboard/guide#review`로 이동하는지 확인
  - `tax-buffer`: `세금보관함 사용법` 사이드 가이드가 보이고 `/dashboard/guide#tax-buffer`로 이동하는지 확인
  - `nhis`: `건보료 화면 사용법` 사이드 가이드가 보이고 `/dashboard/guide#nhis`로 이동하는지 확인
  - `vault`: `증빙 보관함 사용법` 사이드 가이드가 보이고 `/dashboard/guide#vault`로 이동하는지 확인
  - `reconcile`: `대사 리포트 사용법` 사이드 가이드가 보이고 `/dashboard/guide#reconcile`로 이동하는지 확인
  - `package`: `패키지 화면 사용법` 사이드 가이드가 보이고 `/dashboard/guide#package`로 이동하는지 확인
  - `guide`: `핵심 이용 순서` + `#calendar/#review/#tax-buffer/#nhis/#vault/#reconcile/#package/#messages` 섹션이 보이는지 확인

## 다계좌/계좌기반 스모크 (15분)

- 사전 체크(필수):
  - `flask db upgrade`가 최신까지 적용되어 `user_bank_accounts`, `transactions.bank_account_id`, `bank_account_links.bank_account_id`가 존재해야 함
  - 미적용 시 `/dashboard/calendar`, `/dashboard/review`, `/inbox/import`에서 계좌 기능이 정상 동작하지 않음

- 대상 URL:
  - `/inbox/import`
  - `/dashboard/calendar?month=2026-03&account=all`
  - `/dashboard/calendar?month=2026-03&account=unassigned`
  - `/dashboard/day/2026-03-01?account=all`
  - `/dashboard/review?month=2026-03&account=all`
  - `/dashboard/review?month=2026-03&account=unassigned`
  - `/dashboard/review/evidence/<tx_id>/match?month=2026-03&focus=receipt_attach&q=&limit=30`

- 체크 1 (CSV/엑셀 계좌 감지):
  - CSV 또는 엑셀 파일 업로드 후 매핑 화면에 `감지된 계좌` 또는 `계좌를 확인하지 못했어요` 안내가 보이는지 확인
  - 계좌 미선택이어도 import commit이 정상 완료되는지 확인(미지정 허용)
  - 감지된 신규 계좌 추가 체크 시 거래에 계좌가 연결되는지 확인

- 체크 2 (캘린더 계좌 필터/합계):
  - `account=all/unassigned/<계좌ID>` 변경 시 월 합계(수입/지출/순)가 즉시 바뀌는지 확인
  - 월 셀 클릭 후 일자 화면으로 이동해도 `account` 파라미터가 유지되는지 확인
  - 일자 화면에서 수정/삭제/빠른추가 후에도 같은 `account` 스코프가 유지되는지 확인

- 체크 3 (계좌 색상):
  - 월 캘린더의 `계좌 색상 설정`에서 색상 저장 후, 해당 계좌 스코프에서 금액/배지 강조색이 반영되는지 확인
  - `all` 스코프에서는 기존 수입/지출 색상을 유지하는지 확인
  - `unassigned` 스코프에서는 회색 계열로 보이는지 확인

- 체크 4 (수동 입력 기본 계좌):
  - `/dashboard/tx/new` 또는 일자 빠른추가에서 계좌 미선택 저장 시 `기타(수동)` 계좌로 저장되는지 확인
  - 필요 시 다른 계좌로 변경 저장이 가능한지 확인

- 체크 5 (영수증 계좌 추천):
  - 영수증 매칭 화면에서 `추천 계좌` 배지 + 계좌 선택 드롭다운이 보이는지 확인
  - 추천은 기본값일 뿐, 사용자가 바꿔 저장할 수 있는지 확인

- 체크 6 (미지정 일괄 정리):
  - `/dashboard/review`에서 계좌 `미지정` 필터로 조회 후 항목 선택
  - `계좌 일괄 지정`으로 기존 계좌/미지정/새 계좌(별칭) 각각 적용이 되는지 확인
  - 적용 후 다른 거래가 덮어써지지 않고 선택한 거래만 변경되는지 확인

- 체크 7 (민감정보 노출):
  - UI/flash/리뷰/캘린더 어디에도 계좌번호 전체가 노출되지 않는지 확인
  - 표시는 `****1234` 또는 별칭+마스킹 형식인지 확인

## 다계좌 운영 스모크 (20분)

- 대상 URL:
  - `/dashboard/account#bank-management`
  - `/dashboard/calendar?month=2026-03&account=all`
  - `/dashboard/review?month=2026-03&account=all`
  - `/dashboard/reconcile?month=2026-03&account=all`
  - `/dashboard/package?month=2026-03`

- 체크 1 (계좌 관리 통합 화면):
  - 계좌 목록에서 별칭/색상 저장이 정상 반영되는지 확인
  - `위로/아래로` 후 캘린더/리뷰 계좌 드롭다운 순서가 유지되는지 확인
  - `숨김` 처리한 계좌가 기본 드롭다운에서 사라지고, 숨김 계좌 보기에서 복구 가능한지 확인

- 체크 2 (고급 병합/되돌리기):
  - `고급: 계좌 병합`에서 From/To 선택 후 병합 실행
  - 병합 후 리뷰/캘린더에서 거래가 기준 계좌로 이동했는지 확인
  - `마지막 병합 되돌리기`가 한 번 동작해 이전 상태로 복구되는지 확인

- 체크 3 (리뷰/대사 리포트 계좌 일관성):
  - 리뷰 항목에 `● 계좌` 배지가 노출되는지 확인
  - 리뷰 계좌 필터(`all/unassigned/<id>`) 변경 시 목록/카운트가 바뀌는지 확인
  - 대사 리포트에서도 같은 계좌 필터로 합계/누락/중복/이상치가 바뀌는지 확인
  - 리뷰에서 되돌리기(Undo) 실행 후에도 선택한 계좌 필터가 유지되는지 확인

- 체크 4 (패키지 계좌 컬럼):
  - 패키지 ZIP 다운로드 후 `세무사용_정리표.xlsx`의 `거래`, `거래_원본` 시트에 `bank_account` 컬럼 존재 확인
  - 값이 `별칭 + ****1234` 또는 `미지정`으로만 표시되는지 확인

- 체크 5 (민감정보 차단):
  - 계좌번호가 포함된 오류 문자열을 유도해도 화면/로그/flash에 원문 전체 계좌번호가 노출되지 않는지 확인
  - 영수증 파싱 결과(`parsed_json`)에 긴 숫자가 들어오면 `****1234` 형식으로 마스킹 저장되는지 확인

- 체크 6 (백필 스크립트):
  - Dry-run: `PYTHONPATH=. .venv/bin/python scripts/backfill_bank_accounts.py --dry-run --batch-size 100`
  - 실제 실행 전 체크포인트 경로/처리 대상 건수 확인
  - 실제 실행(필요 시): `PYTHONPATH=. .venv/bin/python scripts/backfill_bank_accounts.py --batch-size 100`
  - 출력 리포트(생성 계좌/채워진 거래/미지정 거래) 확인

- 체크 7 (세금보관함/홈 계좌 안내):
  - `?account=<id>`로 진입한 세금보관함에서 “전체 기준 유지” 안내가 보이는지 확인
  - 홈(`/dashboard`)에서 계좌 필터 배지 + `계좌 기준 보기(선택)` 링크가 노출되는지 확인

## 팝빌 연동 가이드 스모크 (10분)

- 대상 URL:
  - `/bank`
  - `/inbox/import`
  - `/bank?link_fail=quick_service&bank_code=0004&guide_bank=0004`

- 체크 1 (은행 선택 전):
  - `/bank` 진입 시 `은행별 빠른조회 가이드` 카드에서 상세 본문이 숨김 상태인지 확인

- 체크 2 (은행 선택 후):
  - 은행 선택 시 “이 은행은 먼저 빠른조회 등록이 필요해요.” 안내 노출
  - 개인/기업 전환 버튼(개인, 기업) 노출 여부 확인(둘 다 경로가 있을 때만)
  - 개인↔기업 전환 시 경로 단계가 바뀌는지 확인
  - 버튼 2개 노출 확인:
    - `은행에서 먼저 설정하기`
    - `지금은 CSV·엑셀로 먼저 시작하기`
  - 드롭다운 은행 목록이 공식 문서 기준 19개(0002, 0003, 0004, 0007, 0011, 0020, 0023, 0027, 0031, 0032, 0034, 0035, 0037, 0039, 0045, 0048, 0071, 0081, 0088)만 노출되는지 확인
  - 최소 검증 은행:
    - 국민은행(0004): 개인/기업 전환 확인
    - 농협은행(0011): 개인/기업 전환 확인
    - SC제일은행(0023): 기업 경로에 First Biz/Straight2Bank 문구 확인
    - 새마을금고(0045): 영업점/홈페이지 안내 문구 확인
    - 신한은행(0088): 회원가입 참고 노트 노출 확인

- 체크 3 (연동 실패 fallback):
  - 빠른조회 미등록 오류를 유도하거나 `?link_fail=quick_service&bank_code=0004&guide_bank=0004`로 접속
  - 같은 `/bank` 화면 안에서 fallback 블록이 뜨는지 확인(새 페이지 이동/모달 없음)
  - fallback 상태에서도 선택 은행 가이드가 그대로 유지되는지 확인
  - fallback 버튼(`은행에서 먼저 설정하기`, `지금은 CSV·엑셀로 먼저 시작하기`) 동작 확인

- 체크 4 (가이드 JSON 누락 내구성):
  - `data/reference/bank_quick_guide_ko.json` 임시 이름 변경 후 `/bank` 접속
  - 500 없이 기본 문구로 렌더링되는지 확인
  - 테스트 후 파일 원복

## 팝빌 백필/잔액 스모크 (15분)

- 대상 URL:
  - `/bank`
  - `/dashboard/review?month=2026-03`
  - `/dashboard/tax-buffer?month=2026-03`

- 체크 1 (버튼 노출/기본 동작):
  - `/bank`에서 계좌별 `최근 3개월 다시 가져오기` 버튼이 보이는지 확인
  - 동기화 ON 계좌에서만 버튼이 활성화되는지 확인
  - 상단 `동기화 새로고침` 버튼이 동일하게 최근 3개월 기준으로 동작하는지 확인

- 체크 2 (백필 범위 정책):
  - 동기화 후 flash 문구가 `팝빌에서 조회 가능한 최근 3개월`로 표시되는지 확인
  - 더 오래된 과거 거래는 새로 생기지 않고(중복/추가 없음), 최근 3개월 범위에서만 변화가 있는지 확인

- 체크 3 (부분 실패 내구성):
  - 한 구간 실패를 유도한 뒤에도 500 없이 `/bank`로 복귀하는지 확인
  - 문구가 `최근 3개월 중 일부 기간은 가져오지 못했어요`로 표시되는지 확인

- 체크 4 (잔액 표시):
  - 잔액이 있는 계좌: `잔액 1,234원` 형식으로 노출되는지 확인
  - 잔액이 없는 계좌: `잔액 미확인` 또는 `이 계좌는 현재 잔액 정보를 받지 못했어요`로 표시되는지 확인
  - 잔액이 비어 있어도 0원으로 잘못 표기되지 않는지 확인

- 체크 5 (GET 자동 백필 금지):
  - `/bank` 단순 새로고침(GET)만 반복 시 거래 수가 갑자기 증가하지 않는지 확인
  - 명시적 POST(`동기화 새로고침`, `최근 3개월 다시 가져오기`)에서만 백필이 실행되는지 확인

## day 상세 계좌 배지 스모크 (5분)
- `/dashboard/day/2026-01-08?account=all`에서 거래마다 `● 계좌표시명` 배지가 보이는지 확인
- `account=all`에서 거래별 배지 색이 각 계좌 색상과 일치하는지 확인
- `/dashboard/day/2026-01-08?account=unassigned`에서 배지가 모두 `미지정`(회색)인지 확인
- `/dashboard/day/2026-01-08?account=<계좌ID>`에서 해당 계좌 거래만 보이는지 확인
- day→month 이동 시 `account` 파라미터가 유지되는지 확인

## review account 필터 스모크 (5분)
- `.venv/bin/python scripts/review_account_filter_smoke.py` 실행
- `/dashboard/review?month=2026-03`가 200인지 확인
- `/dashboard/review?month=2026-03&focus=income_confirm&account=5`가 200인지 확인
- `account=not-a-number`, 매우 큰 숫자 account에서도 500 없이 200인지 확인
- account 필터가 잘못된 값이면 안전하게 `전체` 기준으로 렌더링되는지 확인

## 다계좌 UX 마감 스모크 (15분)
- `/dashboard/day/2026-01-08?account=all`에서 거래별 계좌 배지(`● 별칭 ****1234` 또는 `● 미지정`) 확인
- `/dashboard/day/2026-01-08?account=unassigned`에서 미지정 거래만 표시되고 배지가 회색인지 확인
- `/dashboard/review?month=2026-03&account=all`에서 상단 계좌 필터 + 거래행 계좌 배지 동시 확인
- `/dashboard/review?month=2026-03&focus=income_confirm&account=<id>`에서 focus/account가 함께 유지되는지 확인
- `/dashboard/reconcile?month=2026-03&account=all`에서 필터/합계/리스트 계좌 배지 확인
- `/dashboard/package?month=2026-03&account=all`에서 미리보기 누락 리스트의 계좌 표시 확인
- 패키지 ZIP(`세무사용_정리표.xlsx`)의 `bank_account` 컬럼이 `별칭 ****1234`/`미지정`만 포함하는지 확인
- `account=invalid` 같은 비정상 값으로 접근해도 500 없이 전체 기준으로 렌더링되는지 확인

## 플랜 권한 스모크 (15분)
- free 계정:
  - `/dashboard/nhis?month=2026-03`, `/dashboard/review?month=2026-03`, `/inbox/import` 접근이 모두 가능한지 확인
  - `/bank`에서 계좌 자동 연동 버튼이 비활성/차단되고, 안내 문구가 보이는지 확인
  - `/dashboard/package?month=2026-03`에서 미리보기는 가능하지만 ZIP 다운로드는 차단되는지 확인
- basic 계정:
  - `/bank`에서 `연결 가능 계좌: x / 1`로 보이는지 확인
  - 2번째 계좌 ON 시 서버에서 차단되는지 확인
  - `/dashboard/package/download?month=2026-03` 다운로드가 성공하는지 확인
- pro 계정:
  - `/bank`에서 `연결 가능 계좌: x / 2`로 보이는지 확인
  - 3번째 계좌 ON 시 서버에서 차단되는지 확인
- extra_account_slots=1:
  - basic은 최대 2개, pro는 최대 3개까지 ON 가능한지 확인
  - 차단/허용 결과가 UI가 아니라 서버 POST 결과로 일치하는지 확인

## 플랜 다운그레이드/free 접근 스모크 (10분)
- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/plan_review_receipt_evidence_smoke.py`
- 체크 항목:
  - free/basic/pro 모두 `/dashboard/review?month=2026-03` 접근 가능(200)
  - free/basic/pro 모두 `/inbox/import`, `/dashboard/vault?month=2026-03` 접근 가능(200)
  - free/basic/pro 모두 `POST /inbox/evidence/<id>/mark`, `POST /inbox/evidence/<id>/upload` 가 500 없이 처리(302)
- 다운그레이드 정책 확인:
  - 허용 수를 초과한 기존 ON 계좌는 조회/OFF 가능
  - 허용 수를 초과한 상태에서 신규 추가/재활성화(OFF->ON)는 차단
  - bank 화면에 “현재 연결된 계좌 수가 현재 플랜 허용 수를 초과” 안내가 노출

## Billing 하드닝 스모크 (15분)
- 환경 준비:
  - `TOSS_PAYMENTS_CLIENT_KEY`, `TOSS_PAYMENTS_SECRET_KEY`, `BILLING_KEY_ENCRYPTION_SECRET` 설정
  - `FLASK_APP=app.py .venv/bin/flask billing-startup-check`가 실패 없이 통과하는지 확인
- 스키마 가드:
  - billing/users 필수 컬럼 누락 환경에서 `billing-startup-check`가 명확한 오류를 출력하는지 확인
  - production/staging(`BILLING_GUARD_MODE=strict`)에서는 앱 시작이 hard fail 되는지 확인
- 등록 콜백 세션 의존성:
  - 로그인 없는 상태에서 `/dashboard/billing/register/success?...` 콜백이 500 없이 처리되는지 확인
  - 로그인 없는 상태에서 `/dashboard/billing/register/fail?...` 콜백이 500 없이 처리되는지 확인
- 민감값 로깅:
  - 콜백 접근 시 앱 로그에 `authKey=원문`/`paymentKey=원문`이 남지 않는지 확인
  - reverse proxy/APM에서 querystring 마스킹 또는 비기록 설정이 적용됐는지 확인
- 상태 정합성:
  - `billing_subscriptions.status='grace_started'` 저장 시 DB 체크제약 오류가 나지 않는지 확인

## Billing 멱등/정리 스모크 (10분)
- webhook 멱등:
  - 같은 payload로 `POST /api/billing/webhook`를 2번 호출
  - 첫 호출은 `duplicate=false`, 두 번째는 `duplicate=true`로 응답하는지 확인
- registration attempt 정리:
  - dry-run: `PYTHONPATH=. .venv/bin/python scripts/cleanup_billing_registration_attempts.py --dry-run`
  - 실제 실행: `PYTHONPATH=. .venv/bin/python scripts/cleanup_billing_registration_attempts.py`
  - 오래된 `registration_started`가 `canceled(abandoned)`로 정규화되고, 오래된 `failed/canceled` 정리가 가능한지 확인
- 키 버전:
  - `BILLING_KEY_ACTIVE_VERSION=v2`, `BILLING_KEY_ENCRYPTION_SECRET_V2=...` 설정 후 startup check 통과 확인
  - 활성 버전 키가 없으면 startup check가 명확히 실패/경고를 주는지 확인

## Billing Checkout 단계 스모크 (20분)
- 사전 조건:
  - 로그인 사용자 1명 준비 (free 또는 basic)
  - billing method 없음/있음 케이스를 각각 준비
- free → basic 최초 구독(자동 연결형):
  - `/pricing`에서 `베이직 시작하기` 클릭 (POST `/dashboard/billing/checkout/start`)
  - 결제수단이 없으면 등록 런치 화면(`register_start`)이 바로 렌더되고 토스 등록창 자동 시도가 시작되는지 확인
  - 등록 성공 후 `/dashboard/billing/register/success?...`가 `checkout/processing?intent=...`로 자동 이동하는지 확인
  - `processing`이 POST `/dashboard/billing/checkout/confirm`을 자동 호출하고, 중복 새로고침 시 추가 charge가 생기지 않는지 확인
- basic → pro 업그레이드:
  - `/pricing` 또는 `/mypage`에서 `프로 업그레이드` 클릭
  - 결제수단이 이미 있으면 등록 단계 없이 `checkout/confirm` 또는 `processing`으로 바로 이어지는지 확인
  - confirm 단계 중복 호출 시 동일 intent에서 추가 attempt가 생기지 않는지 확인
  - reconcile 후 `users.plan_code=pro`로 반영되는지 확인
- add-on proration:
  - `/bank` 또는 `/package`에서 추가 계좌 수량 입력 후 구매 시작
  - 결제 성공 후 원래 진입 페이지(`return_to`)로 복귀되는지 확인
  - reconcile 후 `users.extra_account_slots`가 결제 건당 1회만 증가하는지 확인
- 루프/복귀 UX 확인:
  - `ready_for_charge` intent가 `registration_required`로 강등되지 않는지 확인
  - 등록 성공 직후 `billing_methods.status='active'`가 1건 이상 유지되는지 확인 (0건이면 회귀)
  - `checkout_intents.billing_method_id`가 등록 성공한 method id로 채워졌는지 확인
  - `resolve_checkout_billing_method` 경로에서 intent-bound method가 비활성이어도 user active method fallback으로 confirm이 계속 진행되는지 확인
  - 결제 결과 토스트가 진입 페이지에서 1회만 노출되고, 새로고침 시 중복 노출되지 않는지 확인
  - `/dashboard/billing/payment/success` 새로고침은 읽기 전용이며 추가 write side effect가 없는지 확인

## Billing 데이터 오염 진단/복구 스모크 (10분)
- 진단(dry-run):
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --limit 300`
- 복구(dry-run):
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300`
- 복구(apply, 보수 모드):
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_recovery.py --limit 300 --apply`
- 적용 후 재진단:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_data_audit.py --limit 300`
- 필수 확인:
  - 자동 복구는 안전 케이스만 반영(`fixed_count`)
  - 애매한 케이스는 `manual_review`로 남는지 확인
  - `users.plan_code / plan_status / extra_account_slots`가 변경되지 않는지 확인

## Billing Postgres 동시성 스모크 (20분)
- 목적: sqlite가 아닌 Postgres에서 경합 상황 멱등 보장 확인
- 자동 테스트(DSN 제공 시):
  - `BILLING_PG_TEST_DSN='postgresql+psycopg://<user>:<pw>@<host>:5432/<db>' .venv/bin/python -m unittest tests.test_billing_concurrency_postgres`
- 수동/스크립트 검증:
  - `PYTHONPATH=. .venv/bin/python scripts/billing_pg_concurrency_probe.py --cleanup`
- 필수 PASS 항목:
  - 동일 order 성공 콜백 동시 2회에서 `exchange_calls=1`
  - 동일 transmission webhook 2회에서 `duplicate=false/true` 순으로 처리
  - success/fail 경합 후 최종 attempt 상태 `billing_key_issued`
  - 동일 source entitlement 로그 동시 반영 시 row 1건 유지
- 실패 시 점검:
  - `billing_method_registration_attempts`, `billing_methods`, `billing_payment_events`, `entitlement_change_logs`
  - unique constraint/트랜잭션 처리 여부 재확인

## Billing Recurring 자동화 스모크 (20분)
- 사전 조건:
  - 유효한 `billing_subscriptions` 1건 이상(상태 `active` 또는 `grace_started`)
  - 해당 구독에 `billing_methods.status='active'` 1건 연결
  - `FLASK_APP=app.py .venv/bin/flask billing-startup-check` 통과
- 대상 선정 점검:
  - `FLASK_APP=app.py .venv/bin/flask billing-run-recurring --dry-run --limit 20`
  - 결과 JSON의 `due_recurring_count`, `due_retry_count`, `skipped` 사유가 기대와 일치하는지 확인
- 정기청구 실행:
  - `FLASK_APP=app.py .venv/bin/flask billing-run-recurring --subscription-id <id>`
  - `billing_payment_attempts`에 `attempt_type=recurring` 또는 `retry`가 1건만 생성되는지 확인
  - 같은 사이클에서 반복 실행 시 중복 attempt가 추가되지 않는지 확인
- retry 실행:
  - `FLASK_APP=app.py .venv/bin/flask billing-run-retry --subscription-id <id>`
  - grace 대상이 아니면 실행이 건너뛰어지는지, grace 대상이면 `attempt_type=retry`가 기록되는지 확인
- grace 만료/past_due:
  - `FLASK_APP=app.py .venv/bin/flask billing-run-grace-expiry --dry-run`
  - `FLASK_APP=app.py .venv/bin/flask billing-run-grace-expiry --subscription-id <id>`
  - 만료 구독만 `past_due` 전환되고 `users.plan_status`가 projector를 통해 제한 상태로 반영되는지 확인
- 기간 종료 해지:
  - `FLASK_APP=app.py .venv/bin/flask billing-run-cancel-effective --dry-run`
  - `FLASK_APP=app.py .venv/bin/flask billing-run-cancel-effective --subscription-id <id>`
  - `cancel_effective_at` 지난 구독만 `canceled`로 전환되고 다음 주기 청구 대상에서 제외되는지 확인

## 20) Billing Staging E2E 준비 점검 (실측 전)
- 필수 env 존재 여부(값 출력 금지):
  - `python - <<'PY'\nimport os\nfor k in ['TOSS_PAYMENTS_CLIENT_KEY','TOSS_PAYMENTS_SECRET_KEY','BILLING_KEY_ENCRYPTION_SECRET','BILLING_KEY_ACTIVE_VERSION']:\n    print(k, 'SET' if os.getenv(k) else 'MISSING')\nPY`
- startup check:
  - `FLASK_APP=app.py .venv/bin/flask billing-startup-check`
- 결과 문서:
  - 체크리스트: `docs/BILLING_E2E_CHECKLIST.md`
  - 실측 결과: `docs/BILLING_E2E_RESULTS_STAGING.md`
  - 인프라 실측: `docs/BILLING_INFRA_VALIDATION.md`
  - 최종 판정: `docs/BILLING_GO_NO_GO_REPORT.md`

---

## 운영 리허설(2026-03-11)

### 1) billing startup check (실연결)
```bash
FLASK_APP=app.py .venv/bin/flask billing-startup-check
```
기대 결과: `billing startup check ok (mode=warn)`

### 2) DB 백업 리허설
- 결과 문서: `docs/DB_BACKUP_REHEARSAL_RESULTS.md`
- 생성 파일 예시: `reports/rehearsals/db_backup_rehearsal_YYYYMMDD_HHMMSS.dump`

### 3) DB 복구 리허설
- 결과 문서: `docs/DB_RESTORE_REHEARSAL_RESULTS.md`
- 임시 DB 생성 후 `pg_restore`/검증/삭제 절차 수행

### 4) 파일 백업/복구 리허설
- 결과 문서: `docs/FILE_BACKUP_RECOVERY_RESULTS.md`
- `uploads/evidence` 압축 백업 후 `/tmp` 경로 복원 검증

## 21) TAX/NHIS accuracy_level 실사용 분포 감사
- 목적:
  - 분모(코호트)별 `exact_ready / high_confidence / limited / blocked` 분포 확인
  - 관리자/테스트/비활성/레거시 제외 코호트 검증
- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_latest.json`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_post_input_recovery.json`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_post_completion_improvement.json`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_post_inline_save.json`
- 전체 사용자 스캔:
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_full.json`
- 단일 사용자 점검:
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --user-pk 123`
- 결과 해석:
  - `cohorts.<cohort_key>.tax/nhis.accuracy_level_distribution` 확인
  - 권장 분모: `recommended_distribution_cohort=operational_target_users`
  - `cohort_flag_counts`로 관리자/테스트/비활성 비중 확인
  - 분석 결과 문서는 `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`에 기록

## 22) TAX/NHIS 입력 Gap 리포트(자동 보완 가능성)
- 목적:
  - `auto_fillable / low_confidence_inferable / needs_user_input` 분포 집계
  - 자동 보완만으로 `high_confidence/exact_ready` 승급 가능한 비율 확인
- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py --limit 200 --output reports/accuracy_input_gap_latest.json`
- 전체 사용자 스캔:
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py --output reports/accuracy_input_gap_full.json`
- 결과 해석:
  - `tax.gap_bucket_distribution`, `nhis.gap_bucket_distribution` 확인
  - `needs_user_input_top_fields` 상위 항목을 입력 강제 우선순위로 사용
  - 결과 문서는 `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`에 기록

## 23) TAX blocked 원인 감사(최신: missing_income_classification)
- 목적:
  - 세금 blocked의 실제 상위 reason/입력 결손 경로 확정
  - 입력 미수집/미입력/저장상태/백필 가능성 분리
- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/tax_input_gap_audit.py --limit 300 --output reports/tax_input_gap_audit_post_completion_improvement.json`
- 결과 해석:
  - `field_presence_summary`로 `income_classification`/기납부세액 입력 보유율 확인
  - `blocked_missing_taxable_income`가 0인지 확인(이전 원인 해소 여부)
  - 결과 문서는 `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`, `docs/TAX_NHIS_REQUIRED_INPUTS.md`에 반영

## 24) NHIS blocked 원인 감사
- 목적:
  - NHIS blocked의 원인이 snapshot/guard/입력 중 어디인지 확정
- 실행:
  - `PYTHONPATH=. .venv/bin/python scripts/nhis_snapshot_gap_audit.py --limit 300 --output reports/nhis_snapshot_gap_audit_post_completion_improvement.json`
- 결과 해석:
  - `official_guard_status`, `nhis_ready_status`, `snapshot_runtime_status` 순서로 확인
  - `blocked_root_cause_distribution`으로 사용자 blocked 원인 확정
  - 결과 문서는 `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`에 반영

## 25) TAX/NHIS UI Guard 회귀 테스트
- 목적:
  - blocked/limited 상태에서 핵심 숫자 노출 가드가 템플릿에 유지되는지 고정
- 실행:
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_tax_nhis_result_meta tests.test_tax_nhis_ui_copy tests.test_tax_nhis_ui_guard_behavior`
- 결과 해석:
  - `overview/tax_buffer/nhis/package`의 blocked/limited 분기 유지 여부 확인

## 26) Input Recovery Card/CTA regression
- 목적
  - blocked/limited 사용자에게 인라인 저장 카드가 우선 노출되고, CTA는 fallback으로만 남는지 확인
- 명령
  - `.venv/bin/python -m unittest tests.test_input_recovery_cta tests.test_input_recovery_banner_priority tests.test_tax_nhis_ui_guard_behavior`
- 확인 포인트
  - `overview`: tax/nhis 인라인 1문항 저장 카드 우선 노출
  - `tax_buffer`: `tax_recovery_cta`/`nhis_recovery_cta` 복구 블록 + 인라인 저장 우선
  - `nhis`: `nhis_recovery_cta_ctx.show`일 때 가입유형 인라인 저장 카드 우선 노출

## 27) Input Recovery Flow (신규/기존 사용자) 회귀
- 목적
  - 기존 사용자 복구 플로우 + 신규 사용자 필수 입력 강제 플로우가 유지되는지 확인
- 명령
  - `.venv/bin/python -m unittest tests.test_tax_input_recovery_flow tests.test_nhis_input_recovery_flow tests.test_new_user_required_input_gate`
- 분포 재집계
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_post_input_recovery.json`
  - `PYTHONPATH=. .venv/bin/python scripts/tax_input_gap_audit.py --limit 300 --output reports/tax_input_gap_audit_post_input_recovery.json`
  - `PYTHONPATH=. .venv/bin/python scripts/nhis_snapshot_gap_audit.py --limit 300 --output reports/nhis_snapshot_gap_audit_post_input_recovery.json`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py --limit 300 --output reports/accuracy_input_gap_report_post_input_recovery.json`
  - `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --days 30 --limit 5000 --output reports/input_funnel_audit_post_inline_save.json`

## 28) 인라인 1문항 저장 퍼널 회귀
- 목적
  - 인라인 1문항 저장(세금/건보), 단계형 저장, 배너 우선 노출이 유지되는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_input_funnel_instrumentation tests.test_tax_inline_first_field_flow tests.test_nhis_inline_first_field_flow tests.test_tax_stepwise_completion_flow tests.test_input_recovery_banner_priority tests.test_tax_required_input_flow tests.test_nhis_required_input_flow tests.test_tax_nhis_ui_guard_behavior tests.test_tax_estimate_service tests.test_nhis_input_paths`
- 기대 결과
  - `Ran 49 tests ... OK`

## 29) 입력 완료율 개선 후 최신 집계
- 퍼널 집계
  - `PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py --days 30 --limit 5000 --output reports/input_funnel_audit_post_inline_save.json`
- 정확도 분포 재집계
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_level_audit.py --limit 300 --recent-active-days 90 --legacy-days 365 --output reports/accuracy_level_audit_post_inline_save.json`
- 원인/갭 재집계
  - `PYTHONPATH=. .venv/bin/python scripts/tax_input_gap_audit.py --limit 300 --output reports/tax_input_gap_audit_post_completion_improvement.json`
  - `PYTHONPATH=. .venv/bin/python scripts/nhis_snapshot_gap_audit.py --limit 300 --output reports/nhis_snapshot_gap_audit_post_completion_improvement.json`
  - `PYTHONPATH=. .venv/bin/python scripts/accuracy_input_gap_report.py --limit 300 --output reports/accuracy_input_gap_report_post_completion_improvement.json`
- 결과 문서 반영 위치
  - `docs/TAX_INPUT_FUNNEL_PLAN.md`
  - `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`
  - `docs/TAX_NHIS_99_ACCURACY_REPORT.md`

## 30) 캘린더 월별 세금 고정(이슈1) 회귀
- 목적
  - 캘린더 월 전환 시 세금 추정치가 조건부로 고정되는 버그 재발 방지
- 명령
  - `.venv/bin/python -m unittest tests.test_calendar_monthly_tax_bugfix tests.test_tax_estimate_service`
- 확인 포인트
  - `prefer_monthly_signal=False`에서는 기존 annual override 프록시 동작 유지
  - `prefer_monthly_signal=True`에서는 월별 거래 차이가 `buffer_target_krw` 차이로 반영
  - 월별 거래 입력이 같으면 동일값 허용

## 31) 세금 추정치 화면 간 일치 확인(캘린더/요약/정리하기/세금보관함)
- 목적
  - 같은 월 기준으로 핵심 세금 추정치가 주요 화면에서 같은 의미/값으로 표시되는지 확인
- 명령(실데이터 검증)
  - `PYTHONPATH=. .venv/bin/python - <<'PY'`
  - `... CASE_B(또는 테스트 계정)로 /dashboard/calendar, /dashboard/overview, /dashboard/review, /dashboard/tax-buffer 값을 비교 ...`
  - `PY`
- 확인 포인트
  - 캘린더/요약/정리하기/세금보관함이 같은 월 값 사용
  - blocked 월은 숫자 대신 입력 보완 문구가 우선 노출될 수 있음(정책상 정상)

## 32) 토스트-알림센터 브리지(이슈4) 회귀
- 목적
  - 중요한 토스트가 알림센터에도 적재되는지, 공용 notify 브리지 분기가 유지되는지 확인
- 명령
  - `.venv/bin/python -m unittest tests.test_notification_bridge tests.test_notification_center_render`
- 확인 포인트
  - `window.SafeToSpendNotify.notify` 공용 API 존재
  - `toast_only / toast_and_center / center_only` 분기 유지
  - NHIS/세금보관함/정리하기 토스트 브리지 호출 유지
  - 알림센터 렌더(title/level/source 메타) 유지

## 33) 세무사 패키지 첨부 파일명 규칙 회귀
- 목적
  - 세무사 패키지 ZIP의 첨부 파일명이 규칙형(`일시/금액/거래처/증빙종류/순번`)으로 생성되고 인덱스 참조가 일치하는지 확인
- 명령
  - `.venv/bin/python -m unittest tests.test_tax_package_attachment_filenames tests.test_tax_package_zip_contents`
- 확인 포인트
  - ZIP 내부 첨부 경로가 `YYYYMMDD_HHMMSS_금액원_거래처_증빙종류_순번.ext` 패턴인지
  - `attachments_index.xlsx`, `evidence_index.xlsx`의 `attachment_zip_path`가 실제 ZIP 경로와 일치하는지
  - fallback(`시간미상/금액미상/거래처미상/증빙`)과 충돌 방지 순번(`_001`, `_002`) 동작 유지

## 34) 영수증 비용처리 보강 플로우 회귀
- 목적
  - follow-up 답변 + 추가 보강 메모/파일 저장 + 3차 재평가 + 남은 부족 항목 표시가 유지되는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_expense_reinforcement_rules tests.test_receipt_expense_reinforcement_integration tests.test_receipt_expense_followup_rules tests.test_receipt_expense_followup_integration tests.test_receipt_expense_rules_engine tests.test_receipt_expense_rules_integration tests.test_receipt_expense_inline_explanations tests.test_receipt_expense_guidance_page tests.test_receipt_expense_guide_entrypoints`
- 확인 포인트
  - 거래처 식사 후보는 follow-up만으로는 즉시 승급하지 않고, 목적/참석자 보강 후에만 제한적으로 승급
  - 주말·심야 교통비는 업무 사유 메모가 있으면 summary/why 갱신 및 제한적 승급 가능
  - 개인 식비/경조사비/고가 자산은 보강 후에도 보수적 상태 유지
  - review / receipt confirm / receipt match / wizard partial에서 보강 폼과 기존 값 복원 유지
  - migration 적용 필요: `FLASK_APP=app.py .venv/bin/flask db upgrade`

## 35) 영수증 세금 체감 반영 + 숫자 애니메이션 회귀
- 목적
  - 영수증 판정 결과 중 `high_likelihood`만 실제 예상세금 계산에 반영되고, review / tax_buffer / calendar 숫자와 토스트가 같은 서버 계산 결과를 기준으로 갱신되는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_receipt_tax_effects tests.test_receipt_tax_effects_integration tests.test_tax_amount_animation_render tests.test_tax_estimate_service tests.test_tax_accuracy_cases tests.test_calendar_monthly_tax_bugfix`
- 확인 포인트
  - `ReceiptTaxEffectsSummary`에서 `high_likelihood`만 `reflected_expense_krw`로 집계
  - `needs_review`는 `pending_review_expense_krw`에만 들어가고 예상세금은 유지
  - `compute_tax_estimate(...)`가 `receipt_reflected_expense_krw`를 실제 경비로 더해 `tax_delta_from_receipts_krw`를 계산
  - review / tax_buffer / month 템플릿이 `data-tax-animate`, `data-tax-current-value`, `data-tax-previous-value`, `data-tax-changed`를 렌더
  - `base.html`의 `receipt_effect_toast=1` 브리지가 알림센터/토스트를 공용 경로로 처리

## 36) 영수증 세금 체감 반영 실브라우저 E2E
- 목적
  - follow-up / reinforcement 저장 이후 토스트 1회, review 숫자 갱신, tax_buffer / calendar 애니메이션, refresh 후 일관성을 실제 브라우저에서 검증
- 준비
  - 로컬 서버 실행:
    - `cd /Users/tnifl/Desktop/SafeToSpend && FLASK_APP=app.py .venv/bin/flask run --host 127.0.0.1 --port 5001 --no-debugger --no-reload`
  - 시드 생성:
    - `cd /Users/tnifl/Desktop/SafeToSpend && PYTHONPATH=. .venv/bin/python scripts/seed_receipt_tax_effects_e2e.py`
- 실행
  - `cd /Users/tnifl/Desktop/SafeToSpend && E2E_BASE_URL=http://127.0.0.1:5001 /Users/tnifl/node_modules/.bin/playwright test e2e/receipt-tax-effects.spec.ts --workers=1 --reporter=line`
- 확인 포인트
  - follow-up 저장 토스트 정확히 1회
  - reinforcement 저장 토스트 정확히 1회
  - review -> tax_buffer -> calendar 숫자 일관성
  - stale `receipt_effect_*` query param 제거
  - 값 무변경 케이스 `data-tax-changed=0`
  - reduced motion 환경 즉시 final value 반영
- 산출물
  - `reports/receipt_tax_effects_e2e_summary.json`
  - `reports/receipt_tax_effects_e2e_failures.json`
  - `docs/RECEIPT_TAX_EFFECTS_E2E_REPORT.md`

## 37) 자연스럽게 녹이기 1차 회귀
- 목적
  - 온보딩/overview/review/tax_buffer가 `결과 먼저, 입력은 나중` 구조와 생활 언어 카피를 유지하는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_natural_flow_entrypoints tests.test_natural_flow_progressive_questions tests.test_natural_flow_copy tests.test_new_user_required_input_gate tests.test_tax_nhis_ui_copy`
- 확인 포인트
  - 온보딩 저장 후 overview로 이동하는 결과 우선 흐름 유지
  - overview에 `결과 더 좋아지게 만들기` 카드 유지
  - review/tax_buffer의 세금 정확도 CTA가 `tax_profile step=2` 기본 입력으로 연결
  - 전면 카피가 `돈 받을 때 3.3%가 떼이는지`, `일하면서 쓴 비용`, `아직 검토가 필요해요` 같은 생활 언어 기준 유지

## 38) 시즌성 UX 1차 회귀
- 목적
  - 5월/11월/비시즌 상태에 따라 overview/review/tax_buffer/package가 시즌 체크리스트와 맥락 카피를 안정적으로 노출하는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_state_logic tests.test_seasonal_ux_render tests.test_seasonal_ux_copy tests.test_natural_flow_entrypoints tests.test_natural_flow_progressive_questions tests.test_natural_flow_copy tests.test_tax_single_step_flow tests.test_receipt_expense_guide_entrypoints`
- 확인 포인트
  - 날짜별 시즌 판정이 `may_filing_focus / november_prepayment_focus / off_season`으로 안정적으로 나뉘는지
  - overview에 시즌 체크리스트가 improvement 카드보다 먼저 노출되는지
  - review / tax_buffer / package에 작은 시즌 컨텍스트 블록이 붙는지
  - 시즌성 카피가 `작년 수입과 비용 정리`, `상반기 기준 미리 점검`, `이미 빠진 세금 확인` 같은 생활 언어 기준을 유지하는지

## 39) 시즌 카드 계측 / 퍼널 감사
- 목적
  - 시즌 카드의 shown / clicked / landed / completed가 실제로 기록되고, 카드별 퍼널 JSON을 해석 가능한 형태로 내보내는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_metrics_events tests.test_seasonal_ux_metrics_completion tests.test_seasonal_ux_render tests.test_seasonal_ux_state_logic tests.test_seasonal_ux_copy`
  - `PYTHONPATH=. .venv/bin/python scripts/seasonal_ux_metrics_audit.py --days 30 --limit 5000 --output reports/seasonal_ux_metrics_audit_latest.json`
- 확인 포인트
  - `ActionLog.before_state.metric_type=seasonal_ux` 이벤트가 `seasonal_card_shown / clicked / landed / completed`로 구분되는지
  - overview 시즌 허브 카드와 review / tax_buffer / package 시즌 컨텍스트 CTA에 `metric_cta_url`이 연결되는지
  - profile 저장, review follow-up / reinforcement 저장, tax_buffer adjust, package download에서만 completed가 발생하는지
  - 감사 JSON에 `overall / by_season / by_card / low_ctr_cards / low_completion_cards`가 생성되는지

## 40) 시즌 카드 데이터 해석 / 저위험 조정
- 목적
  - 실제 적재된 시즌 카드 퍼널을 다시 읽고, 데이터가 허용하는 범위 안에서만 카피/CTA를 보정했는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_priority_adjustments tests.test_seasonal_ux_render tests.test_seasonal_ux_copy tests.test_seasonal_ux_state_logic`
  - `PYTHONPATH=. .venv/bin/python scripts/seasonal_ux_metrics_audit.py --days 30 --limit 5000 --output reports/seasonal_ux_metrics_audit_latest.json --interpretation-output reports/seasonal_ux_metrics_interpretation.json`
- 확인 포인트
  - 데이터가 부족하면 priority 숫자를 유지하고, 근거 있는 CTA/anchor 조정만 반영하는지
  - `offseason_accuracy` CTA가 `3.3%·빠진 세금 확인하기`로 구체화됐는지
  - review / tax_buffer / package same-screen CTA가 실제 작업 영역 anchor로 연결되는지
  - `reports/seasonal_ux_metrics_interpretation.json`에 `has_enough_data`, `cards_needing_copy_review`, `cards_needing_completion_friction_review`가 생성되는지

## 41) 시즌 카드 수동 priority 미세조정
- 목적
  - 자동 추론을 넣기 전에 off-season 카드의 same-screen friction만 줄이고, priority는 소폭만 조정했는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_priority_adjustments tests.test_seasonal_ux_render tests.test_seasonal_ux_copy`
- 확인 포인트
  - `offseason_monthly_review` priority가 `1 -> 0`으로만 미세 상향되고 카드 순서가 뒤집히지 않는지
  - review CTA가 `반영 대기 항목부터 정리하기` + `#review-worklist`로 연결되는지
  - tax_buffer CTA가 `예상세금·보관액 바로 보기` + `#tax-buffer-kpis`로 연결되는지
  - package CTA가 `세무사 보내기 전 마지막 점검 보기` + `#package-readiness`로 연결되는지

## 42) 시즌 카드 저위험 자동 추론 강화 v1
- 목적
  - 수동 micro-priority를 기본값으로 유지한 채, 허용된 상태 신호만으로 priority를 한 단계 이내에서 설명 가능하게 조정하는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_seasonal_ux_inference_v1 tests.test_seasonal_ux_render tests.test_seasonal_ux_priority_adjustments tests.test_seasonal_ux_state_logic tests.test_seasonal_ux_copy`
- 확인 포인트
  - `receipt_pending_count / reinforcement_pending_count / tax_accuracy_gap / package_ready / receipt_pending_expense_krw`만 priority 조정에 사용되는지
  - 금지 신호(`guessed_withholding_from_patterns`, `guessed_vat_type`, `guessed_prepaid_tax_level`)를 넣어도 결과가 바뀌지 않는지
  - 카드 dict에 `priority_base`, `priority_effective`, `priority_adjustment_score`, `priority_adjustment_reason`가 남는지
  - priority 조정이 최대 한 단계 이내에서만 일어나는지

## 43) 공식 자료 안내 / 정책 문서 초안 회귀
- 목적
  - 홈택스/NHIS 공식 자료 안내 페이지, 서비스 진입점, 업로드 전후 고지 문구, 정책 문서 초안이 서로 어긋나지 않는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_official_data_guide_page tests.test_official_data_entrypoints tests.test_official_data_copy tests.test_official_data_policy_docs`
- 확인 포인트
  - `/guide/official-data`가 렌더되고 홈택스/NHIS 섹션과 공식 링크, 실패 시 메뉴 경로 문구가 보이는지
  - `overview`, `tax_buffer`, `nhis`에 공식 자료 안내 entrypoint가 붙어 있는지
  - 업로드 전/후 partial에 `왜 필요한지`, `무엇이 좋아지는지`, `기준일`, `다시 확인`, `핵심 추출값` 문구가 있는지
  - 정책 문서에 수집·이용 목적, 저장 단위, 보유기간, 파기, 안전조치, 거부 가능 범위가 정리돼 있는지

## 44) 공식 자료 업로드 v1 / parser 회귀
- 목적
  - 공식 자료 업로드 실제 기능이 whitelist 형식만 받고, parser registry와 v1 parser가 fail-closed로 동작하는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_official_data_upload_model tests.test_official_data_parser_registry tests.test_official_data_parsers tests.test_official_data_upload_routes tests.test_official_data_guide_page tests.test_official_data_entrypoints tests.test_official_data_copy tests.test_official_data_policy_docs`
  - `PYTHONPATH=. .venv/bin/python -m py_compile domain/models.py migrations/versions/fb24c1d9e8a1_add_official_data_documents.py routes/web/guide.py routes/web/official_data.py services/official_data_extractors.py services/official_data_parsers.py services/official_data_parser_registry.py services/official_data_upload.py tests/test_official_data_upload_model.py tests/test_official_data_parser_registry.py tests/test_official_data_parsers.py tests/test_official_data_upload_routes.py`
  - `PYTHONPATH=. .venv/bin/python - <<'PY'`
    `from pathlib import Path`
    `from services.official_data_parser_registry import resolve_fixture_document`
    `from services.official_data_parsers import write_parser_smoke_report`
    `write_parser_smoke_report(fixture_paths=[Path('tests/fixtures/official_data/hometax_withholding_statement.csv'), Path('tests/fixtures/official_data/hometax_business_card_usage.xlsx'), Path('tests/fixtures/official_data/nhis_payment_confirmation.pdf'), Path('tests/fixtures/official_data/unknown_headers.csv'), Path('tests/fixtures/official_data/encrypted_notice.pdf'), Path('tests/fixtures/official_data/scanned_image_notice.pdf')], resolver=resolve_fixture_document, output_path=Path('reports/official_data_parser_smoke.json'))`
    `PY`
  - `FLASK_APP=app.py .venv/bin/flask db upgrade`
- 확인 포인트
  - `OfficialDataDocument` 스키마와 migration이 추가됐는지
  - CSV / XLSX / 텍스트 추출 가능한 PDF만 지원하고, 스캔/암호 PDF는 `unsupported`로 닫히는지
  - 홈택스 원천징수 자료, 사업용 카드 사용내역, NHIS 보험료 납부확인서 3종이 fixture 기준으로 `parsed` 되는지
  - 업로드 화면에서 원본 저장 기본값이 비활성 상태로 보이고, 결과 화면에 기준일/재확인 상태가 표시되는지

## 45) 공식 자료 효과 연결 v1 회귀
- 목적
  - 공식 자료 업로드/파싱 결과가 세금/NHIS 계산과 신뢰도 notice에 안전하게 연결되는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_official_data_effects_rules tests.test_official_data_effects_integration tests.test_official_data_effects_render tests.test_tax_estimate_service tests.test_tax_nhis_result_meta tests.test_official_data_upload_routes tests.test_official_data_guide_page tests.test_official_data_entrypoints tests.test_official_data_copy tests.test_official_data_policy_docs`
  - `PYTHONPATH=. .venv/bin/python -m py_compile services/official_data_effects.py services/risk.py services/nhis_runtime.py routes/web/calendar/tax.py routes/web/official_data.py tests/test_official_data_effects_rules.py tests/test_official_data_effects_integration.py tests/test_official_data_effects_render.py`
  - `PYTHONPATH=. .venv/bin/python /tmp/official_data_effects_smoke.py`
- 확인 포인트
  - parsed + fresh 공식 자료만 반영되는지
  - 홈택스 원천징수 자료가 세금 차감 입력값으로만 보정되는지
  - 사업용 카드 사용내역이 자동 비용 확정으로 연결되지 않는지
  - NHIS 자료가 계산값 덮어쓰기 대신 기준일/상태/참고금액으로 연결되는지
  - `overview`, `tax_buffer`, `official_data/result`에 공식 자료 effect notice가 렌더되는지

## 46) 공식 자료 법적 경계 / 신뢰등급 문서 회귀
- 목적
  - 공식 자료 기능 구현 전에 허용/금지/유보 범위, 저장 제한, 신뢰등급 정책이 문서와 테스트로 고정돼 있는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_official_data_legal_docs tests.test_official_data_trust_grade_policy`
- 확인 포인트
  - `docs/OFFICIAL_DATA_LEGAL_BOUNDARY_REPORT.md`와 `docs/OFFICIAL_DATA_LEGAL_MATRIX.md`에 `허용/금지/유보` 표와 항목이 있는지
  - `주민등록번호 전체`, `건강 상세정보`, `자동 조회/스크래핑/대리 인증`이 금지 또는 유보로 명시됐는지
  - `docs/OFFICIAL_DATA_TRUST_GRADE_POLICY.md`에 `A/B/C/D` 신뢰등급과 금지 표현 목록이 있는지
  - `구조 검증 완료는 기관 진위확인과 동일하지 않음`, `해시는 업로드 이후 무결성 추적 도구` 문구가 있는지
  - 다음 구현 티켓 시작 전 아래를 다시 확인
    - 업로드 파일 전체 기본 저장 금지
    - `기관 확인 완료` 자동 판정 금지
    - NHIS 파일 전체 저장 금지

## 47) 공식 자료 런타임 가드 / 리스크 인벤토리 회귀
- 목적
  - 문서로 정한 법적 경계를 실제 저장 경로와 화면 카피에서 강제하고 있는지 확인
- 명령
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_official_data_runtime_guards tests.test_official_data_risk_inventory_docs`
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_official_data_runtime_guards tests.test_official_data_risk_inventory_docs tests.test_official_data_parser_registry tests.test_official_data_parsers tests.test_official_data_upload_routes tests.test_official_data_effects_render tests.test_official_data_copy tests.test_official_data_legal_docs tests.test_official_data_trust_grade_policy`
  - `PYTHONPATH=. .venv/bin/python -m py_compile services/official_data_guards.py services/official_data_extractors.py services/official_data_parsers.py services/official_data_upload.py routes/web/official_data.py tests/test_official_data_runtime_guards.py tests/test_official_data_risk_inventory_docs.py`
- 확인 포인트
  - `needs_review` 경로가 긴 preview/text snippet을 저장하지 않는지
  - `payor_key`, `business_key`, `insured_key`가 raw 값 대신 해시/마스킹으로 축소되는지
  - NHIS payload에서 `member_type` 같은 불필요 항목이 기본 비저장인지
  - 공식 기관 verification 메타 없이는 A등급이 나오지 않는지
  - `result.html`, `official_data_effect_notice.html`, `routes/web/official_data.py`에 `진본`, `법적으로 보장`, `100% 정확`, `원본임을 보증` 표현이 없는지
  - `docs/OFFICIAL_DATA_RISK_INVENTORY.md`, `docs/OFFICIAL_DATA_REMEDIATION_PLAN.md`, `docs/OFFICIAL_DATA_RUNTIME_GUARDS_REPORT.md`에 즉시 수정 항목과 남은 리스크가 정리돼 있는지

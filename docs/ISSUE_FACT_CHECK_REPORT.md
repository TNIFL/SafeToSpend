# ISSUE_FACT_CHECK_REPORT

작성일: 2026-03-14  
검증 범위: 최신 로컬 코드베이스 기준(기능 수정 없이 사실 확인 전용)

## 0) 검증 방식
- 재현 스크립트: `scripts/repro_issue_fact_check.py`
- 퍼널 재집계: `scripts/input_funnel_audit.py`
- 실행 결과 파일:
  - `reports/issue_fact_check_probe.json`
  - `reports/input_funnel_audit_manual_validation.json`

실행 명령:

```bash
PYTHONPATH=. .venv/bin/python scripts/repro_issue_fact_check.py \
  --user-pk 1 \
  --months 2026-01,2026-02,2026-03 \
  --review-month 2026-03 \
  --output reports/issue_fact_check_probe.json

PYTHONPATH=. .venv/bin/python scripts/input_funnel_audit.py \
  --days 7 \
  --limit 5000 \
  --user-pk 1 \
  --output reports/input_funnel_audit_manual_validation.json
```

---

## 이슈 1) 캘린더 세금이 매달 27,225원으로 동일

1) 사용자 제보 내용  
- 캘린더에서 이번 달 세금이 월마다 똑같이 `27,225원`으로 보임.

2) 재현 절차  
- 사용자 `user_pk=1` 기준 `month=2026-01`, `2026-02`, `2026-03`로:
  - `compute_tax_estimate(...)` 결과 비교
  - `/dashboard/calendar?month=...` 렌더 문자열 비교

3) 실제 결과  
- 3개월 모두 계산 결과 `buffer_target_krw=27225`
- 3개월 모두 화면 표시 `27,225원`
- 재현 파일 근거: `reports/issue_fact_check_probe.json`

4) 기대 결과  
- 월별 거래/입력 변화에 따라 월별 표시값이 변해야 함(사용자 기대 기준).

5) 관련 코드 경로  
- `routes/web/web_calendar.py` (calendar에서 `compute_tax_estimate` 사용)
- `templates/calendar/month.html` (세금 추정치 렌더)
- `services/risk.py` (`compute_tax_estimate`)

6) 최종 판정  
- **재현됨**

7) 원인 후보  
- 월별 표시가 `limited_proxy` 계산 모드에서 동일 연간값 월환산에 의해 고정되는 경로.
- 과세소득 입력 소스(`taxable_income_input_source`)와 사용 연간값이 월별 동일.
- 월별 포함수입 값이 동일하게 들어가며 월별 거래 변동이 세금값에 충분히 반영되지 않는 조건.

8) 수정 난이도  
- **중간**

9) 우선순위  
- **상**

---

## 이슈 2) `/dashboard/review` 거래 상세 정보 부족

1) 사용자 제보 내용  
- 지출/수입에서 업체명, 시간, 금액, 계좌, 상대방 정보가 부족하게 보임.

2) 재현 절차  
- `month=2026-03` 대상 거래 샘플 조회.
- review 라우트 쿼리 필드, 템플릿 렌더 필드, 모델 필드 보유 여부 분리 확인.

3) 실제 결과  
- DB 샘플에는 `counterparty`, `memo`, `source`, `occurred_at`, `amount_krw`, `bank_account_id` 존재.
- review 목록 렌더는 `counterparty(or memo fallback)`, 시간, 금액, 계좌 배지 중심.
- `source`, `memo` 별도 노출은 제한적/미노출.

4) 기대 결과  
- 사용자가 거래 맥락(상대방/메모/출처/계좌)을 목록 단계에서 충분히 식별 가능해야 함.

5) 관련 코드 경로  
- `routes/web/calendar/review.py`
- `templates/calendar/review.html`
- `domain/models.py` (`Transaction`)

6) 최종 판정  
- **부분 재현됨**

7) 원인 후보  
- 데이터는 있으나 목록 UI가 핵심 일부만 보여주는 구조.
- 송신자/수취인 전용 구조화 필드 부재로 상대방 정보가 `counterparty/memo` 문자열에 의존.

8) 수정 난이도  
- **중간**

9) 우선순위  
- **중**

---

## 이슈 3) `focus=receipt_attach` 카카오톡 입력 시 무반응

1) 사용자 제보 내용  
- 카카오톡 전자영수증/거래내역 텍스트를 넣어도 아무 반응이 없음.

2) 재현 절차  
- `requirement=maybe`, `status=missing` 거래를 선택.
- `/dashboard/review/evidence/<tx_id>/upload`에 카카오톡 형태 텍스트 POST.
- step 전환 여부와 EvidenceItem 상태 확인.

3) 실제 결과  
- HTTP 200 응답.
- 업로드 후 확인 단계(step2) 마커 검출.
- EvidenceItem 상태 `attached`로 저장됨.

4) 기대 결과  
- 입력 후 저장/처리/다음 단계 전환 또는 오류 피드백이 있어야 함.

5) 관련 코드 경로  
- `templates/calendar/partials/receipt_wizard_upload.html`
- `routes/web/calendar/review.py` (`/review/evidence/<tx_id>/upload`)
- `services/evidence_vault.py`

6) 최종 판정  
- **미재현**

7) 원인 후보  
- 현재 샘플 문자열 기준으로는 정상 처리됨.
- 단, 일부 실패 케이스에서 partial 모달 내부 오류 피드백이 약해 체감상 “무반응”처럼 보일 가능성은 남음.

8) 수정 난이도  
- **낮음~중간** (재현 조건 특정 시)

9) 우선순위  
- **중**

---

## 이슈 4) 우측 토스트가 알림창(알림 센터)에 누적되지 않음

1) 사용자 제보 내용  
- 우측 상단 토스트는 뜨는데 알림창에는 기록되지 않음.

2) 재현 절차  
- 토스트 표시 경로와 알림센터 저장 경로를 코드 레벨로 분리 추적.
- notice 저장소(localStorage)와 page-local toast 연결 여부 확인.

3) 실제 결과  
- 알림센터는 `base.html`의 `pushNotice` + queue polling 흐름 중심.
- `nhis.html`의 `showToast`는 페이지 로컬 토스트 스택에만 표시.
- 토스트 -> 알림센터 브리지 로직 없음.

4) 기대 결과  
- 사용자 기대 기준으로는 토스트와 알림센터가 일관되게 이어져야 함.

5) 관련 코드 경로  
- `templates/base.html` (notice center, localStorage, poll queue)
- `templates/nhis.html` (local toast stack, showToast)

6) 최종 판정  
- **현재 설계상 분리됨**

7) 원인 후보  
- 알림센터 수집 범위가 queue/flash 중심으로 제한됨.
- 페이지별 토스트 공통 적재 브리지 미구현.

8) 수정 난이도  
- **중간**

9) 우선순위  
- **중**

---

## 이슈 5) 30분 자동 계좌 동기화 미작동

1) 사용자 제보 내용  
- 자동 동기화가 안 되고 수동 새로고침해야만 반영됨(기대: 30분 주기 자동).

2) 재현 절차  
- 자동 스케줄러/워커(cron, APScheduler, Celery 등) 존재 여부 확인.
- 수동 동기화 엔드포인트 존재 여부 확인.
- 플랜별 interval 값이 실행체와 연결되는지 확인.

3) 실제 결과  
- 수동 경로 `POST /bank/sync` 존재.
- 템플릿 문구도 버튼 기반 실행을 명시.
- 30분 주기 자동 실행체는 코드 경로에서 확인되지 않음.
- 플랜 interval은 `basic=240`, `pro=60` 값만 확인됨.

4) 기대 결과  
- 30분 주기 자동 동기화 실행체가 존재하고 실제로 주기 실행되어야 함.

5) 관련 코드 경로  
- `routes/web/bank.py`
- `templates/bank/index.html`
- `services/plan.py`
- `services/import_popbill.py` (스케줄러 직접 연결 미확인)

6) 최종 판정  
- **재현됨**

7) 원인 후보  
- 자동 동기화 실행체(스케줄러/워커) 미구현 또는 현 배포 실행경로 미연결.
- `sync_interval_minutes`는 표시/권한 메타 성격이며 주기 작업 트리거로 직접 사용되지 않음.

8) 수정 난이도  
- **중간~높음** (운영 배포/워커 구조 연동 범위에 따라 변동)

9) 우선순위  
- **상**

---

## 분류 요약 (문제 성격)
- 데이터/입력 경로 영향: 이슈 1
- UI 표시/정보 밀도: 이슈 2
- 미재현(조건 특정 필요): 이슈 3
- 설계 미연동(토스트 vs 알림센터): 이슈 4
- 자동화 미구현/미연결 가능성: 이슈 5

## 수정 우선순위 제안 (사실확인 기반)
1. 이슈 1 (세금 월별 동일값 고정 체감)
2. 이슈 5 (자동 동기화 기대 불일치)
3. 이슈 4 (토스트/알림센터 분리로 인한 사용자 혼란)
4. 이슈 2 (review 상세 정보 가독성/식별성)
5. 이슈 3 (현재 샘플 기준 미재현, 재현 조건 추가 수집 필요)

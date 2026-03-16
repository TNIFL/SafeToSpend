# TAX/NHIS 로직 감사 보고서 (Audit)

- 작성일: 2026-03-11 (KST)
- 범위: 현재 코드베이스의 세금/건강보험료 계산 로직 감사(구현 변경 없음)
- 제외: `.env`, `.git`, `.venv`, `.uploads`
- 원칙: 공식 출처 기반 대조, 코드 수정 금지(치명 오류 후보는 보고만)

---

## 2026-03-13 업데이트 (세금 입력 구조 재설계 + 재집계 반영)

- 후속 보정 작업(코드 수정 + 회귀 테스트 + 오차 측정)이 완료됨.
- 최신 결과 문서:
  - `docs/TAX_INPUT_FLOW_REDESIGN.md`
  - `docs/TAX_NHIS_ACCURACY_PLAN.md`
  - `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`
  - `docs/TAX_NHIS_INPUT_RECOVERY_PLAN.md`
  - `docs/TAX_NHIS_REQUIRED_INPUTS.md`
  - `docs/TAX_NHIS_99_ACCURACY_REPORT.md`
- 핵심 상태:
  - 세금: 기본 입력/고급 입력 분리 완료, 실사용 분포는 `blocked 95(97.94%)`, `limited 2(2.06%)`, `exact/high 0`.
  - 건보: guard mismatch 복구 유지, 최신 분포 `blocked 61(62.89%)`, `limited 28(28.87%)`, `high 8(8.25%)`.
  - 내부 판정 레벨 추가: `exact_ready`, `high_confidence`, `limited`, `blocked`.
  - 표준 reason 코드 기준 최신 병목:
    - TAX: `missing_income_classification`, `proxy_from_annual_income`
    - NHIS: `missing_membership_type`, `missing_non_salary_income`
- 주의:
  - 아래 `티켓 1~5` 본문은 최초 감사 시점 기록(히스토리)이다.
  - 최신 운영 판정은 본문 하단의 “최종 보정 보고서 (1차+2차 반영)” 및 `docs/TAX_NHIS_99_ACCURACY_REPORT.md`를 우선한다.

## 2026-03-14 업데이트 (입력 완료율 퍼널 계측 + 단계형 저장 반영)

- 최신 결과 문서:
  - `docs/TAX_INPUT_FUNNEL_PLAN.md`
  - `docs/TAX_NHIS_ACCURACY_DISTRIBUTION.md`
  - `docs/TAX_NHIS_INPUT_RECOVERY_PLAN.md`
  - `docs/TAX_NHIS_INPUT_STRATEGY.md`
  - `docs/TAX_NHIS_99_ACCURACY_REPORT.md`
- 핵심 수치(최근 재집계):
  - TAX: `blocked 95(97.94%)`, `limited 2(2.06%)`, `exact/high 0`
  - NHIS: `blocked 61(62.89%)`, `limited 28(28.87%)`, `high 8(8.25%)`
  - 퍼널: `tax_recovery_cta_shown 7`, `tax_recovery_cta_clicked 0`, NHIS 퍼널 이벤트 0
- 현재 결론:
  - 구조 보강은 완료됐고 병목은 입력 저장 전환율이다.
  - 다음 우선순위는 산식 추가가 아니라 CTA 클릭/단계 저장/복구 완료 전환 개선이다.

---

## 티켓 1
### 1) 변경 대상 파일
- 코드 변경 없음
- 문서 작성: `docs/TAX_NHIS_LOGIC_AUDIT.md`

### 2) 문제 원인
- 세금/건보료 값이 여러 라우트/서비스에서 조합되어 표시되어, 실제 계산 주체와 의미(추정/공식)가 한 번에 보이지 않음.

### 3) 수정 목표
- 세금/건보료 계산 경로를 코드 기준으로 전수 추적하고, 입력/공식/출력 의미를 분리.

### 4) 구현 요구사항 (수행 결과)
- 키워드 전수 검색(`tax`, `estimate`, `nhis`, `health`, `tax_buffer`, `recommended`, `건보`, `세금`) 수행.
- 핵심 계산 경로 확인:
  - 세금 핵심:
    - `services/risk.py:322` `compute_tax_estimate`
    - `services/tax_official_core.py:18` `compute_tax_official_core`
    - `services/reference/tax_reference.py:41` (2026 세율 스냅샷)
  - 건보 핵심:
    - `services/nhis_runtime.py:22` `compute_nhis_monthly_buffer`
    - `services/nhis_estimator.py:562` (regional), `:729` (employee), `:889` (dependent)
    - `services/nhis_rules.py:92` (11월 사이클 포함 반영연도 규칙)
    - `services/reference/nhis_reference.py:51` (2026 스냅샷)
  - UI/라우트:
    - `routes/web/calendar/tax.py:53`
    - `templates/calendar/tax_buffer.html:89`
    - `routes/web/profile.py` + `templates/nhis.html`
- 3개 출력값 분리:
  - 세금 금고 권장액: `TaxEstimate.buffer_target_krw` (`services/risk.py:531`)
  - 월 예상 납부세액: `TaxEstimate.tax_due_est_krw` (`services/risk.py:499`)
  - 예상 건강보험료: `compute_nhis_monthly_buffer` 반환 `amount` (`services/nhis_runtime.py:87`)

### 5) 완료 기준
- 계산 진입점/수식/입력/출력/표현을 코드 기준으로 식별 완료.

### 6) 검증 방법
- 사용 명령(요약):
  - `rg -n "compute_tax_estimate|compute_tax_official_core|compute_nhis_monthly_buffer|estimate_nhis_monthly_dict|tax_buffer|건강보험|예상 납부세액" services routes templates tests -S`
  - `nl -ba services/risk.py | sed -n '300,640p'` 등 파일 단위 확인.

### 7) 남은 리스크
- 세금 로직은 공식 코어를 두었지만, 실사용 입력 경로와의 연결이 약해 `공식 계산 불가`로 떨어질 가능성이 큼(티켓 3 상세).

---

## 티켓 2
### 1) 변경 대상 파일
- 코드 변경 없음
- 문서 반영: `docs/TAX_NHIS_LOGIC_AUDIT.md`

### 2) 문제 원인
- 비공식 요약 자료가 많아, 공식 출처를 고정하지 않으면 검증 결과 신뢰성이 떨어짐.

### 3) 수정 목표
- 공식 출처만으로 검증 기준을 고정하고, 항목별 대조 기준을 명확화.

### 4) 구현 요구사항 (수행 결과)
- 코드/문서 내 공식 출처 레지스트리 확인:
  - `services/official_refs/registry.py`
  - `docs/OFFICIAL_REFERENCE_REGISTRY.md`
  - `docs/REFERENCE_DATA.md`
- 공식 출처(검증 항목 연결):
  - 종합소득세 세율/누진공제:
    - 소득세법 제55조(법령정보): `https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661`
    - 국세청 세율표: `https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594`
  - 지방소득세:
    - 지방세법: `https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95`
  - 원천징수 3.3 관련:
    - 소득세법 제129조(원천징수세율): `https://www.law.go.kr/법령/소득세법/제129조`
    - 국세청 안내(사업소득 3% + 지방소득세 0.3): `https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7666&mi=2454`
  - 건강보험료율/장기요양:
    - 복지부 보도자료(2026 보험료율 7.19%, 장기요양 13.14%/0.9448%): `https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200`
  - NHIS 산정 구조:
    - 국민건강보험법 시행령 제41(반영연도): `https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493`
    - 국민건강보험법 시행령 제44(요율/점수당금액): `https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28`
    - 시행규칙 제44(금융소득 1,000만원 문턱), 별표8(전월세 평가식), 별표4(재산 점수표)

### 5) 완료 기준
- 계산 항목별 공식 출처와 비교 포인트 고정 완료.

### 6) 검증 방법
- 레지스트리/레퍼런스 문서 및 공식 URL 대조.
- 레퍼런스 감시 테스트:
  - `.venv/bin/python -m unittest tests.test_tax_reference_rules tests.test_nhis_reference_rules tests.test_reference_watchdog`
  - 결과: `Ran 14 tests ... OK`

### 7) 남은 리스크
- 일부 외부 공식 페이지는 형식 변경 가능성이 있어, 주기적 갱신 실패 시 런타임이 fallback/차단될 수 있음(이미 watchdog 존재).

---

## 티켓 3
### 1) 변경 대상 파일
- 코드 변경 없음
- 문서 반영: `docs/TAX_NHIS_LOGIC_AUDIT.md`

### 2) 문제 원인
- “월 예상 납부세액”이 실제 종합소득세 추정인지, 단순 월 추정인지 경계가 모호함.

### 3) 수정 목표
- 세금 로직을 공식 기준과 대조하여 일치/단순화/위험 구간 분리.

### 4) 구현 요구사항 (수행 결과)
- 코드 핵심 판독:
  - `compute_tax_estimate`는 실제 계산에서 `compute_tax_official_core` 결과를 월 분할해 사용 (`services/risk.py:446-454`).
  - 단, 과세표준 연간값 키가 profile에 있어야만 계산 가능 (`services/risk.py:432-444`).
  - profile 저장 경로(`services/onboarding.py:310-403`)에는 해당 키 입력 경로가 없음.
  - 계산 불가 시 세액/지방세/원천징수 모두 0으로 강제 (`services/risk.py:459-468`).
- 공식 코어 테스트:
  - `.venv/bin/python -m unittest tests.test_tax_official_core`
  - 벡터 일치: 13M/20M/100M 케이스 합치.
- 수치 대조(공식 코어 기준):
  - 과세표준 13,000,000 -> 국세 780,000 / 지방 78,000 / 합계 858,000
  - 20,000,000 -> 1,740,000 / 174,000 / 1,914,000
  - 48,000,000 -> 5,940,000 / 594,000 / 6,534,000
  - 100,000,000 -> 19,560,000 / 1,956,000 / 21,516,000
- 분류:
  - 일치:
    - 누진세율+누진공제 자체 계산(`tax_official_core`)은 공식 구조와 일치.
    - 지방세 10% 적용 구조(표준세율 가정) 일치.
  - 단순화 허용:
    - 월 세액은 연 세액을 12로 분할한 추정.
    - 원천징수 3.3은 profile/키워드 기반 추정 차감.
  - 위험/불일치:
    - **실사용 입력 부재로 공식 계산 불가(0원) 가능성 큼**.
    - `services/tax_package.py:1098`는 패키지 산출에서 `income_included_total * tax_rate` 단순식 사용(대시보드 공식 코어와 불일치).
    - 부가세(VAT) 별도 계산은 세금 추정식에서 사실상 미반영(`tax_type` 입력은 있으나 산식 반영 약함).

### 5) 완료 기준
- 세금 로직의 정확도 목표를 “정식 신고값”이 아닌 “입력 의존 추정치”로 판정.

### 6) 검증 방법
- 코드 라인 대조 + 공식 코어 테스트 + 숫자 벡터 실행.

### 7) 남은 리스크
- 사용자에게 “실제 납부세액”처럼 읽히면 위험.
- 패키지용 세금 목표값과 대시보드 세금 목표값이 다르게 보일 수 있음.

---

## 티켓 4
### 1) 변경 대상 파일
- 코드 변경 없음
- 문서 반영: `docs/TAX_NHIS_LOGIC_AUDIT.md`

### 2) 문제 원인
- 건보료는 가입유형/소득반영연도/재산/금융소득/고지이력 등에 따라 산식이 크게 달라져 단순 비례식 여부 확인이 중요.

### 3) 수정 목표
- NHIS 로직을 공식 구조와 대조해 정확도 수준을 판정.

### 4) 구현 요구사항 (수행 결과)
- 코드 판독:
  - 지역가입자: 소득+재산+상하한+장기요양 (`services/nhis_estimator.py:562-726`)
  - 직장가입자: 월보수×보험료율×근로자부담 + 보수외소득(연 2천만원 초과) 보정 (`:729-874`)
  - 피부양자: 0원 처리 (`:889-928`)
  - 반영연도 규칙(1~10월 전전년도, 11~12월 전년도): `services/nhis_rules.py:92-103`
  - 금융소득 1,000만원 문턱 반영: `services/reference/nhis_reference.py:128-134`
  - 10원 절사 적용 (`_premium_krw` 경유) 구조 유지.
- 수치 예시 실행:
  - 지역(금융 9.9M): 20,160 + 2,640 = 22,800 (문턱 이하 제외 + 하한 적용)
  - 지역(금융 12M): 71,900 + 9,440 = 81,340 (문턱 초과 전액 포함)
  - 직장(월보수 3M): 107,850 + 14,170 = 122,020
  - 피부양자: 0
- 테스트:
  - `.venv/bin/python -m unittest tests.test_nhis_reference_rules tests.test_nhis_official_golden`
  - 결과(초기 감사 시점): reference_rules 통과, official_golden 1건 실패
  - 실패 항목(초기 감사 시점): `test_case_d_cap_clamp` (기대 4,591,740 vs 실제 4,579,750)
  - 최신 상태: 기대값 정정으로 해당 실패는 해소됨
    - 실제 계산에서 raw가 cap 미도달(4,579,750)로 확인.
- 분류:
  - 일치:
    - 지역/직장/피부양자 분기, 장기요양 분리, 반영연도 규칙, 금융소득 문턱 반영.
  - 단순화 허용:
    - 직장가입자 추정은 실제 회사부담/정산 시나리오를 완전 재현하지 않음.
    - 사용자 입력 부족 시 보수 fallback/고지이력 median 보정 사용.
  - 위험/불일치:
    - 골든 테스트 1건의 기대값-산식 불일치(테스트 벡터 또는 cap 검증 시나리오 재검토 필요).

### 5) 완료 기준
- 건보료 로직이 “공단 고지액 재현기”가 아니라 “공식 기반 추정기”임을 명확히 판정.

### 6) 검증 방법
- 코드 라인 대조 + NHIS 테스트 + 샘플 벡터 실행.

### 7) 남은 리스크
- 가입유형/입력 누락 상태에서 결과 신뢰도가 급락.
- 일부 사용자 상태(직장+사업 혼합/재산 상세)에서 실제 고지액과 차이가 커질 수 있음.

---

## 티켓 5
### 1) 변경 대상 파일
- `docs/TAX_NHIS_LOGIC_AUDIT.md`

### 2) 문제 원인
- 분석 결과를 바로 우선순위 액션으로 연결할 최종 판정이 필요.

### 3) 수정 목표
- A~H 구조의 최종 감사 결론 정리 및 수정 우선순위 제시.

### 4) 구현 요구사항 (최종 결과)

## A. 현재 계산 로직 요약
- 세금:
  - 목표: 월 추가 납부 예상세액(추정) + 세금 금고 권장액.
  - 엔진: `tax_official_core(연 과세표준 기반)` + 월 분할 + 원천징수(3.3) 추정 차감.
  - 차단조건: 연 과세표준 키 부재 시 공식 계산 불가(0원).
- 건강보험:
  - 목표: 월 예상 건보료(추정), 11월 전환 리스크 포함.
  - 엔진: 가입유형별 공식 기반 산식(지역/직장/피부양자), 반영연도 규칙/상하한/장기요양/금융소득 문턱 반영.
  - 입력 부족 시 fallback/고지이력 보정.

## B. 공식 기준 출처 목록
- 국세청 세율표: https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594
- 소득세법 제55조: https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661
- 소득세법 제129조(원천징수): https://www.law.go.kr/법령/소득세법/제129조
- 지방세법: https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95
- 복지부(2026 보험료율/장기요양): https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200
- 국민건강보험법 시행령 제41/44 및 시행규칙 제44/별표8/별표4:
  - https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493
  - https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
  - https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135095&gubun=

## C. 세금 로직 검증 결과
- 공식 코어 계산식(누진세율/누진공제/지방세 10%)은 공식 구조와 대체로 일치.
- 그러나 현재 앱 전체 동작 관점에서는:
  - 과세표준 연 입력이 없으면 공식 계산 차단(0원) -> 실사용 정확도 저하 가능성 큼.
  - 패키지 산출식이 대시보드 공식코어와 불일치(단순 비율식).
- 결론: “실제 신고세액 계산기”보다는 “입력 충족 시 공식 근사 + 미충족 시 차단형 추정”.

## D. 건강보험료 로직 검증 결과
- 지역/직장/피부양자 분기, 장기요양 분리, 연도 사이클, 금융소득 문턱, 상/하한 반영 등 핵심 구조는 공식 기준과 대체로 일치.
- 입력/상태 의존성이 커서 사용자 입력 부족 시 보수 fallback 비중이 증가.
- NHIS 골든 테스트 1건 불일치가 존재(검증 벡터 또는 기대값 갱신 필요).

## E. 코드와 공식 기준의 차이점
1. 세금 공식 계산 입력 전제(연 과세표준) 충족 실패 시 0원 반환.
2. 패키지용 세금 목표값은 단순 비율식(`income_included_total * tax_rate`) 사용.
3. 건보료는 공식 산식 기반이지만 실제 공단 부과 프로세스 전체(개별 예외/조정)를 완전 재현하지는 않음.

## F. 치명 오류 후보 / 단순화 허용 항목 / 표현 수정 필요 항목
- 치명 오류 후보
  - [상] 세금: 공식 계산 입력키 미연결로 `공식 계산 불가 -> 0원` 빈발 가능성.
  - [중] 건보: (초기 감사 시점) `tests.test_nhis_official_golden.test_case_d_cap_clamp` 실패 상태.
- 단순화 허용 항목
  - 월 추정치(연 세액/연 소득의 월 환산), 직장가입자 단순화, fallback 보정.
- 표현 수정 필요 항목
  - “예상 납부세액/건보료”가 실제 고지·신고 확정값처럼 해석되지 않도록 더 강한 고지 필요.

## G. 바로 수정해야 하는 우선순위
1. 상: 세금 공식 입력 경로 정합성(과세표준 입력키 수집/연결) 점검 및 보완.
2. 상: 패키지 산식과 대시보드 산식 불일치 정리(최소한 라벨/고지라도 일치화).
3. 중: NHIS 골든 테스트 실패 벡터 수정 또는 코드/기대값 재검증(현재 해소됨).
4. 중: UI 문구에서 “추정” 고지 강화(특히 세금 0원 차단 상태).

## H. 코드 수정 없이도 사용자에게 명시해야 하는 경고/고지 문구
- 세금:
  - “표시 금액은 추정치이며 실제 종합소득세·지방소득세 신고 결과와 다를 수 있습니다.”
  - “과세표준/공제 정보가 없으면 계산을 제한하거나 0원으로 표시될 수 있습니다.”
  - “원천징수 3.3% 반영은 입력/거래내역 기준 추정입니다.”
- 건보:
  - “표시 금액은 공단 고지액이 아닌 추정치이며, 가입유형·소득/재산 반영시점·공단 조정에 따라 달라질 수 있습니다.”
  - “직장가입자/피부양자/혼합 소득 상태에 따라 실제 부과액과 차이가 발생할 수 있습니다.”

### 5) 완료 기준
- A~H 완성, 공식 근거와 코드 근거를 분리해 판정.

### 6) 검증 방법
- 테스트/실행 근거:
  - `.venv/bin/python -m unittest tests.test_tax_official_core tests.test_nhis_reference_rules tests.test_nhis_official_golden`
    - 결과: `Ran 14 tests ... OK` (기존 NHIS cap 기대값 불일치 해소)
  - `.venv/bin/python -m unittest tests.test_tax_reference_rules tests.test_nhis_reference_rules tests.test_reference_watchdog`
    - 결과: `Ran 14 tests ... OK`
  - 샘플 벡터 실행(세금 4건, NHIS 5건)으로 수치 재확인.

### 7) 남은 리스크
- DB 실데이터 기준 영향 범위(예: 과세표준 입력키 실제 보유 사용자 수)는 현재 환경(Postgres 접근 제한)에서 직접 계수하지 못함.
- 법률 문구의 최종 대외 고지 문안은 별도 법률 검토 필요.

---

## 최종 판정 (초기 감사 스냅샷)
- 판정: **보수 추정 도구로는 사용 가능하나, 실제값으로 보이면 위험**
- 사유:
  - 세금 공식코어 자체는 정확하나 입력 연결 공백이 커서 실사용 정확도 변동이 큼.
  - NHIS는 공식 구조 반영 수준이 높지만 입력 의존 리스크가 존재.
- 주의:
  - 이 섹션은 초기 감사 시점 기록이다.
  - 최신 운영 판정은 아래 “최종 보정 보고서 (1차+2차 반영)” 및 `docs/TAX_NHIS_99_ACCURACY_REPORT.md`를 우선한다.

---

## 최종 보정 보고서 (1차+2차 반영)
- 작성일: 2026-03-12 (KST)
- 기준: 본 문서 상단 감사 결과 + 1차(계산 로직/테스트 보정) + 2차(상태 전달/고지 문구/회귀 테스트) 반영
- 참고: 아래 내용이 이전 “최종 판정” 섹션보다 최신 상태다.

### A. 현재 계산 로직 요약
- 세금
  - 핵심 엔진: `services/tax_official_core.py` (누진세율/누진공제/지방소득세 연동).
  - 월 추정: `services/risk.py::compute_tax_estimate`에서 연간 계산값을 월 단위로 환산.
  - 입력 경로: `services/onboarding.py`, `routes/web/profile.py`에서 과세표준 alias를 canonical key로 정규화/저장.
  - 상태 분기: `official_exact` / `limited_proxy` / `blocked`.
- 건보료
  - 핵심 엔진: `services/nhis_estimator.py` + `services/nhis_rules.py` + `services/reference/nhis_reference.py`.
  - 월 버퍼: `services/nhis_runtime.py::compute_nhis_monthly_buffer`.
  - 상태 분기: 공식 기준 준비 여부/입력 충족도/기준 데이터 상태를 바탕으로 `normal`/`limited`/`blocked` 메타 제공.

### B. 공식 기준 출처 목록
- 국세청 종합소득세율표: https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594
- 소득세법 제55조(세율): https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661
- 소득세법 제129조(원천징수): https://www.law.go.kr/법령/소득세법/제129조
- 지방세법(지방소득세): https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95
- 복지부(2026 보험료율/장기요양): https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200
- 국민건강보험법 시행령/시행규칙(반영연도/요율/금융소득/전월세/재산점수):
  - https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493
  - https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
  - https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135095&gubun=

### C. 세금 로직 검증 결과
- 개선 완료
  - 공식 입력 alias 정규화/저장 경로 보강(`official_taxable_income_annual_krw` 중심).
  - 입력 부족 시 조용한 0원 대신 내부 상태(`limited`/`blocked`)와 사유를 명시.
  - 패키지 세금 수치를 대시보드와 같은 추정 서비스 결과로 통일(실패 시 명시적 fallback 라벨).
- 현재 판단
  - 공식 입력이 있으면 공식 기준 추정 경로가 일관되게 동작.
  - 공식 입력이 없으면 제한 추정으로 동작하며, 상태/사유를 UI에 전달.

### D. 건강보험료 로직 검증 결과
- 개선 완료
  - 골든 테스트 실패 1건(`test_case_d_cap_clamp`) 원인 확정: cap 기대값 오류(실제 raw 미도달).
  - employee cap 경계 테스트(직전/도달/초과) 추가.
  - 런타임/화면에 `nhis_result_meta`(정상/제한/차단 + 사유) 전달.
- 현재 판단
  - 공식 구조(가입유형 분기, 반영연도, 상하한, 장기요양, 금융소득 문턱) 반영은 유지.
  - 입력/기준 데이터 상태에 따른 제한 추정 가능성은 명시적으로 전달됨.

### E. 코드와 공식 기준의 차이점
1. 세금/건보 모두 “월 추정치”이며 실제 신고/고지 확정 프로세스를 완전 재현하지 않는다.
2. 세금은 과세표준/공제 입력이 부족하면 제한 추정 또는 계산 제한으로 동작한다.
3. 건보는 공단 개별 조정(감면/자격 변동/특수 사례)까지 완전 재현하지 않는다.

### F. 치명 오류 후보 / 허용 가능한 단순화 / 표현 수정 필요 항목
- 치명 오류 후보
  - 현재 기준으로 확인된 치명 실패 케이스 없음(기존 NHIS 골든 실패 1건 해소).
- 허용 가능한 단순화
  - 월 환산 추정, 원천징수 3.3 입력/거래 기반 추정, 일부 fallback 추정.
- 표현 수정 필요 항목
  - 추가 UI 리디자인은 미수행. 그러나 핵심 화면(세금보관함/건보료/overview/package)에 추정·제한 고지는 반영됨.

### G. 수정 우선순위 (상/중/하)
- 상
  - 과세표준 입력 UX 노출/완성률 개선(현재는 경로는 연결됐지만 입력 유도는 제한적).
  - NHIS 입력 완료율 향상(가입유형/소득/재산 누락 시 제한 추정 빈도 감소 필요).
- 중
  - 상태 메타를 다른 계산 노출 화면(`review`, `month` 등)까지 일관 확장.
  - fallback 발생 빈도 모니터링(로그/지표화).
- 하
  - 문구 톤/배치 개선(의미 유지 전제).

### H. 코드 수정 없이도 즉시 넣어야 할 사용자 고지 문구
- 세금
  - “이 금액은 신고 확정세액이 아닌 추정치예요.”
  - “입력이 부족하면 0원 또는 실제보다 낮게 보일 수 있어요.”
  - “원천징수 3.3 반영은 입력/거래 내역 기반 추정이에요.”
- 건보
  - “건보료는 공단 고지액이 아닌 추정치예요.”
  - “가입유형/소득 반영시점/재산 기준/감면 여부에 따라 실제와 차이가 날 수 있어요.”

### I. 이번 보강으로 개선된 점
1. 세금 계산이 실제 사용자 입력 경로와 연결되어 공식/제한/차단 상태가 분리됨.
2. package/dashboard 세금 의미 불일치가 해소됨(공통 계산 서비스 사용).
3. NHIS 골든 실패 1건 원인 확정 및 테스트 정정 완료.
4. UI에서 숫자와 함께 신뢰 수준/제한 사유를 함께 전달하도록 보강됨.
5. 회귀 테스트가 계산 로직 + 상태 메타 + 문구 존재를 함께 보호함.

### J. 아직 남는 한계
1. 추정 도구 특성상 실제 신고세액/공단 고지액과 오차는 구조적으로 남는다.
2. 입력값이 부족한 사용자군에서는 제한 추정 비중이 높을 수 있다.
3. 모든 화면에 상태 메타가 전면 확장된 것은 아니므로, 단계적 확장이 필요하다.

### 최종 판정 (3축)
1. 세금 로직: **조건부 신뢰**  
   - 공식 입력 충족 시 신뢰 가능, 미충족 시 제한 추정(상태 고지 전제).
2. 건보료 로직: **조건부 신뢰**  
   - 공식 구조 반영은 안정적이나 입력/기준 데이터 상태에 따라 제한 추정 가능.
3. 제품 표현: **추정 도구로 운영 가능**  
   - 핵심 화면에 추정/제한 고지가 반영되어 “확정값 오해” 리스크를 낮춤.

### 검증 결과(최신)
- `.venv/bin/python -m unittest tests.test_tax_required_inputs tests.test_tax_estimate_service tests.test_tax_nhis_result_meta tests.test_nhis_input_paths tests.test_nhis_required_inputs tests.test_nhis_reference_rules tests.test_nhis_official_golden tests.test_tax_accuracy_cases tests.test_tax_nhis_ui_copy`
- 결과: `Ran 64 tests ... OK`

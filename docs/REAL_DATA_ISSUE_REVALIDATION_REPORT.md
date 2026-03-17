# REAL_DATA_ISSUE_REVALIDATION_REPORT

작성일: 2026-03-14  
검증 목적: 기존 5개 이슈를 실제 로컬 DB 패턴 기반으로 재검증(수정 아님, 사실 재확인)  
검증 범위: 익명화 테스트 계정 CASE_A~CASE_G (총 7개)

## 1) 검증 방식
- 실제 로컬 DB 패턴 조사 후 익명화 테스트 계정 생성.
- 원본 사용자 데이터는 수정하지 않고 복제본만 검증.
- 보고서에는 실제 user_pk/원본 식별정보 미노출.

실행 명령:

```bash
PYTHONPATH=. .venv/bin/python scripts/build_issue_test_accounts.py \
  --create \
  --output reports/real_data_issue_revalidation_matrix.json

PYTHONPATH=. .venv/bin/python scripts/revalidate_real_data_issues.py \
  --matrix reports/real_data_issue_revalidation_matrix.json \
  --summary reports/real_data_issue_revalidation_summary.json
```

산출 파일:
- `reports/real_data_issue_revalidation_matrix.json`
- `reports/real_data_issue_revalidation_summary.json`

---

## 2) 실데이터 패턴 조사 결과 (티켓 1)
- 전체 사용자 수: 98
- 거래 보유 사용자 수: 17
- 거래 수 분포: min 1 / p50 8 / p75 146 / p90 156 / max 395
- 계좌 연동(계좌 또는 링크 보유) 사용자 비율: 17.65%
- Evidence 보유 사용자 비율: 94.12%
- receipt_attach 가능(`maybe+missing`) 사용자 비율: 70.59%

필드 보유율(거래 가중 평균):
- memo: 97.88%
- counterparty: 86.26%
- source: 100%
- account 연결(`bank_account_id`): 48.52%
- occurred_at: 100%

---

## 3) 테스트 계정 설계/생성 결과 (티켓 1~2)

### 케이스 설계
- CASE_A: 월별 거래량 편차 큼
- CASE_B: 계좌 2개 이상 연동
- CASE_C: memo/counterparty 풍부
- CASE_D: detail 필드 빈약
- CASE_E: receipt_attach 검증 가능
- CASE_F: 거래량 많음
- CASE_G: 거래량 적음

### 생성 결과
- 생성 계정 수: 7
- 익명화 원칙:
  - 이메일: `qa_issue_case_*@example.test` 패턴
  - counterparty/memo: 토큰화 치환
  - 계좌 식별값: 일반화/해시 치환
  - 원본 사용자/거래/증빙 레코드: 수정 없음

---

## 4) 이슈별 재검증 결과

## 이슈 1) 캘린더 세금 월별 동일값(27,225 등)
- 테스트 케이스 수: 7
- 재현 케이스 수: 3
- 재현 비율: 42.86%
- 패턴: **특정 조건에서만 재현**
- 특징: 재현 3건 모두 “월별 거래 편차가 있는데도 월 세금이 고정”.

케이스별:
- 재현: CASE_A, CASE_B, CASE_E
- 미재현: CASE_C, CASE_D, CASE_F, CASE_G

판정: **부분 재현됨**

---

## 이슈 2) `/dashboard/review` 상세 정보 부족
- 테스트 케이스 수: 7
- 분류 분포:
  - UI 누락 중심: 5
  - 혼합형: 2
  - 원천데이터 부족 중심: 0

관찰:
- 다수 케이스에서 DB 필드 밀도는 높은데(`memo/counterparty/source`), review 표시는 핵심 일부 위주.
- CASE_D처럼 `counterparty` 자체가 약한 케이스도 있어 혼합형 존재.

판정: **부분 재현됨 (UI 누락 중심 우세)**

---

## 이슈 3) receipt_attach 카카오톡 텍스트 무반응
- 테스트 가능 케이스: 5 (CASE_A/CASE_F는 대상 거래 부족으로 판단불가)
- 샘플 문자열: 3종(전자영수증형/거래확인형/요약형)
- 실패/부분실패 케이스: 0
- 실패 샘플 분포: 없음

판정: **미재현**

비고:
- 본 재검증 범위에서는 “submit/처리/step 전환/상태 변경” 모두 확인됨.

---

## 이슈 4) 토스트와 알림센터 미연동
- 테스트 케이스 수: 7 (화면 접근 확인)
- 코드 경로 확인:
  - base notice 저장: 존재(`pushNotice`, queue polling)
  - nhis page toast: 존재(`showToast`)
  - nhis -> pushNotice 브리지: 없음

판정: **현재 설계상 분리됨**

---

## 이슈 5) 계좌 자동동기화(30분) 미작동
- 테스트 케이스 수: 7
- 연동 계좌 보유 케이스: 2 (CASE_B, CASE_E)
- 연동 케이스 재현: 2/2 (100%)

코드 확인:
- 수동 동기화 라우트: 있음 (`POST /bank/sync`)
- 수동 실행 안내 문구: 있음
- 주기 스케줄러 코드(30분 자동): 미확인
- plan interval 상수: 60/240만 확인, 30은 확인 안 됨

판정: **재현됨 (자동 동기화 미구현/미연결)**

---

## 5) 최종 종합 판정
- 이슈1: 부분 재현됨
- 이슈2: 부분 재현됨
- 이슈3: 미재현
- 이슈4: 현재 설계상 분리됨
- 이슈5: 재현됨

우선순위 제안:
1. 이슈5 자동 동기화
2. 이슈1 월별 세금 고정
3. 이슈4 토스트-알림센터 정책 정합
4. 이슈2 review 상세 정보 표시
5. 이슈3 (현 시점 미재현, 추가 실패 샘플 확보 시 재검증)

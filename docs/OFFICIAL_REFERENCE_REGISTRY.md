# 공식 기준 레지스트리 (Official Reference Registry)

- 기준 버전: `official-refs-2026.03.07`
- 대상 연도: `2026`
- 마지막 점검일: `2026-03-07`
- 원칙: **공식 근거가 누락/불확실하면 숫자 출력 금지**
- 허용 출처 도메인: `law.go.kr`, `nhis.or.kr`, `mohw.go.kr`, `nts.go.kr`

## A. NHIS 공식 입력 체크리스트

1. 건강보험료율 (7.19%, 0.0719)
- 조문/근거: 국민건강보험법 시행령 제44조 제1항
- URL: https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
- 추출 값: `1만분의 719`

2. 재산 점수당 금액 (211.5원)
- 조문/근거: 국민건강보험법 시행령 제44조 제2항
- URL: https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
- 추출 값: `211.5원`

3. 장기요양 비율 (건보료 대비 13.14%, 소득 대비 0.9448%)
- 조문/근거: 보건복지부 공식 보도자료
- URL: https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200
- 추출 값: `13.14%`, `0.9448%`

4. 전월세 평가식
- 조문/근거: 국민건강보험법 시행규칙 별표8
- URL: https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=
- 추출 값: `[보증금 + (월세 * 40)] * 0.30`

5. 재산 기본공제 및 점수표
- 조문/근거: 국민건강보험법 시행령 별표4
- URL: https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135095&gubun=
- 추출 값: 재산 등급 점수표, 기본공제 `100,000,000원`

6. 금융소득 1,000만원 기준
- 조문/근거: 국민건강보험법 시행규칙 제44조 단서
- URL: https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846
- 추출 값: `이자+배당 합 <= 1,000만원 제외`, `초과 시 전액 합산`

7. 소득 반영 시기
- 조문/근거: 국민건강보험법 시행령 제41조 제3항
- URL: https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493
- 추출 값: `1~10월 = 전전년도`, `11~12월 = 전년도`

8. 월 보험료 상/하한
- 조문/근거: 월별 건강보험료액의 상한과 하한에 관한 고시
- URL: https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=2100000270472&chrClsCd=010201
- 추출 값: 하한 `20,160원`, 상한 `4,591,740원`

## B. TAX 공식 입력 체크리스트

1. 종합소득세 기본세율/누진공제
- 조문/근거: 소득세법 제55조, 국세청 공식 세율표
- URL:
  - https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661
  - https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594
- 추출 값: 과세표준 구간, 세율, 누진공제

2. 지방소득세(개인지방소득세)
- 조문/근거: 지방세법(종합소득분)
- URL: https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95
- 추출 값: `소득세의 10%` 체계

## C. 끝수처리 규칙

1. 건보료/장기요양 끝수
- 조문/근거:
  - 국민건강보험법 제107조
  - 국고금관리법 제47조
- URL:
  - https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95
  - https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EA%B3%A0%EA%B8%88%EA%B4%80%EB%A6%AC%EB%B2%95
- 앱 적용값: `10원 미만 절사(truncate_under_10)`

## D. 상/하한 규칙

1. 월 보험료 상/하한은 고시값으로 clamp
- 하한: `20,160원`
- 상한: `4,591,740원`
- 기준 URL: https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=2100000270472&chrClsCd=010201

## TAX 계산 범위 정책 (확정/차단)

- 공식 확정 가능 영역: `과세표준(연)` 입력이 존재할 때
  - 산출세액(국세) + 지방소득세(10%) 계산
- 불확실 영역: 과세표준이 없고, 매출/경비/공제만 일부 있는 경우
  - 숫자 출력 금지
  - UI 문구: `계산 불가: 공식 입력(과세표준 또는 공제 항목)이 부족해요`

## 구현 위치

- 레지스트리: `services/official_refs/registry.py`
- 검증 스크립트: `scripts/verify_official_refs.py`
- 런타임 게이트: `services/official_refs/guard.py`

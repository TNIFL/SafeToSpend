# 공식 기준 스냅샷 (2026-03-06 KST 확인)

이 문서는 앱 런타임에서 사용하는 건보료/세금 기준 상수의 단일 소스입니다.  
실제 값은 `services/reference/nhis_reference.py`, `services/reference/tax_reference.py`에서 로드됩니다.

## 1) 건보료 (NHIS, 2026 기준)

- 적용 시작일: `2026-01-01`
- 마지막 확인일: `2026-03-06`
- 보험료율(건강보험): `0.0719` (7.19%)
- 지역 재산점수당 금액: `211.5원`
- 장기요양보험료율(소득 대비 참고): `0.009448` (0.9448%)
- 장기요양보험료(건강보험료 대비): `0.1314` (13.14%)
- 월별 건보료 하한(건강보험료 기준): `20,160원`
- 월별 건보료 상한(건강보험료 기준): `4,591,740원`
- 재산 기본공제: `100,000,000원`
- 자동차 보험료: `폐지(지역가입자)` 처리
- 금융소득 기준: `이자+배당 합 <= 10,000,000원`이면 제외, `10,000,000원 초과`면 전액 합산
- 전월세 평가식: `[보증금 + (월세 × 40)] × 0.30`
- 소득 반영 시기: `1~10월 = 전전년도`, `11~12월 = 전년도`

### NHIS 공식 출처

- 시행령 제44조(보험료율/점수당 금액):  
  https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
- 장기요양 비율(복지부):  
  https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200
- 월별 상하한(고시/생활법령):  
  https://www.law.go.kr/LSW//admRulInfoP.do?admRulSeq=2100000270472&chrClsCd=010201
- 소득 반영 시기(시행령 제41조):  
  https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493
- 금융소득 1,000만원 규칙(시행규칙 제44조 단서):  
  https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846
- 전월세 평가식(시행규칙 별표8):  
  https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=
- 2024-02 개편(자동차 폐지/재산공제 확대):  
  https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95%EC%8B%9C%ED%96%89%EB%A0%B9  
  https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95%EC%8B%9C%ED%96%89%EA%B7%9C%EC%B9%99

## 2) 세금 (종합소득세 + 지방소득세)

- 적용 시작일: `2026-01-01`
- 마지막 확인일: `2026-03-06`
- 종합소득세: 소득세법 제55조 누진세율 + 누진공제
- 지방소득세(종합소득분): `소득세의 10%`

### 2026 누진표(테스트 고정)

1. 14,000,000 이하: 6% / 누진공제 0
2. 50,000,000 이하: 15% / 누진공제 1,260,000
3. 88,000,000 이하: 24% / 누진공제 5,760,000
4. 150,000,000 이하: 35% / 누진공제 15,440,000
5. 300,000,000 이하: 38% / 누진공제 19,940,000
6. 500,000,000 이하: 40% / 누진공제 25,940,000
7. 1,000,000,000 이하: 42% / 누진공제 35,940,000
8. 초과: 45% / 누진공제 65,940,000

### 세금 공식 출처

- 소득세법 제55조:  
  https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661
- 국세청 세율표/예시:  
  https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594
- 지방소득세 10% 안내:  
  https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95

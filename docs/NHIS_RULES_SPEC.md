# NHIS 지역가입자 건보료 규칙 스펙 (SafeToSpend)

이 문서는 SafeToSpend의 지역가입자 건보료 계산에서 **규칙(법/고시 기준)**과 **추정(자동매칭/입력 보정)** 경계를 고정하기 위한 스펙입니다.

## 1) 공식 근거 링크
- 보험료율/점수당금액(시행령 제44조, 2026 기준 7.19% / 211.5원)
  - https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28
- 소득/재산 점수 산정(시행령 별표4, 자동차 점수 삭제 반영)
  - https://www.law.go.kr/flDownload.do?bylClsCd=110201&flSeq=139783837&gubun=
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=159610025&gubun=
- 전월세 평가(시행규칙 별표8)
  - https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=
- 소득 평가비율 100%/50% + 저소득 하한 처리(시행규칙 제44조)
  - https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846
- 자동차 부과 폐지 정책 근거(복지부)
  - https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1479847&mid=a10503010000&tag=
- 반영주기(11월~다음해 10월)
  - https://www.nhis.or.kr/static/alim/paper/oldpaper/202211/sub/18.html
- 공식 모의계산기(수동 검증)
  - https://www.nhis.or.kr/nhis/minwon/initCtrbCalcView.do
- 장기요양 비율(2026: 건보 대비 13.14%, 소득 대비 0.9448%)
  - https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010100

## 2) 규칙 vs 추정 경계
- 규칙(오차 0 목표)
  - 보험료율, 점수당금액, 장기요양 비율
  - 소득 평가비율(100%/50%)
  - 336만원/28만원 경계 처리
  - 재산 기본공제(1억원), 전월세 평가식
  - 상한/하한 적용
  - 11월 반영주기
- 추정(오차 가능)
  - 주소/자산 자동매칭
  - 공시가격 → 과세표준 환산
  - 고지서 이력 기반 보정
  - 입력 누락 상태의 간편 추정

## 3) 엔진 입력 계약
- 소득(연간 소득금액 기준)
  - 100% 반영: 사업/이자/배당/기타
  - 50% 반영: 근로/연금
- 재산
  - 재산세 과세표준 합계(보유분)
  - 전월세: 보증금, 월세
- 기타
  - target_month(YYYY-MM)
  - 선택: 과거 고지서(점수/건보료/합계)

## 4) 엔진 출력 계약
- 소득 분해
  - income_monthly_evaluated_krw
  - income_points
  - income_premium_krw
- 재산 분해
  - property_amount_krw
  - property_points
  - property_premium_krw
- 최종 보험료
  - health_premium_krw
  - ltc_premium_krw
  - total_premium_krw
- 제약/주기
  - caps_applied
  - floors_applied
  - income_year_applied
  - property_year_applied
- 신뢰/근거
  - confidence_level
  - basis(source, source_year, fetched_at, matched_key, calc_steps)

## 5) 핵심 산식(엔진 적용)
- 전월세 평가
  - `rent_eval = (보증금 + 월세*40) * 0.30`
- 소득 평가
  - `평가소득연액 = (100%군 합계) + (50%군 합계 * 0.5)`
  - `평가소득월액 = floor(평가소득연액 / 12)`
- 소득 점수(336만원 초과)
  - `points = 95.25911708 + ((income - 3,360,000)/10,000) * 0.28350928`
  - 상한: `20,348.90`
- 건강보험료(월)
  - `health_raw = income_premium + property_premium (+ vehicle_premium if enabled)`
  - 상한/하한 적용
- 장기요양보험료(월)
  - `ltc = round(health * 0.1314)` (2026 기준)
- 합계
  - `total = health + ltc`

## 6) 반영주기(현재 vs 11월)
- cycle_start_year
  - target_month가 11~12월이면 해당 연도
  - target_month가 1~10월이면 전년도
- 적용 연도
  - income_year_applied = cycle_start_year - 1
  - property_year_applied = cycle_start_year

## 7) 비정상 결과 가드
- total_points가 너무 낮은데 총액이 비정상적으로 작으면 경고 플래그를 강제
- 입력 부족/매칭 실패는 0점 확정이 아니라 low confidence + 경고로 처리
- 모든 출력은 UI에서 반드시 `(추정)` 표기

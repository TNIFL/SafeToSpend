# 공식 자료 검증 범위 정의

## 1. 구조 검증 범위
- 형식 whitelist 검사
- MIME/확장자 일치 검사
- 제목/발급기관/필수 헤더 검사
- 기준일/기간/핵심 금액 파싱 검사
- 해시 계산
- parser_version, parse_status, trust_grade 저장

## 2. 기관 확인 범위
- 정부24 `다운로드파일 진본확인`
- 홈택스 `민원증명 원본 확인(수요처조회)`
- NHIS `증명서 발급사실 확인`

기관 공식 기능에서 확인 성공 메타가 있어야만 `기관 확인 완료` 상태를 저장할 수 있다.

## 3. 구조 검증과 기관 확인의 차이
- 구조 검증 완료는 기관 진위확인과 동일하지 않다.
- 해시는 업로드 이후 무결성 추적 도구다.
- 해시만으로 기관 발급 여부를 증명하지 않는다.
- 공식 로고, 문서 제목, 양식 일치, PDF 텍스트 추출 성공만으로 `기관 확인 완료`를 선언하지 않는다.

## 4. 이번 단계 구현 대상
- 구조 검증
- 기준일 저장
- 신뢰등급 저장
- 기관 확인 메타를 담을 수 있는 준비

## 5. 이번 단계 비대상
- 자동 스크래핑
- 법적 검토 없는 대리 인증
- 비공식 원본확인 우회
- 사용자 업로드만으로 `기관 확인 완료` 자동 부여

## 6. 저장 가능한 기관 확인 메타 예시
- verification_channel
- verification_checked_at
- verification_result_code
- verification_note_minimal

파일 전체 또는 긴 응답 원문은 이 메타와 별도로 취급한다.

## 공식 확인 기능 출처
- 정부24 공식 사이트: https://plus.gov.kr/
- 홈택스/손택스 민원증명 원본 확인 메뉴 안내: https://mob.hometax.go.kr/jsonAction.do?actionId=UTBPPABA40F001
- 국민건강보험공단 증명서 발급사실 확인: https://www.nhis.or.kr/static/html/guide/sub1_04.html
- 국민건강보험 사회보험통합징수포털 증명서 진위확인 안내: https://si4n.nhis.or.kr/jpca/JpCab00105.do

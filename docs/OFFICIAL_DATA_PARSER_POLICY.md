# 공식자료 parser/registry 정책

## 기본 원칙

- registry가 문서 유형 후보를 먼저 식별합니다.
- parser는 식별된 문서에서 핵심 값만 추출합니다.
- 같은 공식 문서의 소폭 변형만 허용합니다.
- 애매하면 `parsed`로 올리지 않고 `needs_review`, `unsupported`, `failed`로 닫습니다.
- OCR, 스캔 이미지, 과한 fuzzy matching은 v1 범위 밖입니다.

## 현재 main에서 허용하는 변형

- 제목/문서명 공백, 개행, 구두점 차이
- 헤더 위치가 앞 1~3행 정도 밀린 tabular 문서
- header alias 수준의 소폭 헤더 변형
  - `세목` / `세목명`
  - `납부세액 합계` / `납부금액 합계`
  - `원천징수세액 합계` / `원천징수 세액 합계`
  - `가입자구분` / `가입자 유형` / `가입자유형`
- 날짜 포맷 차이
  - `YYYY-MM-DD`
  - `YYYY.MM.DD`
  - `YYYY년 M월 D일`
- 금액 포맷 차이
  - `640000`
  - `640,000`
  - `640 000원`

## 계속 막는 경우

- 기관/문서 유형 식별이 약한 문서
- 핵심 날짜/금액/세목 없이 비슷해 보이기만 하는 문서
- 지원 문서가 아닌 자유형 정리 파일
- 스캔/이미지/OCR 의존 문서
- 다중 후보 중 임의 선택이 필요한 문서

## 현재 지원 문서 유형

- `hometax_withholding_statement`
- `hometax_tax_payment_history`
- `nhis_payment_confirmation`
- `nhis_eligibility_status`

## 책임 경계

- registry
  - 문서 타입 후보 식별
  - 공식기관/지원 문서 여부 판정
- parser
  - 핵심 날짜/금액/유형 값 추출
  - `passed / needs_review` 구조 판정
- upload
  - `parsed / needs_review / unsupported / failed` 상태 저장
  - 사용자 화면용 설명 구성

# 공식 자료 파서 정책 v1

## 1. 기본 원칙

- 문서별 전용 parser/adapter 구조를 사용합니다.
- registry가 문서 식별을 먼저 하고, 맞는 parser로만 라우팅합니다.
- OCR, 스크린샷, 스캔 이미지 만능 처리는 하지 않습니다.
- 확실히 읽히는 것만 자동 반영합니다.
- 애매하면 `needs_review`, 지원 범위 밖이면 `unsupported`로 닫습니다.

## 2. parser 상태값

- `parsed`
- `needs_review`
- `unsupported`
- `failed`

## 3. registry 판정 원칙

registry는 아래를 순서대로 확인합니다.

1. 파일 확장자/MIME whitelist
2. PDF 암호 여부
3. 스캔 PDF 여부
4. 제목/헤더/핵심 키워드 일치 여부
5. parser 존재 여부

registry 결과값은 아래만 씁니다.

- `supported_document_type`
- `unsupported_format`
- `unsupported_document_type`
- `needs_review`

## 4. fixture 원칙

- 공식 양식 구조를 참조한 비식별 fixture만 사용
- parser 회귀 테스트용 구조-충실 fixture 사용
- 실명/실번호/실거래처 사용 금지
- 성공 fixture와 함께 `needs_review`, `unsupported` fixture도 유지

## 5. v1 parser 목록

- `hometax_withholding_statement`
- `hometax_business_card_usage`
- `nhis_payment_confirmation`

## 6. 남기는 메타

- `parser_version`
- `document_type`
- `parse_status`
- `parse_error_code`
- `parse_error_detail`
- `parsed_at`
- `extracted_payload_json`
- `extracted_key_summary_json`

## 7. fail-closed 예시

- 기간 또는 금액이 정확히 안 읽힘 -> `needs_review`
- 암호 PDF -> `unsupported`
- 스캔 PDF -> `unsupported`
- 지원 문서 헤더 불일치 -> `unsupported_document_type`

## 8. explainability 원칙

자동 반영 여부는 아래 정보로 설명 가능해야 합니다.

- 어떤 문서로 식별했는지
- 어떤 필드가 읽혔는지
- 어떤 이유로 `needs_review/unsupported/failed`가 됐는지

# 공식자료 지원 형식

## 현재 main에서 받는 공식자료

| 쉬운 이름 | document_type | 지원 형식 |
| --- | --- | --- |
| 홈택스 원천징수 관련 문서 | `hometax_withholding_statement` | `CSV`, `XLSX` |
| 홈택스 납부내역 | `hometax_tax_payment_history` | `CSV`, `XLSX` |
| 건강보험 납부확인서 | `nhis_payment_confirmation` | 텍스트 추출 가능한 `PDF` |
| 건강보험 자격 관련 문서 | `nhis_eligibility_status` | 텍스트 추출 가능한 `PDF` |

## 지원 형식 안에서 허용하는 구조 변형

- 상단 안내문 1~2줄 추가
- tabular 헤더가 1~3행 밀린 경우
- 제목/헤더의 공백, 개행, 구두점 차이
- 날짜 표기 차이
- 금액 표기 차이

## 명시적 비지원 형식

- 이미지 파일(`PNG`, `JPG`, `JPEG`, `WEBP`)
- 스캔 PDF
- 암호가 걸린 PDF
- 사용자가 다시 편집한 자유형 정리 파일
- 제목/기관명/핵심 헤더가 손상된 파일

## fail-closed 원칙

- 핵심 구조와 값이 명확할 때만 `parsed`
- 일부만 읽히면 `needs_review`
- 지원 문서가 아니면 `unsupported`
- 읽기 자체가 실패하면 `failed`

# 영수증 비용처리 입력 스키마 초안

목적:
- OCR/텍스트 파서 결과
- 매칭된 거래 정보
- 사용자가 보완 입력한 설명
을 분리해서 규칙 엔진 입력으로 고정한다.

## 1. 핵심 입력 필드

| 필드명 | 타입 | 필수 여부 | 설명 | 생성 주체 |
| --- | --- | --- | --- | --- |
| `merchant_name` | string | 권장 | 영수증/전자영수증의 상호명 | OCR/텍스트 파서 |
| `approved_at` | datetime string | 권장 | 결제 승인 시각 | OCR/텍스트 파서 또는 거래 매칭 |
| `amount_krw` | integer | 필수 | 결제 금액 | 거래 금액 우선, 없으면 OCR 추출값 |
| `payment_method` | string | 선택 | 카드/계좌/현금 등 결제수단 | OCR/텍스트 파서 |
| `source_text_raw` | string | 권장 | 원문 텍스트 | 업로드 원문 |
| `source_text_normalized` | string | 권장 | 정규화한 원문 | 파서 전처리 |
| `candidate_transaction_id` | integer | 선택 | 매칭 후보 거래 ID | 거래 매칭 단계 |
| `counterparty` | string | 선택 | 거래의 상대방/가맹점 표시값 | 거래 데이터 |
| `memo` | string | 선택 | 거래 메모 | 거래 데이터/사용자 입력 |
| `weekend_flag` | boolean | 필수 | 주말 결제 여부 | 엔진 전처리 |
| `late_night_flag` | boolean | 필수 | 심야 결제 여부 | 엔진 전처리 |

## 2. 권장 확장 필드

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| `receipt_type` | string | 종이 영수증, 전자영수증, 현금영수증, 세금계산서 등 |
| `vat_krw` | integer | 부가세 금액 |
| `approval_no` | string | 승인번호 |
| `account_id` | integer | 연결 계좌 식별자 |
| `business_context_note` | string | 사용자가 직접 입력한 업무 목적 메모 |
| `attendee_note` | string | 식사/접대비 후보일 때 참석자/거래처 메모 |
| `evidence_kind` | string | 적격증빙 성격 분류값 |

## 3. 정규화 규칙
- `merchant_name`, `counterparty`, `memo`는 공백 정리 후 비교한다.
- `approved_at`은 timezone 포함 ISO 형식으로 정규화한다.
- `amount_krw`는 정수 원 단위로 통일한다.
- `weekend_flag`, `late_night_flag`는 `approved_at` 기준으로 전처리한다.
- `source_text_raw`는 원본 보관, `source_text_normalized`는 엔진 비교용으로만 사용한다.

## 4. 입력 원칙
- 거래 금액이 있으면 OCR 금액보다 거래 금액을 우선한다.
- 거래 시각이 있으면 업로드 시각보다 거래 시각을 우선한다.
- 사용자가 입력하지 않은 설명을 엔진이 임의 생성하지 않는다.
- 사용자 확인이 필요한 메모는 `business_context_note`, `attendee_note` 같은 별도 필드로 받는다.

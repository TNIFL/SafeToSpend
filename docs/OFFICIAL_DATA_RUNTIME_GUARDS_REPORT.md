# 공식 자료 runtime guards 보고서

## 목적
- official data 저장/렌더 경로에서 금지 데이터와 금지 판정을 런타임에서 차단한다.
- trust grade와 identifier 마스킹 규칙을 공통 정책으로 고정한다.

## 적용 범위
- `services/official_data_guards.py`
- `services/privacy_guards.py`
- `services/official_data_upload.py`
- `services/official_data_parsers.py`
- `routes/web/official_data.py`
- `templates/official_data/result.html`

## 저장 가드 결과
- preview/snippet/raw text 계열 키 제거
  - `preview_text`, `text_preview`, `raw_text`, `document_text`, `snippet` 등
- 주민등록번호 패턴 감지 시 저장 제거 + `needs_review` downgrade
- 건강 상세정보성 자유 텍스트 제거 + `needs_review` downgrade
- NHIS payload는 `member_type` 같은 추가 민감 키를 더 보수적으로 제거
- 식별키 raw 값은 저장하지 않고 `*_hash`, `*_masked`만 유지

## trust grade 강제 규칙
- `A`: 공식 verification source + `verification_status == succeeded` + `parse_status == parsed`
- `B`: 구조 검증 성공(`structure_validation_status == passed`) + parsed
- `C`: 일반 사용자 업로드 자료 기준
- `D`: user modified 또는 review/failed 상태
- 구조 검증 성공만으로 `A` 부여 금지

## 전용 필드 우선 원칙
- 새 저장은 전용 필드에 쓴다.
  - `trust_grade`
  - `trust_grade_label`
  - `trust_scope_label`
  - `structure_validation_status`
  - `verification_source`
  - `verification_status`
  - `verification_checked_at`
  - `user_modified_flag`
  - `sensitive_data_redacted`
- 기존 JSON summary는 읽기 fallback만 허용한다.

## UI 차단 결과
- result 화면은 전용 trust field 기준으로 검증 수준을 표시한다.
- 금지 표현 사용 금지
  - `진본`
  - `원본임을 보증`
  - `법적으로 보장`
  - `100% 정확`
- 허용 표현만 유지
  - `기관 확인 완료`
  - `공식 양식 구조와 일치`
  - `업로드한 자료 기준`
  - `검토 필요`

## 공통 identifier guard 연계
- official_data 가드는 `services/privacy_guards.py`를 사용한다.
- 공통 함수
  - `mask_bank_identifier(...)`
  - `hash_sensitive_identifier(...)`
  - `sanitize_account_like_value(...)`
  - `redact_identifier_for_render(...)`
  - `is_disallowed_identifier_storage(...)`

## 남은 리스크
- legacy JSON-only 레코드는 fallback이 남아 있다.
- 운영 DB의 과거 raw 값은 remediation script 실제 적용 전까지 남아 있을 수 있다.

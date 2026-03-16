# 공식 자료 업로드 구현 보고서 v1

## A. 지원 문서/지원 형식

v1은 whitelist 방식으로 아래 3종만 지원합니다.

1. 홈택스 이미 빠진 세금/원천징수 자료
2. 홈택스 사업용 카드 사용내역
3. NHIS 보험료 납부확인서

지원 형식은 아래처럼 좁게 유지합니다.

- CSV
- XLSX
- 텍스트 추출 가능한 원본 PDF

명시적 비지원 형식은 아래와 같습니다.

- 스크린샷 이미지
- 사진 촬영본
- 스캔 PDF
- 암호 걸린 PDF
- 사용자가 다시 편집한 파일
- 복사/붙여넣기 텍스트 문서

핵심 원칙은 `정확히 읽히는 것만 반영`입니다.

## B. 저장 스키마 구현 결과

`OfficialDataDocument` 모델과 migration `fb24c1d9e8a1_add_official_data_documents.py`를 추가했습니다.

주요 필드:

- `source_system`
- `document_type`
- `display_name`
- `file_name_original`
- `file_mime_type`
- `file_size_bytes`
- `file_hash`
- `parser_version`
- `parse_status`
- `parse_error_code`
- `parse_error_detail`
- `extracted_payload_json`
- `extracted_key_summary_json`
- `document_issued_at`
- `document_period_start`
- `document_period_end`
- `verified_reference_date`
- `raw_file_storage_mode`
- `raw_file_key`
- `parsed_at`

기본 저장 전략은 아래와 같습니다.

- 원본 저장 기본값: `none`
- 기본 저장 단위: 핵심 추출값 + 기준일 + 파싱 상태
- 원본 저장 UI: 준비 중 상태로만 표시

## C. parser registry와 v1 parser 구현 결과

구현 파일:

- `services/official_data_extractors.py`
- `services/official_data_parser_registry.py`
- `services/official_data_parsers.py`
- `services/official_data_upload.py`

registry가 먼저 아래를 판단합니다.

1. 확장자/MIME whitelist
2. 암호 PDF 여부
3. 스캔 PDF 여부
4. 제목/헤더/핵심 키워드 일치 여부
5. 문서 타입별 parser 연결 여부

지원 상태:

- `supported_document_type`
- `unsupported_format`
- `unsupported_document_type`
- `needs_review`

parser 상태:

- `parsed`
- `needs_review`
- `unsupported`
- `failed`

지원 fixture 기준 smoke 결과는 아래 파일에 저장했습니다.

- `reports/official_data_parser_smoke.json`

## D. 업로드 UI/결과 상태 구현 결과

추가 라우트:

- `GET /dashboard/official-data/upload`
- `POST /dashboard/official-data/upload`
- `GET /dashboard/official-data/result/<id>`

추가 템플릿:

- `templates/official_data/upload.html`
- `templates/official_data/result.html`

구현한 결과 상태:

- 성공: 추출된 핵심값 요약 + 기준일 + 자동 관리 안내
- 검토 필요: 일부만 읽힘 + 자동 반영 안 함 + 다시 받는 경로 안내
- 지원 안 함: 형식/문서 비지원 + 공식 사이트 경로 안내
- 실패: 파싱 실패 + 원인 코드 표시

가이드/엔트리포인트 연결:

- `/guide/official-data`에서 지원 문서는 바로 업로드 진입 가능
- `overview`, `tax_buffer`, `nhis`에서 업로드 페이지로 바로 진입 가능

## E. 기준일/재확인 UX 결과

업로드 결과 화면에서 아래를 같이 보여줍니다.

- 자료 종류
- 기준일
- 기간
- 핵심 금액
- 현재 상태
- 재확인 권장 여부

초안 재확인 규칙:

- `parsed`가 아니면 검토 필요
- 기준일이 오래됐으면 재확인 권장
- 시즌(4~6월, 10~11월)에 들어왔고 기준일이 지난 경우 재확인 권장

표시 문구 원칙:

- “이 자료를 기준으로 자동 관리해볼게요”
- “새 시즌이 오거나 숫자가 크게 달라질 때만 다시 확인할게요”
- “영구 자동화” 표현은 사용하지 않음

## F. 테스트 결과

실행 명령:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_official_data_upload_model \
  tests.test_official_data_parser_registry \
  tests.test_official_data_parsers \
  tests.test_official_data_upload_routes \
  tests.test_official_data_guide_page \
  tests.test_official_data_entrypoints \
  tests.test_official_data_copy \
  tests.test_official_data_policy_docs
```

결과:

```text
Ran 25 tests in 1.784s

OK
```

추가 확인:

- parser smoke JSON 생성 완료
- migration 적용 완료

## G. 남은 리스크

- 아직 모든 홈택스/NHIS 공식 자료를 지원하지 않습니다.
- 스캔/이미지 문서는 여전히 비지원입니다.
- 홈택스 메뉴/헤더가 개편되면 parser/guide를 같이 보정해야 합니다.
- NHIS 원본 저장 허용 범위는 민감정보 처리 범위를 더 좁혀 최종 확정하는 것이 맞습니다.
- PDF 텍스트 추출은 v1 범위를 좁게 잡은 단순 extractor 기준이라, 구조가 조금만 달라도 `needs_review`로 닫힙니다.

## H. 다음 단계 연결 포인트

1. 추출값 저장 스키마를 실제 세금/건보료 입력값 업데이트와 연결
2. 원본 선택 저장 UI와 삭제/보유기간 처리 구현
3. 더 많은 홈택스 공식 문서 parser 추가
4. overview / tax_buffer / nhis에 기준일 배지와 재확인 CTA 연결

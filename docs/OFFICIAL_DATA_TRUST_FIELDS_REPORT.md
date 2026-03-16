# 공식 자료 trust field 전환 보고서

## 1. Baseline 체크
- 브랜치: `official-data-trust-remediation`
- 기준 커밋: `2b5007f` (`chore: restore official_data baseline migration ancestry`)
- 작업 시작 전 확인 명령
  - `PYTHONPATH=/tmp/official-data-v1-baseline FLASK_APP=/tmp/official-data-v1-baseline/app.py .venv/bin/flask db heads -d /tmp/official-data-v1-baseline/migrations`
  - `PYTHONPATH=/tmp/official-data-v1-baseline FLASK_APP=/tmp/official-data-v1-baseline/app.py .venv/bin/flask db current -d /tmp/official-data-v1-baseline/migrations`
  - `PYTHONPATH=/tmp/official-data-v1-baseline FLASK_APP=/tmp/official-data-v1-baseline/app.py .venv/bin/flask db upgrade -d /tmp/official-data-v1-baseline/migrations`
- 작업 시작 기준 결과
  - `flask db heads`: `fb24c1d9e8a1 (head)`
  - `flask db current`: `fb24c1d9e8a1 (head)`
  - `flask db upgrade`: no-op 정상 종료
- 중단 원칙
  - 위 3개 중 하나라도 실패하면 trust/remediation 작업을 진행하지 않는다.

## 2. 목적
- `OfficialDataDocument`의 trust/verification 메타를 JSON 보조 구조가 아니라 전용 스키마 필드로 고정한다.
- 이후 공식 자료 상태반영의 기준축을 필드 단위로 안정화한다.
- 읽기 fallback은 유지하되, 새 저장 경로는 전용 필드 우선으로 강제한다.

## 3. source of truth
- `docs/OFFICIAL_DATA_LEGAL_BOUNDARY_REPORT.md`
- `docs/OFFICIAL_DATA_LEGAL_MATRIX.md`
- `docs/OFFICIAL_DATA_DATA_CLASSIFICATION.md`
- `docs/OFFICIAL_DATA_STORAGE_AND_DELETION_POLICY.md`
- `docs/OFFICIAL_DATA_TRUST_GRADE_POLICY.md`
- `docs/OFFICIAL_DATA_VERIFICATION_SCOPE.md`
- `docs/OFFICIAL_DATA_RUNTIME_GUARDS_REPORT.md`
- `docs/OFFICIAL_DATA_RISK_INVENTORY.md`
- `docs/OFFICIAL_DATA_REMEDIATION_PLAN.md`

## 4. 전용 필드 추가 결과
`OfficialDataDocument`에 아래 필드를 추가했다.
- `trust_grade`
- `trust_grade_label`
- `trust_scope_label`
- `structure_validation_status`
- `verification_source`
- `verification_status`
- `verification_checked_at`
- `verification_reference_masked`
- `user_modified_flag`
- `sensitive_data_redacted`

기본값과 제약은 보수적으로 고정했다.
- `trust_grade`: nullable, `A/B/C/D` 외 저장 금지
- `verification_status`: 기본 `none`, `none/pending/succeeded/failed/not_applicable`
- `structure_validation_status`: 기본 `not_applicable`, `passed/failed/partial/not_applicable`
- `user_modified_flag`: 기본 `false`
- `sensitive_data_redacted`: 기본 `true`
- 자동 `A` 부여 금지

## 5. migration 책임 분리
- migration 파일: `8fd1c2b3a4e5_add_official_data_trust_fields_and_identifier_guards.py`
- migration은 컬럼, 제약, 인덱스 추가만 수행한다.
- 기존 JSON-only 레코드 정리와 기존 식별자 cleanup은 migration에 넣지 않는다.
- 기존 데이터 정리는 별도 remediation script에서 dry-run 가능하게 분리한다.

## 6. 저장/렌더 전환 결과
- 새 저장 경로는 아래 전용 필드를 우선 사용한다.
  - `trust_grade`
  - `trust_grade_label`
  - `trust_scope_label`
  - `verification_status`
  - `verification_source`
  - `verification_checked_at`
  - `structure_validation_status`
  - `user_modified_flag`
  - `sensitive_data_redacted`
- `extracted_key_summary_json` 안의 trust 관련 값은 읽기 fallback만 허용한다.
- result 화면은 전용 필드 우선, JSON fallback 보조 규칙으로 렌더한다.

## 7. A등급 차단 규칙
- 공식 기관 verification 메타가 없는 상태에서는 `A` 저장 금지
- `verification_source in {government24_download_verify, hometax_origin_check, nhis_certificate_verify}`
  이고 `verification_status == succeeded`일 때만 `A` 가능
- 구조 검증 성공은 최대 `B`
- `user_modified_flag == true` 또는 `parse_status in {needs_review, failed}`이면 `D`
- 그 외는 `C`

## 8. 읽기 fallback 정책
- 기존 레코드에서 전용 필드가 비어 있으면 `extracted_key_summary_json`의 trust 값을 읽는다.
- fallback은 읽기 호환용으로만 남긴다.
- 새 저장은 fallback JSON에 의존하지 않는다.

## 9. 테스트 기대값 변경 이유
- official data result 화면은 더 이상 summary JSON의 trust 값을 주 표시 기준으로 쓰지 않는다.
- route/render 테스트는 `structure_validation_status`, `verification_status`, 전용 trust field 우선 렌더를 기준으로 바뀐다.
- 이 변경은 사용자 체감 기능 확장이 아니라, 상태반영 기준축을 JSON 임시값에서 전용 필드로 이동시키기 위한 것이다.

## 10. 검증 결과
- 단위 테스트: `tests.test_official_data_trust_fields`
- 연관 회귀: `tests.test_official_data_runtime_guards`, `tests.test_official_data_upload_routes`
- migration 적용 결과
  - `flask db upgrade`: `fb24c1d9e8a1 -> 8fd1c2b3a4e5`
  - 이후 `flask db heads/current`: `8fd1c2b3a4e5 (head)`

## 11. 남은 리스크
- 기존 JSON-only 레코드는 아직 전용 필드가 비어 있을 수 있다.
- fallback은 임시 호환이므로 이후 운영 적용 전에 별도 backfill 정책을 정해야 한다.
- 기관 verification 연계는 이번 단계 범위 밖이라 실제 운영에서 `A` 등급은 거의 발생하지 않는다.

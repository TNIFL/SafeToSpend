# bank/plain identifier 리메디에이션 보고서

## 1. 목적
- bank/account 관련 경로에서 raw 식별자 저장, 표시, summary 누적 리스크를 실제 코드 기준으로 줄인다.
- 이번 문서는 actual file 기준 인벤토리, 수정 결과, remediation script 사용법을 함께 고정한다.

## 2. 점검 범위
- `domain/models.py`
- `routes/web/bank.py`
- `services/import_popbill.py`
- `templates/bank/index.html`
- `templates/calendar/tax_buffer.html`
- `templates/calendar/review.html`
- `templates/calendar/month.html`

## 3. 인벤토리 결과
| 위치 | 현재 동작 | 위험 유형 | 심각도 | 즉시 조치 | 백필 필요 |
| --- | --- | --- | --- | --- | --- |
| `domain/models.py` `BankAccountLink.account_number` | legacy raw 계좌번호가 남아 있을 수 있음 | 평문 식별자 저장 잔존 | high | 새 저장은 token 구조로 전환, 기존 값은 script로 정리 | 예 |
| `routes/web/bank.py` `toggle()/alias()` | raw hidden input과 raw lookup에 의존하던 경로 | 저장 경로가 raw 식별자에 의존 | high | `account_fingerprint + account_last4 + bank_account_id` 중심으로 전환 | 예 |
| `templates/bank/index.html` hidden input | 계좌번호 원문이 DOM hidden field에 실리던 구조 | raw 식별자 render | high | hidden input에서 raw 제거 | 아니오 |
| `templates/calendar/tax_buffer.html` linked balance table | `acc.account_number` 우선 렌더 가능 | raw 식별자 render | high | `account_number_masked` 우선 렌더 | 아니오 |
| `services/import_popbill.py` `error_summary[errors][account]` | `bank_code-account_number`를 JSON에 저장 | summary/json 내 raw 식별자 저장 | high | masked 값으로 축소 | 예 |
| `domain/models.py` `ImportJob.error_summary` | 기존 raw 식별자 JSON이 남아 있을 수 있음 | 기존 데이터 잔존 리스크 | medium | remediation script로 정리 | 예 |
| `templates/calendar/review.html` | 재점검 결과 raw bank identifier 렌더 없음 | 직접 리스크 미확인 | low | 유지 | 아니오 |
| `templates/calendar/month.html` | 재점검 결과 raw bank identifier 렌더 없음 | 직접 리스크 미확인 | low | 유지 | 아니오 |

## 4. 실제 수정 결과
### 4.1 공통 가드 도입
- `services/privacy_guards.py` 추가
- 공통 유틸
  - `mask_bank_identifier(...)`
  - `hash_sensitive_identifier(...)`
  - `sanitize_account_like_value(...)`
  - `redact_identifier_for_render(...)`
  - `is_disallowed_identifier_storage(...)`

### 4.2 bank 화면/저장 경로 축소
- `routes/web/bank.py`
  - raw 계좌번호 대신 `account_fingerprint`, `account_last4` 기반으로 link를 찾거나 생성
  - 새/갱신 link는 `account_number`에 raw 대신 `acct_<hash-prefix>` token 저장
  - `bank_account_id`를 우선 연결
  - recent job error summary는 렌더 직전에 masked 값으로 축소
- `templates/bank/index.html`
  - hidden input에서 raw `account_number` 제거
  - `account_fingerprint`, `account_last4`만 전송

### 4.3 import error summary 축소
- `services/import_popbill.py`
  - error summary의 `account`는 `0004-****9012` 같은 masked 값으로만 저장
  - sync 시 raw 계좌번호는 live account lookup에서 메모리로만 사용
  - `bank_account_id + account_fingerprint` 우선, legacy raw는 fallback만 유지

### 4.4 render 축소
- `templates/calendar/tax_buffer.html`
  - `acc.account_number_masked` 우선 렌더, 없을 때만 legacy fallback

## 5. remediation script
- 파일: `scripts/remediate_sensitive_identifiers.py`
- 원칙
  - migration에 넣지 않는다.
  - dry-run 기본, `--apply`는 별도 승인 후 사용
- 대상
  - `BankAccountLink.account_number`
  - `ImportJob.error_summary`
- dry-run 예시
```bash
PYTHONPATH=. .venv/bin/python scripts/remediate_sensitive_identifiers.py --limit 200 --output reports/bank_identifier_remediation_smoke.json
```
- 출력 리포트
  - `scanned_rows`
  - `changed_rows`
  - `skipped_rows`
  - `affected_models`
  - `dry_run/applied`
  - 환경에 따라 `db_available`, `mode`

## 6. smoke 결과
- 현재 clean baseline 환경에는 DB URI가 없어 dry-run은 fixture smoke로 기록했다.
- 생성 파일: `reports/bank_identifier_remediation_smoke.json`
- 결과 요약
  - `bank_account_links`: 1 row scanned / 1 changed
  - `import_jobs`: 1 row scanned / 1 changed
  - `mode = fixture`
  - `db_available = false`

## 7. 테스트 기대값 변경 이유
- bank page는 raw hidden input을 더 이상 보내지 않는다.
- import error summary는 raw 계좌번호가 아니라 masked 값으로만 남는다.
- tax buffer 계좌 표시는 masked 값 우선으로 바뀐다.

## 8. 남은 리스크
- `BankAccountLink.account_number` 컬럼 자체는 legacy 호환 때문에 남아 있다.
- 기존 운영 데이터는 remediation script를 실제 DB에 적용하기 전까지 raw 값이 남아 있을 수 있다.
- package/export 전역 식별자 점검은 이번 단계 범위 밖이다.

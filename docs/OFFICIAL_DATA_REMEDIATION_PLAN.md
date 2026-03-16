# 공식 자료 / identifier remediation 계획

## 목적
- official data trust field 전환과 bank/plain identifier 리스크 제거의 우선순위를 고정한다.
- migration, 코드 수정, remediation script의 책임을 분리한다.

## 우선순위

### P0 이번 티켓 즉시 수정
- `official_data` trust/verification 전용 필드 추가
- trust grade 저장/렌더를 전용 필드 우선으로 전환
- `routes/web/bank.py` raw 계좌번호 의존 처리 제거
- `templates/bank/index.html`, `templates/calendar/tax_buffer.html`의 raw 계좌번호 표시 제거
- `services/import_popbill.py` error summary raw 식별자 제거

### P1 이번 티켓 코드 수정 + remediation script 정리
- `BankAccountLink.account_number` 새 저장을 token 구조로 전환
- `ImportJob.error_summary` 기존 데이터 정리용 dry-run script 추가
- official data JSON 메타와 전용 필드 사이 fallback/중복 최소화
- smoke report 추가

### P2 후속 검토
- package/export 전역 식별자 재점검
- 외부 verification 메타 연계
- legacy JSON-only official data 레코드의 선택적 backfill

## 실행 순서
1. baseline 체크 통과 확인
2. bank/plain identifier actual path 재점검 문서 고정
3. trust/verification 전용 필드 migration
4. 저장/렌더를 전용 필드 중심으로 전환
5. 공통 identifier guard 도입
6. P0/P1 경로 실제 수정
7. remediation script dry-run
8. 문서/회귀 최종 고정

## 상태
| 항목 | 상태 | 비고 |
| --- | --- | --- |
| baseline 체크 잠금 | 완료 | DB baseline 정상 확인 기록 고정 |
| bank/plain identifier actual path 재점검 | 완료 | actual file 기준으로 P0/P1/P2 확정 |
| trust/verification 전용 필드 migration | 완료 | `8fd1c2b3a4e5` 적용 |
| trust grade 저장/렌더 전용 필드 전환 | 완료 | 새 저장은 전용 필드 우선, JSON fallback만 유지 |
| 공통 identifier guard 도입 | 완료 | `services/privacy_guards.py` 추가 |
| bank/import raw 식별자 경로 수정 | 완료 | bank form/import summary/tax buffer 반영 |
| remediation script dry-run | 완료 | fixture smoke report 생성 |
| 운영 DB 실제 cleanup | 후속 | 승인 및 DB env 준비 후 `--apply` |

## 분리 원칙
- migration은 컬럼 추가/제약 추가만 담당한다.
- 기존 데이터 정리는 migration에 넣지 않는다.
- remediation은 `scripts/remediate_sensitive_identifiers.py`에서 dry-run 가능하게 수행한다.

## 테스트 기대값 변경 원칙
- trust grade와 verification 표시는 전용 필드 우선으로 바뀌므로 official_data route/render 테스트 기대값이 바뀔 수 있다.
- raw 계좌번호를 마스킹/token 기준으로 바꾸면 bank/tax buffer 관련 렌더 기대값이 바뀔 수 있다.
- 기대값 변경 이유는 `docs/OFFICIAL_DATA_TRUST_FIELDS_REPORT.md`와 `docs/BANK_IDENTIFIER_REMEDIATION_REPORT.md`에 함께 기록한다.

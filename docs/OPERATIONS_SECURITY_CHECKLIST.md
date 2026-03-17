# 운영 안전 체크리스트

## 1) 관리자 권한 부여
- 관리자 권한은 `users.is_admin`만 기준이다.
- 회원가입만으로 관리자가 될 수 없다.
- 권한 부여:
  - `flask --app app set-admin-role --email admin@example.com --grant`
- 권한 회수:
  - `flask --app app set-admin-role --email admin@example.com --revoke`

## 2) 계정 삭제 검증
- 탈퇴 후 아래 사용자 귀속 데이터가 남지 않는지 확인한다.
  - `official_data_documents`
  - `nhis_user_profiles`
  - `nhis_bill_history`
  - `billing_*`
  - `evidence_items`
  - `receipt_items`
  - `user_bank_accounts`
- 파일 정리 대상:
  - `EvidenceItem.file_key`
  - `ReceiptItem.file_key`
  - `ReceiptExpenseReinforcement.supporting_file_key`
  - `OfficialDataDocument.raw_file_key`
- 파일이 이미 없어도 탈퇴 자체는 완료돼야 하고, 누락은 로그로 남아야 한다.

## 3) SECRET_KEY 배포 전 확인
- 외부에서 접근 가능한 서버는 기본 `SECRET_KEY`로 실행하면 안 된다.
- 기본 `SECRET_KEY`는 localhost 전용 개발 환경에서만 허용된다.
- 배포 전 확인:
  - `APP_ENV`가 운영/스테이징 값인지
  - `SECRET_KEY`가 기본값이 아닌지
  - 세션/토큰 서명 경로가 같은 키를 쓰는지

## 4) 코드 전달용 압축 생성
- 사전 점검:
  - `PYTHONPATH=. .venv/bin/python scripts/export_code_bundle.py --dry-run --fail-if-forbidden-found`
- ZIP 생성:
  - `PYTHONPATH=. .venv/bin/python scripts/export_code_bundle.py`
- 제외 대상:
  - `uploads/**`
  - `reports/**`
  - `*.dump`
  - `*.sql`
  - 실제 `.env*` (`.env.example` 제외)
  - `__pycache__/**`
  - `.pytest_cache/**`
  - `.venv/**`
- 결과 ZIP은 repo 밖에 만들어야 한다.

# DB Backup Rehearsal Results

작성일: 2026-03-11
목적: DB 백업 절차를 실제로 1회 수행해 실행 가능성 확인

## 1) 실행 환경
- 대상 DB: 로컬 PostgreSQL (운영/스테이징 아님)
- 접속 정보: `.env` 기반(`SQLALCHEMY_DATABASE_URI`)
- 민감값(비밀번호/전체 URI)은 문서에서 마스킹

## 2) 실행 명령(실행 스크립트)
```bash
/bin/zsh -lc 'set -a; source .env; set +a; .venv/bin/python - <<PY
# SQLALCHEMY_DATABASE_URI 파싱 -> pg_dump -Fc 실행
PY'
```

## 3) 실행 결과
- 시작/종료 시각: 2026-03-11 22:09 (KST) 전후
- 결과: `SUCCESS`
- 생성 파일: `reports/rehearsals/db_backup_rehearsal_20260311_220959.dump`
- 파일 크기: `278,892 bytes` (약 `0.27 MB`)

원문 핵심 로그:
- `BACKUP_STATUS=success`
- `BACKUP_FILE=reports/rehearsals/db_backup_rehearsal_20260311_220959.dump`
- `BACKUP_SIZE_BYTES=278892`

## 4) 검증
- 백업 파일 존재 확인: 완료
- 백업 파일 포맷(`pg_dump -Fc`) 확인: 완료
- 다음 단계(복구 리허설) 연계 가능: 완료

## 5) 한계/주의
- 본 리허설은 로컬 DB 기준이며, 스테이징/운영 백업 스케줄러 검증은 별도 필요
- 운영 환경의 백업 보존 주기/암호화/원격 보관 정책은 인프라 권한 필요

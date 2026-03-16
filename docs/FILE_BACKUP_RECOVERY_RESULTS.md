# File Backup & Recovery Results

작성일: 2026-03-11
목적: 업로드 파일(`uploads/evidence`) 백업/복구 가능성 점검

## 1) 대상 경로
- 소스: `uploads/evidence`

## 2) 리허설 절차
1. `uploads/evidence`를 `tar.gz`로 압축 백업
2. `/tmp` 임시 경로에 압축 해제
3. 백업 전/후 파일 수 비교

## 3) 실행 결과
- 실행 시각: 2026-03-11 22:10 (KST) 전후
- 백업 전 파일 수: `180`
- 복구 후 파일 수: `180`
- 판정: `SUCCESS`

원문 핵심 로그:
- `FILE_BACKUP_FILE_COUNT=180`
- `FILE_BACKUP_SIZE_BYTES=166808539`
- `FILE_RESTORE_FILE_COUNT=180`

## 4) 아티팩트 위치
- 압축 파일은 저장소 용량 보호를 위해 `/tmp/s2s_backup_rehearsal_artifacts/`로 이동
- 복구 검증 경로: `/tmp/s2s_file_restore_20260311_221037`

## 5) 한계/주의
- 본 검증은 로컬 파일시스템 기준
- 운영 스토리지(S3/NAS/볼륨 스냅샷) 정책/암호화/보존주기 검증은 별도 필요
- 파일 무결성(해시 전체 비교) 자동화는 추가 작업 권장

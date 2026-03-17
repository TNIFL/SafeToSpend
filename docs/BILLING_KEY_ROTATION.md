# Billing Key Rotation Guide

## 목적
`billing_key_enc` 암호화 비밀키를 교체할 때, 기존 결제수단 복호화 실패 없이 안전하게 전환하기 위한 최소 절차입니다.

## 현재 구조
- 활성 버전: `BILLING_KEY_ACTIVE_VERSION` (기본 `v1`)
- 비밀키 우선순위:
  1. `BILLING_KEY_ENCRYPTION_SECRET_<ACTIVE_VERSION_UPPER>`
  2. `BILLING_KEY_ENCRYPTION_SECRET`
- 신규 등록 결제수단은 활성 버전으로 암호화되고, `billing_methods.encryption_key_version`에 기록됩니다.

## 권장 회전 순서
1. 새 키 발급
- 새 버전 키 생성 (예: `v2`)
- 운영 비밀 저장소에 `BILLING_KEY_ENCRYPTION_SECRET_V2` 저장

2. 선배포
- 애플리케이션에 `BILLING_KEY_ENCRYPTION_SECRET_V2`를 먼저 배포
- 아직 `BILLING_KEY_ACTIVE_VERSION`은 기존 값 유지 (`v1`)

3. 활성 버전 전환
- `BILLING_KEY_ACTIVE_VERSION=v2` 반영
- 이후 신규 등록 결제수단은 `v2`로 암호화

4. 재암호화 배치(후속)
- 기존 `v1` 데이터는 배치로 점진 재암호화
- 배치 완료 전까지 `v1` 키 유지

5. 구버전 키 제거
- 모든 데이터가 `v2`로 전환된 뒤 `v1` 키 제거

## 장애 대응
- 전환 직후 등록 실패가 발생하면:
  1. `billing-startup-check`로 환경변수 상태 점검
  2. `BILLING_KEY_ACTIVE_VERSION`과 실제 키 존재 여부 확인
  3. 필요 시 활성 버전을 직전 값으로 롤백

## 주의
- `billing_key_enc` 평문 복호화/출력 금지
- 비밀키를 DB/로그/이슈 트래커에 남기지 않기
- 키 회전 작업은 운영자 2인 이상 검토 후 실행 권장

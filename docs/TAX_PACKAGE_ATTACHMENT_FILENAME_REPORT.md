# TAX Package Attachment Filename Report

## A. 문제 요약
- 세무사 전달 패키지 ZIP의 첨부 파일명이 기존에는 `tx_id + 원본파일명` 형태여서, 파일명만 보고 거래 맥락(일시/금액/거래처/증빙종류)을 파악하기 어려웠다.
- 개선 목표는 내부 저장키(`EvidenceItem.file_key`)는 유지하고, ZIP export 시점에서만 전달용 규칙형 파일명으로 리네이밍하는 것이다.

## B. 기존 패키지 파일명 구조 (Source of Truth)
- 다운로드 진입점: `routes/web/package.py:224` `download()` → `build_tax_package_zip(...)` 호출.
- ZIP 생성 핵심: `services/tax_package.py:922` `build_tax_package_zip()`.
- 기존 첨부 파일명 결정:
  - 기존 로직은 `secure_filename(original_filename)`에 `tx.id_`를 붙여 ZIP 경로 생성.
  - 기존 예시: `03_증빙첨부(attachments)/attachments/101_receipt.jpg`
- 첨부 경로 참조 위치:
  - `tx_records[].attachment_zip_path`
  - `attachments_index_records[].attachment_zip_path`
  - `evidence_index_records[].attachment_zip_path`
- 실제 파일 읽기 source:
  - `resolve_file_path(ev.file_key)` 사용 (내부 저장 경로는 변경하지 않음).

## C. 새 파일명 규칙
- 적용 위치: `services/tax_package.py`
  - `_build_attachment_export_filename(...)` (line 281)
  - `_build_attachment_zip_path(...)` (line 316)
  - `build_tax_package_zip()` 내 첨부 경로 생성 구간 (line 1215)
- 기본 포맷:
  - `YYYYMMDD_HHMMSS_금액원_거래처_증빙종류_순번.ext`
- 실제 예시:
  - `20260314_132455_15800원_스타벅스_영수증_001.jpg`
- 확장자:
  - 원본 파일 확장자 우선 유지
  - 원본 확장자 없으면 MIME 기반 추론
  - 둘 다 없으면 `.bin`

## D. fallback 규칙
- 거래시각 없음: `YYYYMMDD_시간미상`
  - 날짜는 현재 KST 기준일, 시간은 `시간미상`.
- 금액 없음: `금액미상`
- 거래처/장소 없음: `거래처미상`
  - 우선순위: `counterparty` > `memo 요약` > `거래처미상`
- 증빙종류 불명: `증빙`
  - 추론 우선순위: 전자영수증 > 영수증 > 첨부파일 > 증빙
- 동일 파일명 충돌:
  - `_build_attachment_zip_path`에서 `_001`, `_002` 순번 자동 증가로 회피.
- 문자열 정규화:
  - OS 금지문자/제어문자 제거 (`\\ / : * ? " < > |` 포함)
  - 과도한 길이는 token 단위 절단
  - 긴 숫자열(계좌/카드 유사 패턴) 제거 처리

## E. ZIP/인덱스 반영 범위
- ZIP 첨부 경로:
  - `03_증빙첨부(attachments)/attachments/{규칙형파일명}`
- 동기화 반영:
  - `transactions.xlsx`의 `attachment_zip_path`
  - `attachments_index.xlsx`의 `attachment_zip_path`
  - `evidence_index.xlsx`의 `attachment_zip_path`
- 안내 문구 동기화:
  - `README_증빙파일규칙.txt`를 새 규칙/ fallback 설명으로 갱신.

## F. 테스트 결과
- 실행 명령:
  - `.venv/bin/python -m unittest tests.test_tax_package_attachment_filenames tests.test_tax_package_zip_contents`
- 결과:
  - `Ran 10 tests ... OK`
- 검증 범위:
  - 정상 규칙 파일명 생성
  - 시간/금액/거래처 fallback
  - 특수문자 제거
  - 확장자 유지/추론
  - 동일 이름 충돌 시 순번 증가
  - ZIP 내부 첨부 경로 규칙 준수
  - `attachments_index.xlsx` / `evidence_index.xlsx`의 경로 참조 일치

## G. 실제 스모크 테스트 결과
- 결과 파일: `reports/tax_package_attachment_filename_smoke.json`
- 생성 ZIP: `SafeToSpend_TaxPackage_2026-03.zip`
- 첨부 샘플:
  - `SafeToSpend_TaxPackage_2026-03/03_증빙첨부(attachments)/attachments/20260314_132455_15800원_스타벅스_영수증_001.jpg`
- 인덱스 참조 샘플:
  - `03_증빙첨부(attachments)/attachments/20260314_132455_15800원_스타벅스_영수증_001.jpg`
- ZIP 내부 실제 경로와 인덱스 참조 경로가 일치함을 확인.

## H. 남은 리스크
- 거래처 원천값이 비거나 품질이 낮으면 `거래처미상` fallback이 노출될 수 있음.
- `memo` 품질이 낮은 데이터는 거래처 token 가독성이 제한될 수 있음.
- 현재 모델 구조상 거래 1건당 첨부 1건이 기본이라 `_002` 이상 순번은 충돌/확장 케이스 중심으로 검증됨.
- token 길이 제한으로 매우 긴 거래처명은 잘려 표시될 수 있음.

## I. 최종 판정
- 판정: **해소됨**
- 근거:
  - 전달용 규칙형 파일명 생성 로직이 ZIP export 경로에 반영됨.
  - 인덱스/원장 참조 경로 정합성 유지됨.
  - 단위 테스트 + ZIP 내용 테스트 + 스모크 결과 모두 통과.

# RECEIPT_ATTACH 카카오톡 텍스트 재현 보고서

## A. 문제 요약
- 제보 이슈: `/dashboard/review?...focus=receipt_attach...`에서 카카오톡 전자영수증/거래확인 텍스트를 넣어도 “아무 반응이 없다”.
- 이번 단계 목표: 기능 수정이 아니라 **실패 샘플 확보 + 재현 조건 확정**.
- 결과 요약: 샘플 8종 × 케이스 5개 = **총 40회 실행**, 실패/부분실패 0건.

## B. 처리 흐름 정리 (코드 기준)
### 1) submit
- 화면/모달 입력 폼:
  - `templates/calendar/partials/receipt_wizard_upload.html`
  - 폼 액션: `POST /dashboard/review/evidence/<tx_id>/upload` (`partial=1`)
- 라우트:
  - `routes/web/calendar/review.py:3017` `review_evidence_upload`

### 2) handler
- `receipt_text`, `receipt_type` 수신:
  - `routes/web/calendar/review.py:3047-3050`
- 텍스트 업로드 경로:
  - `store_evidence_text_file(...)`
  - `services/evidence_vault.py:630`
- EvidenceItem 상태 변경:
  - `status="attached"`, `file_key`/`note(receipt_meta:...)` 저장
  - `routes/web/calendar/review.py:3073-3091`
- 성공 시 confirm 단계로 redirect:
  - `routes/web/calendar/review.py:3120-3130`

### 3) parser
- confirm 단계에서 텍스트 파일 파싱:
  - `routes/web/calendar/review.py:3273-3282`
  - 텍스트면 `parse_receipt_from_text(...)`
  - `services/receipt_parser.py:113`
- 파서 결과 렌더:
  - partial step2 템플릿 `templates/calendar/partials/receipt_wizard_confirm.html`
  - `parser: ...` badge, `추출됨`/`자동 인식 실패` 표시

### 4) step transition
- 모달 JS가 submit 후 HTML을 교체하고 step marker 갱신:
  - `templates/calendar/review.html:904-945`
- step2 판정 신호:
  - `data-step="2"` 또는 `자동 인식 확인` 텍스트.

### 5) evidence state update
- EvidenceItem 변경 신호:
  - `status` 변화(`missing -> attached`)
  - `file_key` 생성
  - `note` 갱신

### 6) ui feedback
- 성공/실패 피드백 신호:
  - step2 렌더 + parser badge
  - 또는 flash/error 텍스트.

### “아무 반응 없음” 분해 기준
- `submit failure`
- `server route not entered`
- `parser no-result`
- `state transition missing`
- `evidence state unchanged`
- `ui feedback missing`

## C. 샘플 구성
- 생성 파일: `scripts/repro_receipt_attach_kakao_matrix.py`
- 샘플 파일: `reports/receipt_attach_kakao_matrix.json`
- 샘플 수: **8종**

샘플 유형:
1. 전자영수증형
2. 거래내역 확인형
3. 카드 승인 알림형
4. 입금 알림형
5. 짧은 요약형
6. 필드 일부 누락형
7. 변형/잡음 포함형
8. 누락/변형형

모든 샘플은 익명화 문자열이며, `sample_id`, `sample_type`, `field_presence`, `expected_difficulty` 메타를 저장했다.

## D. 재현 매트릭스 결과
- 실행 명령:
  - `PYTHONPATH=. .venv/bin/python scripts/repro_receipt_attach_kakao_matrix.py --matrix-source reports/real_data_issue_revalidation_matrix.json --out-matrix reports/receipt_attach_kakao_matrix.json --out-fail-samples reports/receipt_attach_kakao_fail_samples.json`
- 실행 케이스:
  - 후보 케이스 5개: `CASE_B`, `CASE_C`, `CASE_D`, `CASE_E`, `CASE_G`
  - 스킵 케이스 2개: `CASE_A`, `CASE_F` (`no_receipt_attach_candidate`)
- 총 실행 횟수:
  - **40회** (8 샘플 × 5 케이스)
- 단계별 결과:
  - `submit_success`: 40/40
  - `server_route_entered`: 40/40
  - `parser_processed`: 40/40
  - `step_transition`: 40/40
  - `evidence_state_changed`: 40/40
  - `ui_feedback`: 40/40
- verdict 분포:
  - `success`: 40
  - `partial`: 0
  - `fail`: 0

## E. 실패 샘플 보존 결과
- 파일: `reports/receipt_attach_kakao_fail_samples.json`
- 결과:
  - `fail_samples = []`
  - `count = 0`
  - 메모: “실패/부분실패 샘플이 없어 빈 배열”

## F. 실패 유형 분류 결과
- 분류 키:
  - `submit_failure`
  - `server_route_not_entered`
  - `parser_no_result`
  - `state_transition_missing`
  - `evidence_state_unchanged`
  - `ui_feedback_missing`
- 집계 결과:
  - **전 항목 0건**

## G. 남은 리스크
- 이번 재현은 서버/테스트클라이언트 기준으로는 전부 정상이다.
- 사용자 제보의 “무반응” 체감은 아래와 같은 프론트 런타임 조건일 수 있으나, 이번 범위(실패 샘플 확보)에서는 확정하지 못했다.
  - 브라우저/네트워크 단절 시 모달 `fetch` 예외 처리 체감
  - 특정 클라이언트 환경에서 모달 렌더 지연/실패
- 따라서 현재는 **실패 입력 샘플 기반 수정**으로 바로 가기 어렵다.

## H. 최종 판정
- **여전히 미재현 → 현 단계 보류 가능**
- 근거:
  - 다중 샘플(8) × 다중 케이스(5) 매트릭스에서 실패/부분실패 0건.
  - 실패 샘플 파일이 비어 있어, 수정 티켓 입력값(재현 가능한 failing input) 미확보.
- 다음 액션 권장:
  - 사용자 환경에서 실제 실패 원문/영상/네트워크 로그를 추가 확보한 뒤 동일 매트릭스에 샘플 추가 재실행.

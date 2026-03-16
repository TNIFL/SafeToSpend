# 공식 자료 추출값 효과 연결 보고서

## A. 공식 자료 반영 대상/우선순위 규칙
- 반영 대상
  - 홈택스 원천징수 자료: 이미 빠진 세금 보정
  - 홈택스 사업용 카드 사용내역: reference/support only
  - NHIS 보험료 납부확인서: 기준일/상태/참고금액
- 우선순위
  - `parsed + fresh + 필수 필드 정상` 공식 자료 > 수기 입력 > 거래 추정
  - 단, 더 최신 수기 입력이 있으면 수기 입력 유지
- 금지
  - 사업용 카드 사용내역으로 자동 비용 확정
  - stale 자료 강제 반영
  - NHIS 자료로 계산값 무조건 덮어쓰기

## B. 공식 자료 효과 계산 서비스 구현 결과
- 신규 서비스: `services/official_data_effects.py`
- 핵심 함수
  - `collect_official_data_effects_for_user(...)`
  - `compute_tax_official_effects(...)`
  - `compute_nhis_official_effects(...)`
  - `summarize_official_data_effects(...)`
- 반환값
  - `verified_withholding_tax_krw`
  - `verified_paid_tax_krw`
  - `verified_tax_reference_date`
  - `verified_nhis_paid_amount_krw`
  - `verified_nhis_reference_date`
  - `official_data_confidence_level`
  - `applied_documents`
  - `ignored_documents`
  - `stale_documents`
  - `effect_messages`
- fail-closed
  - `parsed`가 아니면 무시
  - 필수값 부족 시 `ignored`
  - 기준일 오래됨/기간 불일치 시 `stale`

## C. 세금/건보료 연결 결과
### 세금
- `TaxEstimate` 확장
  - `official_verified_withholding_tax_krw`
  - `official_verified_paid_tax_krw`
  - `official_tax_reference_date`
  - `tax_due_before_official_adjustment_krw`
  - `tax_delta_from_official_data_krw`
  - `buffer_delta_from_official_data_krw`
  - `official_data_applied`
  - `official_data_confidence_label`
- 홈택스 원천징수 자료가 fresh이고 우선순위를 만족하면 세금 차감 입력값으로 보정
- 반영 전/후 스냅샷을 모두 남김

### 건보료
- `build_nhis_result_meta(...)`와 `compute_nhis_monthly_buffer(...)`에 공식 자료 메타 연결
- v1 원칙
  - 계산값 자체는 유지
  - `nhis_official_reference_date`
  - `nhis_official_paid_amount_krw`
  - `nhis_official_status_label`
  - `nhis_official_data_applied`
  - `nhis_recheck_recommended`
  중심으로 UX 연결

## D. 업로드 후 사용자 피드백 UX 결과
- 업로드 결과 화면
  - 공식 자료 기준 스냅샷 저장 여부
  - 기준일
  - 일부 반영/지원 안 함/재확인 권장
- overview / tax_buffer
  - `공식 자료 기준으로 보정` notice
  - 기준일 표시
  - 공식 자료 반영으로 바뀐 점 요약
- 과장 금지
  - `100% 정확` 표현 없음
  - `공식 자료 기준으로 보정됨` 수준 유지

## E. 테스트 결과
- 신규 테스트
  - `tests.test_official_data_effects_rules`
  - `tests.test_official_data_effects_integration`
  - `tests.test_official_data_effects_render`
- 관련 회귀 묶음
  - 공식 자료 업로드/가이드/정책 테스트
  - 세금/NHIS 결과 메타 테스트

## F. 남은 리스크
- 지원 문서는 아직 3종으로 좁다.
- 홈택스 구조 개편 시 parser/guide/effect 규칙을 함께 수정해야 한다.
- NHIS 자료는 v1에서 계산값 덮어쓰기까지 가지 않고 신뢰도/기준일 연결 중심이다.
- stale 기준은 운영 데이터에 맞춰 추가 보정 여지가 있다.

## G. 다음 단계 연결 포인트
1. 추출값을 세금/건보료 입력값에 더 정교하게 매핑
2. 지원 parser 확대
3. 기준일 배지와 재확인 트리거를 overview/nhis 메인 흐름에 더 강하게 연결
4. 원본 선택 저장 UI와 삭제/보유기간 처리 연결

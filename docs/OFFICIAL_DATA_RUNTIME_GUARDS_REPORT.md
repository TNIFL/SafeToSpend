# 공식 자료 런타임 가드 보고서

## 1. 목적
- 법적 경계 문서를 실제 저장 경로와 화면 경로에 강제한다.
- 공식 자료 관련 금지 저장, 금지 표현, 금지 신뢰등급 판정을 코드 레벨에서 막는다.

## 2. 런타임 가드 적용 범위
- `services/official_data_upload.py`
  - parser 결과를 DB에 저장하기 직전 sanitize 적용
  - registry `needs_review` 경로의 preview 저장 제거
- `services/official_data_parsers.py`
  - partial payload에서 raw text 제거
  - summary는 masked 식별 참조만 남김
- `services/official_data_extractors.py`
  - detection text 길이 제한
- `routes/web/official_data.py`
  - 구조 검증과 기관 확인이 혼동되지 않도록 상태 카피 하향
- `templates/official_data/result.html`
  - 검증 수준, 등급, 기준일 중심 표시
  - raw 식별값/과장 표현 제거
- `templates/partials/official_data_effect_notice.html`
  - 구조 검증과 기관 확인을 같은 뜻으로 쓰지 않는다는 안내 추가

## 3. 저장 가드 규칙
- 주민등록번호 전체 패턴은 저장 전 제거 또는 safe fail
- 건강 상세정보성 자유 텍스트는 저장 금지
- 긴 preview/snippet/raw text는 저장 금지
- NHIS payload는 더 보수적으로 축소
- 식별키 원문은 저장하지 않고 해시/마스킹만 남김
- `fixture_source` 같은 불필요 메타는 기본 비저장

## 4. A등급 강제 규칙
- A등급은 공식 기관 확인 성공 메타가 있을 때만 허용
- 구조 검증 통과는 최대 B등급
- unsupported/uploaded는 C등급
- 사용자 수정 또는 검토 필요는 D등급
- 현재 구현 범위에서는 기관 확인 연계가 없으므로 자동으로 A등급이 나오지 않는다.

## 5. 금지 표현 차단 결과
- 제거/차단 대상
  - `진본`
  - `법적으로 보장`
  - `100% 정확`
  - `원본임을 보증`
- 구조 검증 완료 자료는 `구조 검증 완료`, `공식 양식 구조와 일치`, `업로드한 자료 기준`, `기준일 기준 반영` 수준으로만 표현한다.

## 6. 즉시 수정 완료 항목
- `needs_review` preview 비저장 처리
- parser partial payload에서 raw text 제거
- NHIS `member_type` 기본 비저장
- `payor_key`, `business_key`, `insured_key`의 저장 전 해시/마스킹
- result summary raw 식별값 제거
- `업로드 확인 완료` 대신 `구조 검증 완료`로 카피 하향

## 7. 남은 리스크
- 기존 계좌번호 평문 저장은 이번 티켓 범위 밖이라 별도 리메디에이션이 필요하다.
- upload 화면의 일부 `원본` 카피는 후속 카피 정리 티켓에서 통일하는 것이 맞다.
- trust grade는 현재 summary/payload 메타 기반이라 전용 스키마 필드 분리 여부를 후속 검토해야 한다.

## 8. 다음 구현 티켓 전 체크
- 새 parser가 preview/raw text를 payload에 넣지 않는가
- NHIS parser가 건강 상세정보나 긴 자유 텍스트를 저장하지 않는가
- A등급이 verification 메타 없이 나오지 않는가
- result/notice copy에 금지 표현이 없는가

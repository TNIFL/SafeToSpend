# 공식 자료 위험 저장/표시 경로 인벤토리

## 범위
- `official_data` 모델/서비스/라우트/템플릿
- preview/snippet/result 요약 경로
- 공식 자료와 맞닿은 세무사 패키지/다운로드 연결 가능 경로
- 관련 로그/디버그 출력 경로
- 기존 코드베이스의 민감 식별값 저장 사례

## 위험 인벤토리
| 위치 | 현재 동작 | 위험 유형 | 심각도 | 권장 조치 | 이번 단계 분류 |
| --- | --- | --- | --- | --- | --- |
| `services/official_data_upload.py:118-123` | `needs_review` 자료에 `preview_text` 일부를 `extracted_payload_json`에 저장 | 긴 원문 preview 저장 | 높음 | preview 비저장, registry 이유만 남김 | 즉시 수정 |
| `services/official_data_parsers.py:174` | 홈택스 원천징수 parser가 `payor_key` 원문을 payload에 담음 | 평문 식별키 저장 | 높음 | 저장 직전 해시/마스킹, summary는 masked only | 즉시 수정 |
| `services/official_data_parsers.py:251` | 사업용 카드 parser가 `business_key` 원문을 payload에 담음 | 평문 식별키 저장 | 높음 | 저장 직전 해시/마스킹 | 즉시 수정 |
| `services/official_data_parsers.py:302,323` | NHIS parser가 `text_preview` 일부를 partial payload에 담음 | NHIS 긴 원문 preview 저장 | 매우 높음 | 텍스트 preview 제거, 구조 플래그만 저장 | 즉시 수정 |
| `services/official_data_parsers.py:332-333` | NHIS parser가 `insured_key`, `member_type`를 payload에 담음 | NHIS 식별값/불필요 부가정보 저장 | 매우 높음 | `insured_key`는 해시·마스킹, `member_type`는 기본 비저장 | 즉시 수정 |
| `services/official_data_upload.py:181-189` | result summary에 `주요 식별키`를 그대로 표시 | 화면 raw 식별값 노출 | 높음 | render 직전 masked summary만 사용 | 즉시 수정 |
| `routes/web/official_data.py:32,59` | `지원하는 공식 원본 파일`, `업로드 확인 완료` 같은 강한 표현 사용 | 검증 수준 오인 가능 | 중간 | `지원 형식`, `구조 검증 완료` 수준으로 하향 | 즉시 수정 |
| `templates/official_data/result.html:49-52` | `원본 파일 전체` 저장 설명과 raw summary 구조 전제 | 저장 범위 오인 가능 | 중간 | `파일 전체 기본 비저장`, `핵심 추출값 중심`으로 정리 | 즉시 수정 |
| `templates/official_data/upload.html:42,58,63` | `공식 원본`, `원본 파일 선택 저장` 문구 사용 | 표현 정합 리스크 | 중간 | `기관 발급 파일`, `파일 전체 선택 저장`으로 하향 | 즉시 수정 |
| `services/official_data_effects.py` | 공식 자료 effect notice는 기준일/보정 중심, 금지 표현 없음 | 현재 뚜렷한 저장 리스크 없음 | 낮음 | 유지, 금지 표현 회귀만 점검 | 후속 검토 |
| `services/official_data_*` 로그 경로 | `official_data` payload를 직접 logger/print 하는 경로는 현재 뚜렷하지 않음 | 로그 재노출 가능성은 낮으나 재발 주의 | 낮음 | 새 로그 추가 시 payload 전체 출력 금지 | 후속 검토 |
| `domain/models.py`, `routes/web/bank.py` | 계좌번호 전체 저장 경로 존재 | 평문 민감 식별값 저장 | 매우 높음 | 별도 리메디에이션 티켓에서 암호화/비저장 구조로 전환 | 다음 티켓 |

## 점검 결과 요약
- 공식 자료 경로의 즉시 수정 대상은 `긴 preview 저장`, `NHIS payload 과수집`, `식별키 원문 저장`, `구조 검증을 기관 확인처럼 보이게 하는 카피`다.
- 세무사 패키지 쪽은 현재 공식 자료 신뢰등급을 직접 노출하는 경로가 확인되지 않았다. 다음 공식 자료 패키지 연결 티켓에서 별도 검토가 필요하다.
- 로그/디버그 경로는 현재 `official_data` payload 직접 출력이 확인되지는 않았지만, 새 로그를 추가할 때는 raw payload 금지 원칙을 같이 강제해야 한다.

# 공식 자료 리메디에이션 계획

## 즉시 수정
| 항목 | 상태 | 수정 파일 | 남은 후속 작업 |
| --- | --- | --- | --- |
| `needs_review` preview 비저장 | 완료 | `services/official_data_upload.py` | registry detection meta만 남기는 방식 유지 |
| parser partial payload에서 raw text 제거 | 완료 | `services/official_data_parsers.py` | 새 parser 추가 시 동일 규칙 적용 |
| 식별키 저장 축소/해시·마스킹 | 완료 | `services/official_data_guards.py`, `services/official_data_upload.py` | 실제 스키마 trust grade 필드 분리 검토 |
| NHIS payload 축소 | 완료 | `services/official_data_guards.py`, `services/official_data_parsers.py` | NHIS 확장 parser도 동일 규칙 적용 |
| result summary raw 식별값 제거 | 완료 | `services/official_data_upload.py`, `templates/official_data/result.html` | overview/NHIS 뱃지에도 동일 masked policy 적용 |
| 구조 검증과 기관 확인 카피 분리 | 완료 | `services/official_data_upload.py`, `routes/web/official_data.py`, `templates/official_data/upload.html`, `templates/official_data/result.html`, `templates/partials/official_data_effect_notice.html` | guide/entrypoint 카피 일괄 정리 후속 검토 |

## 다음 티켓에서 수정
| 항목 | 이유 | 권장 방향 |
| --- | --- | --- |
| 평문 계좌번호 저장 | 공식 자료 경계와 별개로 기존 스키마 리스크가 큼 | 암호화 또는 `fingerprint + last4` 중심 재설계 |
| trust grade 전용 저장 필드 | 현재는 summary/payload 메타에 포함 | 스키마 필드 분리 여부 후속 결정 |

## 후속 검토
| 항목 | 이유 |
| --- | --- |
| 세무사 패키지/다운로드 산출물의 신뢰등급 표기 | 공식 자료 패키지 연결 티켓에서 별도 기준 필요 |
| official_data debug/log 금지 가드 공통화 | 현재 직접 로그는 없지만 후속 회귀가 필요 |
| 원본 선택 저장 UI와 삭제 정책 | 법적 경계 문서를 따르는 별도 구현 필요 |

## 이번 단계 원칙
- 저장 차단은 문서 수준이 아니라 `upload 저장 직전`에 강제한다.
- 기관 확인 메타 없이는 A등급을 줄 수 없다.
- NHIS 자료는 같은 규칙 안에서도 더 보수적으로 축소한다.
- 금지 표현은 template와 route copy 양쪽에서 제거한다.

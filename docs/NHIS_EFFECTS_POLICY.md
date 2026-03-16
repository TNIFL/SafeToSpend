# NHIS 공식 자료 반영 정책

## 기준축

NHIS 공식 자료 반영도 아래 전용 필드만 기준으로 판단한다.

- `trust_grade`
- `verification_status`
- `structure_validation_status`

## NHIS 반영 표

| 문서 종류 | 실제 코드 document_type | 반영 대상 상태 | 반영 강도 | 기준 필드 | 사용자 표시 문구 | 금지 해석 |
| --- | --- | --- | --- | --- | --- | --- |
| 건보료 납부확인서 | `nhis_payment_confirmation` | `parse_status=parsed`, `trust_grade in (A,B,C)`, `structure_validation_status=passed` | 참고/신뢰도 보정 | `total_paid_amount_krw`, `verified_reference_date` | `최근 공식 납부 기준 참고 상태예요.` | 건보료 완전 확정/현시점 정확 금액으로 해석 금지 |
| 건보료 납부확인서 | `nhis_payment_confirmation` | `trust_grade=D` 또는 `parse_status!=parsed` 또는 `structure_validation_status!=passed` | 반영 보류 | 전용 필드 검토 후 미반영 | `검토가 더 필요해서 참고 상태만 보류했어요.` | 자동 확정/기관 보증으로 해석 금지 |
| NHIS 자격 계열 문서 | `nhis_eligibility_status` | `parse_status=parsed`, `trust_grade in (A,B,C)`, `structure_validation_status=passed` | 참고/재확인 판단 보조 | `verified_reference_date`, `eligibility_status`, `eligibility_start_date`, `eligibility_end_date`, `latest_status_change_date` | `자격 상태 자료를 기준일과 재확인 판단에 참고하고 있어요.` | 이 자료만으로 건보료 금액 완전 확정으로 해석 금지 |
| NHIS 자격 계열 문서 | `nhis_eligibility_status` | `trust_grade=D` 또는 `parse_status!=parsed` 또는 `structure_validation_status!=passed` | 반영 보류 | 전용 필드 검토 후 미반영 | `검토가 더 필요해서 자격 상태는 참고 반영하지 않았어요.` | 자동 확정/기관 보증으로 해석 금지 |

## NHIS 1차 반영 원칙

- 1차에서는 NHIS 자료로 건보료를 완전 확정하지 않는다.
- 허용 범위는 아래와 같다.
  - 기준일 갱신
  - 최근 공식 납부금액 참고
  - 자격 상태/최근 변동일 참고
  - 신뢰도 상승 또는 재확인 필요 상태 표시
- `nhis_payment_confirmation`은 최근 공식 납부금액 참고용이다.
- `nhis_eligibility_status`는 가입 상태/최근 변동일/재확인 판단 보조용이다.
- 자료 기준일이 오래되면 `stale` 또는 `재확인 권장` 상태로 내린다.
- `trust_grade=A/B/C/D`에 따라 effect strength를 차등 적용한다.
- `verification_status` 또는 `structure_validation_status`가 불충분하면 참고 상태도 보류할 수 있다.

## stale / recheck 규칙

- 기준일이 없으면 `review_needed`
- 기준일이 오래됐으면 `stale`
- 시즌에 가까운 시기이거나 기준일이 90일 이상 지난 경우 `재확인 권장`
- `trust_grade=D`면 reference 상태도 보수적으로 보류하거나 `review_needed`

## 금지 해석

- `건보료 완전 확정`
- `기관이 최종 보증한 금액`
- `이 자료만으로 신고/납부 완료`
- `현재 달 금액을 자동으로 확정 반영`

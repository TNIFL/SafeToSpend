# 공식 자료 반영 정책

## 기준축

공식 자료 반영은 아래 전용 필드만 기준으로 판단한다.

- `trust_grade`
- `verification_status`
- `structure_validation_status`

`extracted_payload_json`나 `extracted_key_summary_json` 안의 임시 trust 값으로 반영 강도를 결정하지 않는다.

## 세금 반영 표

| 문서 종류 | 실제 코드 document_type | 반영 대상 상태 | 반영 강도 | 기준 필드 | 사용자 표시 문구 | 금지 해석 |
| --- | --- | --- | --- | --- | --- | --- |
| 홈택스 원천징수/이미 빠진 세금 자료 | `hometax_withholding_statement` | `parse_status=parsed`, `trust_grade in (A,B)`, `structure_validation_status=passed` | 직접 반영 | `total_withheld_tax_krw`, `verified_reference_date` | `공식 자료 기준으로 이미 빠진 세금을 반영했어요.` | 수입/비용 자체를 확정한 것으로 해석 금지 |
| 홈택스 원천징수/이미 빠진 세금 자료 | `hometax_withholding_statement` | `parse_status=parsed`, `trust_grade=C`, `structure_validation_status=passed` | 약한 반영 또는 참고 | `total_withheld_tax_krw`, `verified_reference_date` | `업로드한 자료 기준 참고값으로 반영 강도를 낮췄어요.` | 기관 확인 완료로 해석 금지 |
| 홈택스 원천징수/이미 빠진 세금 자료 | `hometax_withholding_statement` | `trust_grade=D` 또는 `parse_status!=parsed` 또는 `structure_validation_status!=passed` | 반영 보류 | 전용 필드 검토 후 미반영 | `검토가 더 필요해서 세금 숫자에는 자동 반영하지 않았어요.` | 자동 확정/신고 완료로 해석 금지 |
| 홈택스 납부내역 계열 | `hometax_tax_payment_history` | `parse_status=parsed`, `trust_grade in (A,B)`, `structure_validation_status=passed` | 직접 반영 | `paid_tax_total_krw`, `verified_reference_date`, `latest_payment_date` | `공식 자료 기준으로 이미 납부한 세금을 반영했어요.` | 이 문서 1종만으로 신고 완료/세액 완전 확정으로 해석 금지 |
| 홈택스 납부내역 계열 | `hometax_tax_payment_history` | `parse_status=parsed`, `trust_grade=C`, `structure_validation_status=passed` | 참고 또는 약한 반영 | `paid_tax_total_krw`, `verified_reference_date` | `업로드한 납부 자료 기준 참고값으로만 반영했어요.` | 기관 확인 완료/신고 완료로 해석 금지 |
| 홈택스 납부내역 계열 | `hometax_tax_payment_history` | `trust_grade=D` 또는 `parse_status!=parsed` 또는 `structure_validation_status!=passed` | 반영 보류 | 전용 필드 검토 후 미반영 | `검토가 더 필요해서 납부내역은 자동 반영하지 않았어요.` | 자동 확정/법적 확정으로 해석 금지 |
| 홈택스 사업용 카드 사용내역 | `hometax_business_card_usage` | `parse_status=parsed` + 모든 trust grade | 참고 전용 | `total_card_usage_krw`, `verified_reference_date` | `사업용 카드 자료를 참고 정보로만 보여줘요.` | 비용 확정/세금 즉시 감소로 해석 금지 |

## 신뢰등급별 세금 반영 강도

| trust_grade | verification_status / structure_validation_status 예시 | 세금 직접 반영 가능 여부 | 설명 |
| --- | --- | --- | --- |
| A | `verification_status=succeeded`, `structure_validation_status=passed` | 가능 | 기관 확인 메타와 구조 검증이 모두 있는 경우만 직접 반영 후보 |
| B | `verification_status=none`, `structure_validation_status=passed` | 가능 | 구조 검증은 됐지만 기관 확인 전이므로 기준일과 이유를 함께 표시 |
| C | `verification_status=none`, `structure_validation_status=passed` 또는 제한적 상태 | 제한적 | 업로드 자료 기준이므로 직접 반영은 약하게 하거나 참고 수준으로 제한 |
| D | `needs_review`/`failed`/`user_modified_flag=true` 등 | 불가 | 세금 숫자 자동 반영 금지 |

## 반영 원칙

- 세금 직접 반영은 `hometax_withholding_statement`의 원천징수 성격 값과 `hometax_tax_payment_history`의 이미 납부한 세금 성격 값만 대상으로 한다.
- 비용, 수입, 신고 완료 상태를 이번 단계에서 새로 확정하지 않는다.
- 공식 자료가 있어도 `비용 확정`, `신고 완료`, `법적으로 확정`으로 해석하면 안 된다.
- `verification_status` 또는 `structure_validation_status`가 불충분하면 반영을 보류할 수 있다.
- 반영 전/후 숫자와 기준일을 함께 보여준다.

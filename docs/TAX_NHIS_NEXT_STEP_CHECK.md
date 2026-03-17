# TAX/NHIS Next Step Check

- 작성일: 2026-03-14
- 목적: 인라인 저장 플로우/퍼널 계측이 실제 동작하는지 최소 검증으로 확정

## A. 세금 인라인 저장 플로우 검증

실검증 사용자: `user_pk=343` (수동 재현)

확인 결과:
1. `GET /overview?month=2026-03` 인라인 소득유형 카드 노출 확인
2. `GET /dashboard/tax-buffer?month=2026-03` 인라인 소득유형 카드 노출 확인
3. `POST /dashboard/profile/tax-income-classification` 저장 성공 확인
4. 저장 후 `reason` 변경 확인
   - before: `missing_income_classification`
   - after: `missing_withheld_tax`
5. 이벤트 확인
   - `tax_inline_income_classification_shown` 기록
   - `tax_inline_income_classification_saved` 기록
   - `tax_basic_next_step_viewed` 기록

판정: **정상**

## B. NHIS 인라인 저장 플로우 검증

실검증 사용자: `user_pk=343` (수동 재현)

확인 결과:
1. `GET /overview?month=2026-03` 인라인 가입유형 카드 노출 확인
2. `GET /dashboard/nhis?month=2026-03&source=nhis` 인라인 가입유형 카드 노출 확인
3. `POST /dashboard/nhis/membership-type` 저장 성공 확인
4. 저장 후 `reason/accuracy` 재계산 확인
   - before: `blocked / missing_membership_type`
   - after: `limited / missing_property_tax_base`
5. 이벤트 확인
   - `nhis_inline_membership_type_shown` 기록
   - `nhis_inline_membership_type_saved` 기록
   - `nhis_detail_next_step_viewed` 기록

판정: **정상**

## C. 퍼널 계측 최종 판정

근거 파일: `reports/input_funnel_audit_manual_validation.json`

최소 기대치 검증:
- TAX: `shown>=1`, `saved>=1`, `next_step_viewed>=1` 충족
- NHIS: `shown>=1`, `saved>=1`, `next_step_viewed>=1` 충족

결론:
- 퍼널 계측은 **정상**
- `post_inline_save`에서 0건이 관측된 원인은 계측/저장 연결 버그가 아니라, 해당 기간 운영 트래픽의 실제 저장 행동 부족이다.

## D. 다음 단계

- 이 트랙(인라인 저장 동작/계측 버그 확인)은 **종료 가능**
- 다음 작업부터는 다른 주제로 이동 가능
- 단, 운영 개선 과제로는 "노출 대비 실제 저장 전환율" 실험이 남아 있다

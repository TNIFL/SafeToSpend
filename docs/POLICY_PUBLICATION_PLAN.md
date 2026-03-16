# 정책 문서 공개 경로 계획

작성일: 2026-03-11
목적: 정책 문서 초안을 실제 사용자 노출 경로로 연결하기 위한 배포 계획

## 1. 공개 대상 문서
- 이용약관: `docs/TERMS_OF_SERVICE_DRAFT.md`
- 개인정보처리방침: `docs/PRIVACY_POLICY_DRAFT.md`
- 결제/구독/자동결제 안내: `docs/BILLING_AND_SUBSCRIPTION_POLICY_DRAFT.md`
- 해지/환불 정책: `docs/REFUND_AND_CANCELLATION_POLICY_DRAFT.md`

## 2. 권장 공개 URL(앱 라우트)
- `/terms`
- `/privacy`
- `/billing-policy`
- `/refund-policy`

## 3. 템플릿 노출 위치(오픈 전 필수)
1. 공통 footer(`templates/base.html`)
- 이용약관 / 개인정보처리방침 / 결제정책 / 해지·환불 / 문의 링크 고정

2. 요금제/결제 시작 페이지(`templates/pricing.html`)
- CTA 주변에 핵심 고지 요약
  - 자동결제
  - 업그레이드 즉시청구/환불없음
  - 기간종료 해지
  - 결제 실패 3일 유예

3. 결제 시작 CTA 인접 화면
- `templates/mypage.html`
- `templates/bank/index.html`
- `templates/package/index.html`

4. 구독/계정 관리 영역
- 결제 상태/해지 관련 링크에서 정책 문서 접근 가능하게 유지

## 4. 결제 시작 전 필수 고지 문구(요약)
- "업그레이드는 전체 금액 즉시 청구되며 기존 결제분 환불은 없습니다."
- "해지는 기간 종료 시 반영되며, 환불은 제공하지 않습니다."
- "결제 실패 시 3일 유예기간이 적용됩니다."
- "세금/건강보험료 수치는 참고용 추정값일 수 있습니다."

## 5. 현재 구현 상태 점검
- 문서 초안: 생성됨
- 앱 내 공개 라우트: 미구현
- footer/pricing/mypage 링크 연결: 미구현

## 6. 오픈 판정
- 정책 문서 "초안 작성"은 완료되었으나, 실제 사용자 공개 경로가 없으므로
  오픈 전 필수 항목은 **미완료** 상태.

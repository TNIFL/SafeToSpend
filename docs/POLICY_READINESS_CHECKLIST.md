# Policy Readiness Checklist

작성일: 2026-03-11  
목적: 구독형 서비스 필수 정책 문서 준비 상태 점검

## 필수 정책 문서 점검표

| 항목 | 현재 상태 | 공개 경로 | 누락 시 리스크 |
|---|---|---|---|
| 이용약관 | 없음 | 없음 | 법적 분쟁 시 기준 부재, 결제/해지 분쟁 확대 |
| 개인정보처리방침 | 없음 | 없음 | 개인정보 수집/보관/파기 근거 불명확 |
| 해지/환불 정책 | 일부 있음(문구 산재) | [templates/pricing.html](/Users/tnifl/Desktop/SafeToSpend/templates/pricing.html), [templates/bank/index.html](/Users/tnifl/Desktop/SafeToSpend/templates/bank/index.html) | 페이지별 문구 불일치 시 신뢰 저하/민원 증가 |
| 자동결제 안내 | 일부 있음(결제 플로우 문구) | [templates/billing/*.html](/Users/tnifl/Desktop/SafeToSpend/templates/billing) | 청구 시점/해지 반영 시점 오해 가능 |
| 공식 문의처 | 일부 있음(인앱) | [routes/web/support.py](/Users/tnifl/Desktop/SafeToSpend/routes/web/support.py), [templates/support/form.html](/Users/tnifl/Desktop/SafeToSpend/templates/support/form.html) | 앱 장애 시 대체 채널 부재 |
| 세금/건보료 추정값 고지 | 일부 있음 | [templates/base.html](/Users/tnifl/Desktop/SafeToSpend/templates/base.html) 면책 문구 | 추정치와 확정치 혼동 가능 |

## 세금/건보료 추정값 고지 점검
- 현재 근거: footer 면책 문구(`세무대리/법률자문이 아닌 참고용`)
- 부족한 점:
  - 추정치임을 요약/상세/결제 페이지에서 일관되게 노출하는 정책 문구 미정
  - 이용약관/정책 문서의 공식 문구 부재

## 오픈 전 필수 미완료 항목
1. 이용약관 초안 + 공개 URL
2. 개인정보처리방침 초안 + 공개 URL
3. 해지/환불/자동결제 안내 통합 문서 + 결제 페이지 링크
4. 문의처(인앱 외 대체 채널) 명시

## 권장 공개 위치
- Footer 고정 링크: `이용약관`, `개인정보처리방침`, `결제/해지 안내`, `문의`
- 결제 시작 CTA 근처: 자동결제/환불없음/해지시점 핵심 2~3줄 고지

## 결론
- 정책 문서 관점 현재 상태는 `오픈 전 필수 미완료`다.
- 기술 구현과 별개로 정책/신뢰 문서가 확보되지 않으면 실서비스 오픈은 `NO-GO`가 타당하다.

# Natural Flow Question Map

## 질문 우선순위 분류 기준

- `now_required`: 결과를 보여주기 전 최소한으로 필요한 질문
- `later_required`: 결과를 본 뒤 정확도를 올릴 때 필요한 질문
- `contextual_only`: 특정 기능 순간에만 필요한 질문
- `advanced_hidden`: 기본 흐름에서는 숨기고 필요한 경우만 노출

## 화면별 질문 분류

| 화면/흐름 | 질문 또는 입력 | 분류 | 추천 노출 시점 | 추천 톤 |
| --- | --- | --- | --- | --- |
| onboarding | 혼자 관리하시나요? | now_required | 가입 직후 | `혼자 보시는지, 같이 보는지만 알려주세요.` |
| onboarding | 한 달에 들어오는 돈은 어느 정도인가요? | now_required | 가입 직후 | `대충 골라도 괜찮아요.` |
| onboarding | 어떤 일을 주로 하세요? | later_required에 가까운 now_required | 가입 직후 마지막 | `정리 추천을 맞추려고 물어요.` |
| onboarding | 지금 가장 먼저 보고 싶은 건? | now_required | 가입 직후 마지막 | `첫 화면 우선순위를 맞출게요.` |
| overview | 소득 유형 1문항 | later_required | blocked 숫자 확인 직후 | `이것만 알면 세금 계산을 시작할 수 있어요.` |
| overview | 돈 받을 때 3.3%가 떼이는지 | later_required | 정확도 카드 클릭 후 | `예상세금이 더 현실에 가까워져요.` |
| overview | 올해 들어온 돈 / 일하면서 쓴 비용 | later_required | 정확도 카드 클릭 후 | `보관 금액이 덜 흔들려요.` |
| review | 이 거래가 업무인가요? | contextual_only | expense_confirm 순간 | `이걸 정하면 반영 가능성이 달라져요.` |
| review | 영수증을 붙일 수 있나요? | contextual_only | receipt_required / receipt_attach 순간 | `영수증이 있으면 비용 반영 설명이 쉬워져요.` |
| review | 거래처 식사인가요? / 주말 업무인가요? | contextual_only | follow-up 순간 | `왜 묻는지`와 함께 |
| review | 참석자/용도/관계/추가 메모 | contextual_only | reinforcement 순간 | `세무사 전달 자료까지 더 선명해져요.` |
| tax_buffer | 소득 유형 / 총수입 / 일하면서 쓴 비용 / 이미 빠진 세금 | later_required | 숫자를 본 뒤 | `이 정보 1개만 더 있으면 지금 숫자가 더 정확해져요.` |
| tax_profile step 1 | 업종 / 과세유형 / 전년도 수입 / 원천징수 | advanced_hidden | 기본 흐름에서는 뒤로 | `더 자세히 맞추고 싶을 때` |
| package | 경조사비/추가 서류/홈택스 자료 | contextual_only | 패키지 직전 | `세무사 전달 자료를 마무리할 때만` |

## 세무 용어 치환 후보

| 기존 표현 | 생활 언어 우선 표현 | 사용 정책 |
| --- | --- | --- |
| 원천징수 | 돈 받을 때 미리 빠진 세금 / 3.3% | 전면 문구는 생활 언어, 세무 용어는 보조 설명 |
| 과세유형 | 부가세를 따로 받는 방식 | 세부 화면에서만 보조 노출 |
| 필요경비 | 일하면서 쓴 비용 | 비용처리 안내/카드 우선 표현 |
| 반영 보류 | 아직 검토가 필요해요 | 상태 배지/토스트/안내 문구 |
| 고급 입력 | 더 자세히 맞추는 정보 | advanced hidden 구역 |

## 재노출 정책

- 동일한 세금 정확도 질문은 overview/review/tax_buffer에서 동시에 강하게 반복하지 않는다.
- review에서 receipt follow-up이 진행 중이면 overview에는 영수증 반영 가능성 카드만 요약으로 보여준다.
- `tax_profile step=2` 입력을 끝낸 사용자는 같은 정확도 카드를 같은 세션에서 약하게 줄인다.

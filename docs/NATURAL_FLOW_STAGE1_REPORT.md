# Natural Flow Stage 1 Report

## A. 현재 문제 요약

현재 흐름은 기능은 충분하지만, 첫 진입과 주요 화면에서 사용자가 바로 세무 용어와 입력 요구를 마주치는 구간이 남아 있다.

주요 문제는 아래와 같다.

- 온보딩이 결과 확인보다 설문 완료에 가깝다.
- 가입/로그인 직후 세금 기본 입력으로 바로 보내 결과보다 입력이 앞선다.
- `overview`, `review`, `tax_buffer`에서 숫자는 이미 보여주고 있는데, 추가 입력 CTA가 여전히 세무 설정 화면 중심이다.
- `업종`, `과세유형`, `원천징수`, `업무 경비`, `반영 보류` 같은 표현이 생활 언어 없이 먼저 나온다.
- follow-up / reinforcement / 비용처리 안내는 이미 잘 붙어 있지만, 초기 진입과 연결되는 보상 구조가 약하다.

화면별로 무거운 지점을 정리하면 아래와 같다.

| 화면 | 현재 무거운 지점 | 사용자 입장에서 왜 무거운가 | Stage 1 처리 방향 |
| --- | --- | --- | --- |
| 온보딩 | 프리랜서 유형, 월 소득 구간, 관리 방식, 목표를 설문형으로 연속 질문 | 결과를 보기 전부터 답을 맞춰야 하는 느낌이 강함 | 질문 순서를 생활 언어 기준으로 바꾸고 `결과 먼저` 카피로 전환 |
| 가입 직후 동선 | 온보딩 저장 후 `tax_profile step=2`로 직행 | 사용자가 첫 결과보다 입력 화면을 먼저 봄 | overview로 먼저 이동 |
| overview | recovery banner와 세금/건보 숫자 아래 세무 용어가 직접 노출 | 숫자는 보이는데 왜 더 입력해야 하는지 보상 구조가 약함 | `정확도/반영 가능성/전달 품질` 카드로 재구성 |
| review | `정확도를 위해 1분만 입력` 카드가 세무 프로필 입력 중심 | 지금 처리 중인 거래와 직접 연결이 약함 | `이 정보 1개만 더 있으면` 카드로 생활 언어 전환 |
| tax_buffer | recovery CTA가 입력 누락 중심으로 보임 | 사용자는 금고 숫자를 보고 있는데 다시 세무 입력으로 끌려감 | `지금 보이는 숫자를 더 정확하게 만들기` 카드로 변환 |
| tax_profile | step 1에 업종/과세유형/전년도 수입/원천징수 노출 | 결과를 보기 전에는 지나치게 전문적임 | 기본 진입은 step 2로 보내고 step 1은 뒤로 숨김 |
| receipt wizard / follow-up / reinforcement | 질문 구조는 맥락형이지만 별도 가이드 없으면 초반에 발견하기 어려움 | 기능은 있는데 처음엔 왜 필요한지 맥락이 약함 | overview/review/tax_buffer의 결과 개선 카드와 연결 |

## B. 초기 질문 축소 결과

Stage 1에서 초기 흐름은 아래 원칙으로 바꾼다.

- 온보딩은 4문항 유지하되 결과와 직접 연결되는 질문부터 앞에 둔다.
- 업종 분류 같은 질문은 남기되 `대충 골라도 괜찮아요` 톤으로 완화한다.
- 온보딩 저장 후에는 overview로 이동한다.
- 세금 기본 입력은 `정확도 올리기` CTA 뒤로 보낸다.

## C. 결과 먼저 구조 반영 결과

적용 구조는 아래와 같다.

1. 가입/로그인
2. 온보딩 4문항
3. overview 진입
4. 현재 상태 기준 숫자/할 일/반영 상태 확인
5. 필요한 경우에만
   - 세금 정확도 올리기
   - 영수증 반영 가능성 올리기
   - 세무사 전달 품질 올리기
   카드로 추가 행동 유도

## D. 정확도/반영 가능성 카드 반영 결과

Stage 1에서 사용하는 카드 유형은 아래 세 가지다.

| 카드 유형 | 노출 조건 | 사용자 문구 방향 | 연결 화면 |
| --- | --- | --- | --- |
| 세금 정확도 올리기 | 세금 프로필 미완료, blocked/limited | `이 정보 1개만 더 있으면 예상세금이 더 정확해져요.` | `tax_profile step=2` |
| 영수증 반영 가능성 올리기 | receipt required / receipt attach / expense confirm 남음 | `이 답변을 마치면 비용 반영 대상으로 바뀔 수 있어요.` | review / receipt 흐름 |
| 세무사 전달 품질 올리기 | package pass 아님, 증빙/메모 보강 필요 | `이 메모를 남기면 세무사 전달 자료가 더 명확해져요.` | package / review |

## E. 생활 언어 치환 결과

핵심 치환 방향은 아래와 같다.

- 원천징수 -> 돈 받을 때 미리 빠진 세금 / 3.3%
- 업무 경비 -> 일하면서 쓴 비용
- 과세유형 -> 부가세를 따로 받는 방식
- 반영 보류 -> 아직 검토가 필요해요
- 세금 설정 -> 가능한 경우 `정확도 올리기`, `기본 정보 이어서 입력` 같은 CTA로 우회

자세한 치환표는 [NATURAL_FLOW_COPY_GUIDE.md](/Users/tnifl/Desktop/SafeToSpend/docs/NATURAL_FLOW_COPY_GUIDE.md)에 정리한다.

## F. 상황형 질문 분산 결과

질문 분산 원칙은 아래와 같다.

- 세금 정확도 질문 -> overview / review / tax_buffer 카드에서 필요할 때만
- 영수증 반영 질문 -> review / receipt wizard 안에서만
- 추가 보강 질문 -> reinforcement 영역 안에서만
- 시즌성 질문(5월/11월) -> 이번 단계에서는 문서화만 하고 실제 전면 배치는 보류

## G. 테스트 결과

- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_natural_flow_entrypoints tests.test_natural_flow_progressive_questions tests.test_natural_flow_copy tests.test_new_user_required_input_gate tests.test_tax_nhis_ui_copy`
  - `Ran 16 tests in 0.016s`
  - `OK`
- `PYTHONPATH=. .venv/bin/python -m unittest tests.test_tax_single_step_flow tests.test_input_recovery_banner_priority tests.test_receipt_expense_guide_entrypoints`
  - `Ran 10 tests in 0.007s`
  - `OK`
- `PYTHONPATH=. .venv/bin/python -m py_compile routes/web/auth.py routes/web/calendar/review.py routes/web/calendar/tax.py services/risk.py routes/web/overview.py`
  - syntax check passed

## H. 남은 리스크

- 깊은 화면(`tax_profile`, package 상세)에는 세무 용어가 여전히 남아 있을 수 있다.
- 기존 고급 입력 경로는 숨겨졌지만 제거되지는 않았다.
- 시즌성 UX는 아직 이번 단계 범위 밖이다.
- 자동 추론 강화 없이 카피/흐름만 바꿔도 일부 사용자는 여전히 입력을 어렵게 느낄 수 있다.

## I. 다음 단계 연결 포인트

- 5월/11월 시즌 UX 분기
- 정확도 카드 클릭률/완료율 계측
- 자동 추론 강화
- WebKit/Firefox까지 QA 확대

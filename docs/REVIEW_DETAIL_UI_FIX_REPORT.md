# Review 상세 정보 표시 개선 보고서

## A. 문제 요약
- `/dashboard/review` 카드가 `source` 중심으로 보이면서, 사용자에게 더 중요한 거래 상대/시간/계좌/메모 정보가 충분히 드러나지 않았다.
- 실데이터 재검증 기준으로도 원천 데이터 부재 단독보다 UI 누락 비중이 높았다.

## B. 기존 표시 구조 문제
- 카드 타이틀이 `counterparty or memo or 알 수 없음` 단일 라인에 묶여 있어, 메모/상대방/출처를 구분해 읽기 어려웠다.
- `source`는 일부 케이스에서 사실상 유일하게 눈에 띄고, 계좌/메모는 보조 구조가 부족했다.
- 수입/지출 공통으로 “어디서 발생한 거래인지(계좌)”와 “언제 발생했는지(시각)”의 위계가 약했다.

## C. 필드 우선순위 정책 (고정)
### 1) 지출 카드
1. `display_title`: `counterparty` > `memo 요약` > `source 라벨`
2. `display_amount` + `display_time`
3. `display_account` + `display_source` (보조 라인)
4. `display_memo` (선택 노출)

### 2) 수입 카드
1. `display_title`: `counterparty` > `memo 요약` > `source 라벨`
2. `display_amount` + `display_time`
3. `display_account` + `display_source` (보조 라인)
4. `display_memo` (선택 노출)

### 3) 공통 fallback 규칙
- 타이틀: `counterparty` 없으면 `memo` 요약, 둘 다 없으면 `source` 라벨.
- 계좌: 연결 계좌명 없으면 `계좌 정보 없음`.
- 시각: `occurred_at` 없으면 `시간 정보 없음`.
- 출처: `source` 비면 `출처 미상`.
- 메모: 비어 있으면 노출하지 않음.
- 중복 제거: 동일 문자열을 한 카드 내에서 중복 노출하지 않음.

### 4) source별 필드 차이 대응
- `source`는 타이틀 기본값이 아니라 fallback용 보조 필드로 사용.
- 계좌/상대방/메모가 있으면 source는 하단 보조 라인으로만 노출.
- 실제 원천 데이터에 없는 값은 생성하지 않음.

## D. 라우트/직렬화 수정 내용
- `routes/web/calendar/review.py`에 템플릿 전용 뷰모델 빌더를 추가:
  - `_source_display_label`
  - `_review_time_display`
  - `_build_review_display_fields`
- 각 `item`에 아래 공통 필드를 주입:
  - `display_title`
  - `display_subtitle`
  - `display_time`
  - `display_amount`
  - `display_account`
  - `display_source`
  - `display_memo`
  - `raw_counterparty`
- 핵심 정책 반영:
  - `counterparty` 우선 title
  - `counterparty` 없으면 `memo` 요약
  - 둘 다 없으면 `source` 라벨 fallback
  - 계좌명 미식별(`미지정`/`선택 계좌`)은 `계좌 정보 없음` 처리
  - `None`/`null` 문자열이 화면에 직접 노출되지 않도록 정규화

## E. 템플릿 수정 내용
- `templates/calendar/review.html` 카드 구조를 4단계로 정리:
  1. title(상대/메모 요약/출처 fallback) + 상태 chip
  2. 금액 + 시각
  3. 계좌 + 출처(보조)
  4. 메모(선택) + 기존 reason
- 기존의 `tx.counterparty or tx.memo` 단일 노출을 제거하고 `display_*` 필드 중심으로 렌더링.
- source는 제목 기본값이 아닌 보조 라인으로 후순위 배치.
- `KB카드/스타뱅` 같은 source 단독 강조를 줄이고, 상대/금액/시간/계좌를 우선 노출.

## F. 테스트 결과
- `tests/test_review_detail_fields.py`
  - counterparty 우선 title
  - memo fallback title
  - source-only fallback
  - 계좌/시간 fallback
  - 동일 문자열 중복 메모 제거
  - 템플릿 안전 키 세트 보장
- `tests/test_review_detail_render.py`
  - `display_title/amount/time/account/source/memo` 렌더 경로 확인
  - 기존 단일식(`tx.counterparty or tx.memo`) 제거 확인
- 실행 명령:
  - `.venv/bin/python -m unittest tests.test_review_detail_fields tests.test_review_detail_render`
- 결과:
  - `Ran 9 tests ... OK`

## G. 실데이터 재검증 결과
- 실행 명령:
  - `PYTHONPATH=. .venv/bin/python scripts/revalidate_real_data_issues.py --matrix reports/real_data_issue_revalidation_matrix.json --summary reports/real_data_issue_revalidation_summary_after_review_fix.json`
- 재검증 케이스: 7개(CASE_A~G)
- 이슈2 분류 전/후:
  - 수정 전: `UI 누락 중심 5`, `혼합형 2`, `원천데이터 부족 중심 0`, `미재현 0`
  - 수정 후: `UI 누락 중심 0`, `혼합형 2`, `원천데이터 부족 중심 0`, `미재현 5`
- 해석:
  - UI 누락 중심 케이스는 제거됨.
  - 남은 2개 혼합형은 필드 밀도 편차(특히 counterparty 공백/패턴 불균일) 영향이 남은 케이스.

## H. 남은 리스크
- source별 원천 데이터 밀도 차이로 일부 카드는 fallback 비중이 남을 수 있다.
- 거래 원천 시스템이 `counterparty/memo`를 비워 보내는 경우, 개선 후에도 source fallback 카드는 남을 수 있다.
- 요약/검토 화면에서 “source-only”가 완전히 0이 되려면 import 단계 원천 필드 품질 개선이 별도 필요하다.

## I. 최종 판정
- **대부분 해소됨**
- 근거:
  - 실데이터 재검증에서 `UI 누락 중심 5 -> 0`으로 감소.
  - 템플릿이 `display_*` 기반으로 재구성되어 상세정보(상대/금액/시간/계좌/메모/출처)가 분리 노출됨.
- 잔여 과제:
  - 혼합형 2개 케이스는 원천 데이터 품질 편차 대응(추가 fallback 미세조정 또는 import 품질 개선) 추적 필요.

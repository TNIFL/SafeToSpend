# 영수증 비용처리 출력 계약 초안

목적:
- 규칙 엔진이 내려주는 결과를 UI와 문서, 테스트가 같은 의미로 해석하게 한다.

## 1. canonical 상태값

| level | 사용자 라벨 | guide anchor |
| --- | --- | --- |
| `high_likelihood` | 비용처리 가능성이 높은 편이에요 | `high-likelihood` |
| `needs_review` | 추가 확인이 필요해요 | `needs-review` |
| `do_not_auto_allow` | 자동으로 인정하지 않아요 | `do-not-auto` |
| `consult_tax_review` | 세무 검토가 필요할 수 있어요 | `consult` |

## 2. 출력 필드

| 필드명 | 타입 | 필수 여부 | 설명 |
| --- | --- | --- | --- |
| `level` | string | 필수 | 위 4개 canonical 상태값 중 하나 |
| `label` | string | 필수 | 사용자에게 바로 보여줄 짧은 라벨 |
| `summary` | string | 필수 | 한 줄 요약 설명 |
| `why` | string | 필수 | 왜 이런 상태로 봤는지에 대한 짧은 이유 |
| `guide_anchor` | string | 필수 | `/guide/expense`의 연결 anchor |
| `follow_up_questions` | array[string] | 선택 | 추가 확인이 필요한 질문 목록 |
| `evidence_requirements` | array[string] | 선택 | 추가로 요구할 증빙/메모 안내 |
| `official_source_refs` | array[string] | 권장 | 내부 근거 식별자 또는 공식 출처 키 |
| `confidence_note` | string | 선택 | 자동 인정이 아님을 보완 설명하는 문구 |

## 3. JSON 예시

```json
{
  "level": "needs_review",
  "label": "추가 확인이 필요해요",
  "summary": "카페·식비·주말 결제는 업무와 개인 사용이 섞이기 쉬워요.",
  "why": "영수증만으로는 회의비인지 개인 소비인지 단정하기 어렵습니다.",
  "guide_anchor": "needs-review",
  "follow_up_questions": [
    "누구와 어떤 목적으로 사용했나요?",
    "업무와 직접 관련된 지출인가요?"
  ],
  "evidence_requirements": [
    "거래 목적 메모",
    "참석자 또는 거래처 정보"
  ],
  "official_source_refs": [
    "income_tax_act_article_27",
    "income_tax_act_article_33"
  ],
  "confidence_note": "서비스의 분류 결과는 보조 판단입니다."
}
```

## 4. 현재 안내 UX와의 연결 규칙
- 현재 UI의 라벨 체계와 반드시 1:1로 연결한다.
- anchor 값은 `/guide/expense` 섹션 ID와 반드시 일치한다.
- `summary`는 카드용 1줄, `why`는 인라인 설명용 1~2줄로 유지한다.
- `follow_up_questions`가 비어 있지 않으면 UI는 추가 입력/확인 단계로 연결할 수 있어야 한다.

## 5. 금지 원칙
- `level=high_likelihood`라고 해서 자동 확정 문구를 내려주지 않는다.
- `do_not_auto_allow`를 개인지출 확정으로 단정하는 문구로 쓰지 않는다.
- 공식 근거가 약한 항목을 `high_likelihood`로 올리지 않는다.

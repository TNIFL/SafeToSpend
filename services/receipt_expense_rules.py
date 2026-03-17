from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Any

from core.time import utcnow
from sqlalchemy.exc import ProgrammingError


RECEIPT_EXPENSE_LEVELS: dict[str, dict[str, str]] = {
    "high_likelihood": {
        "label": "비용처리 가능성이 높은 편이에요",
        "guide_anchor": "high-likelihood",
        "tone": "high",
    },
    "needs_review": {
        "label": "추가 확인이 필요해요",
        "guide_anchor": "needs-review",
        "tone": "review",
    },
    "do_not_auto_allow": {
        "label": "자동으로 인정하지 않아요",
        "guide_anchor": "do-not-auto",
        "tone": "block",
    },
    "consult_tax_review": {
        "label": "세무 검토가 필요할 수 있어요",
        "guide_anchor": "consult",
        "tone": "consult",
    },
}

DEFAULT_CONFIDENCE_NOTE = "서비스의 분류 결과는 보조 판단입니다."

SOURCE_REF_ARTICLE_27 = "income_tax_act_article_27"
SOURCE_REF_ARTICLE_33 = "income_tax_act_article_33"
SOURCE_REF_ARTICLE_35 = "income_tax_act_article_35"
SOURCE_REF_ARTICLE_160_2 = "income_tax_act_article_160_2"
SOURCE_REF_DECREE_208_2 = "income_tax_act_enforcement_decree_article_208_2"

TRANSPORT_KEYWORDS = (
    "ktx",
    "srt",
    "택시",
    "버스",
    "지하철",
    "교통",
    "주차",
    "통행료",
    "톨게이트",
    "출장",
    "유류",
    "주유",
)
BOOKS_EDU_PRINT_KEYWORDS = (
    "도서",
    "서점",
    "교재",
    "교육",
    "강의",
    "세미나",
    "클래스",
    "인쇄",
    "복사",
    "프린트",
    "출력",
    "우편",
    "택배",
    "배송",
)
OFFICE_SUPPLIES_KEYWORDS = (
    "문구",
    "사무용품",
    "소모품",
    "복사용지",
    "토너",
    "잉크",
    "다이소",
    "stationery",
)
MEAL_CAFE_KEYWORDS = (
    "스타벅스",
    "카페",
    "커피",
    "음료",
    "식당",
    "레스토랑",
    "점심",
    "저녁",
    "식비",
    "베이커리",
    "샌드위치",
    "브런치",
    "치킨",
    "피자",
)
BUSINESS_MEAL_KEYWORDS = (
    "거래처",
    "고객",
    "미팅",
    "회의",
    "접대",
    "상담",
    "파트너",
    "협력사",
    "업무",
    "방문",
)
PERSONAL_SPEND_KEYWORDS = (
    "본인 식사",
    "개인 식사",
    "혼밥",
    "생활비",
    "가사",
    "가정용",
    "개인",
    "가족",
)
HIGH_VALUE_ASSET_KEYWORDS = (
    "노트북",
    "맥북",
    "아이패드",
    "태블릿",
    "모니터",
    "카메라",
    "렌즈",
    "의자",
    "책상",
    "가구",
    "전자기기",
    "휴대폰",
    "스마트폰",
    "애플스토어",
    "apple store",
    "apple",
)
CONDOLENCE_GIFT_KEYWORDS = (
    "경조사",
    "축의",
    "부의",
    "조의",
    "화환",
    "선물",
    "gift",
    "기프티콘",
)
MIXED_SPENDING_KEYWORDS = (
    "편의점",
    "마트",
    "쿠팡",
    "온라인쇼핑",
    "쇼핑",
    "올리브영",
    "생활용품",
)

FOLLOW_UP_QUESTION_SPECS: dict[str, dict[str, Any]] = {
    "business_meal_with_client": {
        "prompt": "거래처와의 식사인가요?",
        "input_type": "boolean",
        "choices": (
            {"value": "yes", "label": "네"},
            {"value": "no", "label": "아니오"},
        ),
        "allow_text": True,
        "text_label": "거래처/목적 메모",
        "text_placeholder": "예: A사 미팅 · 참석자 2명",
    },
    "weekend_or_late_night_business_reason": {
        "prompt": "주말·심야 결제 사유를 남길 수 있나요?",
        "input_type": "text",
        "allow_text": True,
        "text_label": "업무 관련 메모",
        "text_placeholder": "예: 토요일 출장 이동, 야간 외근",
    },
    "asset_vs_consumable": {
        "prompt": "업무용 자산인가요, 소모품인가요?",
        "input_type": "choice",
        "choices": (
            {"value": "asset", "label": "업무용 자산"},
            {"value": "consumable", "label": "소모품/소액품"},
            {"value": "unknown", "label": "아직 모르겠어요"},
        ),
        "allow_text": True,
        "text_label": "사용 목적 메모",
        "text_placeholder": "예: 촬영 업무용, 사무실 비치용",
    },
    "ceremonial_business_related": {
        "prompt": "업무 관련 경조사비인가요?",
        "input_type": "boolean",
        "choices": (
            {"value": "yes", "label": "네"},
            {"value": "no", "label": "아니오"},
        ),
        "allow_text": True,
        "text_label": "관련 메모",
        "text_placeholder": "예: 거래처 경조사 · 대상자 메모",
    },
    "personal_meal_exception_reason": {
        "prompt": "개인 식사가 아니라면 업무 관련 메모를 남길 수 있나요?",
        "input_type": "text",
        "allow_text": True,
        "text_label": "업무 관련 메모",
        "text_placeholder": "예: 고객 미팅 중 식사, 외근 중 식사",
    },
    "mixed_spend_business_context": {
        "prompt": "업무용 구매라면 어떤 용도인지 적어줄 수 있나요?",
        "input_type": "text",
        "allow_text": True,
        "text_label": "업무 관련 메모",
        "text_placeholder": "예: 촬영 소모품, 고객 발송용 물품",
    },
}

FOLLOW_UP_TEXT_PROMOTION_KEYWORDS = (
    "업무",
    "거래처",
    "미팅",
    "회의",
    "고객",
    "협력사",
    "외근",
    "출장",
    "상담",
    "방문",
)

REINFORCEMENT_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "business_context_note": {
        "label": "업무 관련 설명",
        "input_type": "textarea",
        "placeholder": "예: 고객사 미팅 준비, 출장 이동, 발송 업무",
        "help": "어떤 업무와 연결되는 지출인지 짧게 남겨 주세요.",
    },
    "attendee_names": {
        "label": "참석자",
        "input_type": "text",
        "placeholder": "예: 홍길동 팀장, A사 이OO",
        "help": "식사·미팅이라면 함께한 사람을 남겨 주세요.",
    },
    "client_or_counterparty_name": {
        "label": "거래처/상대방",
        "input_type": "text",
        "placeholder": "예: A사, B파트너",
        "help": "업무 상대방이 있다면 남겨 주세요.",
    },
    "ceremonial_relation_note": {
        "label": "관계/행사 설명",
        "input_type": "textarea",
        "placeholder": "예: 거래처 경조사, 업무 관련 행사",
        "help": "경조사·선물이라면 어떤 관계인지 적어 주세요.",
    },
    "asset_usage_note": {
        "label": "자산/소모품 용도",
        "input_type": "textarea",
        "placeholder": "예: 촬영 업무용, 사무실 비치용",
        "help": "고가 장비·가구는 업무용 사용 목적을 남겨 주세요.",
    },
    "weekend_or_late_night_note": {
        "label": "주말·심야 사유",
        "input_type": "textarea",
        "placeholder": "예: 토요일 고객 미팅, 야간 출장 이동",
        "help": "주말·심야 결제라면 업무 사유를 남겨 주세요.",
    },
    "supporting_file": {
        "label": "보강 파일",
        "input_type": "file",
        "placeholder": "",
        "help": "일정표, 참석자 메모, 관련 자료가 있으면 함께 첨부해 주세요.",
    },
}


@dataclass(slots=True)
class ReceiptExpenseInput:
    merchant_name: str = ""
    approved_at: datetime | None = None
    amount_krw: int = 0
    payment_method: str = ""
    source_text_raw: str = ""
    source_text_normalized: str = ""
    candidate_transaction_id: int | None = None
    counterparty: str = ""
    memo: str = ""
    weekend_flag: bool = False
    late_night_flag: bool = False
    receipt_type: str = ""
    business_context_note: str = ""
    attendee_note: str = ""
    client_or_counterparty_name: str = ""
    ceremonial_relation_note: str = ""
    asset_usage_note: str = ""
    weekend_or_late_night_note: str = ""
    supporting_file_present: bool = False
    supporting_file_name: str = ""
    evidence_kind: str = ""
    focus_kind: str = ""
    follow_up_answers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.approved_at is not None:
            payload["approved_at"] = self.approved_at.isoformat(sep=" ", timespec="minutes")
        return payload


def _clean_text(value: Any, *, lower: bool = False) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if lower:
        text = text.lower()
    return text


def _parse_amount(value: Any) -> int:
    text = re.sub(r"[^0-9-]", "", str(value or ""))
    if not text:
        return 0
    try:
        return int(text)
    except Exception:
        return 0


def _parse_int_optional(value: Any) -> int | None:
    text = re.sub(r"[^0-9-]", "", str(value or "")).strip()
    if not text:
        return None
    try:
        return int(text)
    except Exception:
        return None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _clean_text(value, lower=True)
    if not text:
        return None
    if text in {"1", "true", "t", "y", "yes", "on"}:
        return True
    if text in {"0", "false", "f", "n", "no", "off"}:
        return False
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _clean_text(value)
    if not text:
        return None
    candidates = (
        text,
        text.replace("T", " "),
        text.replace(".", "-"),
        text.replace("/", "-"),
    )
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            continue
    return None


def _normalize_source_text(raw: str) -> str:
    text = _clean_text(raw, lower=True)
    text = re.sub(r"[^\w\s가-힣]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _build_context_text(normalized: ReceiptExpenseInput) -> str:
    parts = (
        normalized.merchant_name,
        normalized.counterparty,
        normalized.memo,
        normalized.payment_method,
        normalized.source_text_normalized,
        normalized.business_context_note,
        normalized.attendee_note,
        normalized.client_or_counterparty_name,
        normalized.ceremonial_relation_note,
        normalized.asset_usage_note,
        normalized.weekend_or_late_night_note,
        normalized.receipt_type,
        normalized.evidence_kind,
    )
    return _clean_text(" ".join(part for part in parts if part), lower=True)


def _normalize_answer_value(value: Any) -> str:
    return _clean_text(value, lower=True)


def normalize_follow_up_answers(raw: Any) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}

    entries: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            key = _clean_text(row.get("question_key"))
            if key:
                entries.append((key, row))

    normalized: dict[str, dict[str, Any]] = {}
    for question_key, payload in entries:
        if question_key not in FOLLOW_UP_QUESTION_SPECS:
            continue
        if isinstance(payload, dict):
            answer_value = _normalize_answer_value(payload.get("answer_value"))
            answer_text = _clean_text(payload.get("answer_text"))
            answered_at = _parse_datetime(payload.get("answered_at"))
            answered_by = _parse_int_optional(payload.get("answered_by"))
        else:
            answer_value = _normalize_answer_value(payload)
            answer_text = ""
            answered_at = None
            answered_by = None
        if not answer_value and not answer_text:
            continue
        normalized[question_key] = {
            "answer_value": answer_value,
            "answer_text": answer_text,
            "answered_at": answered_at.isoformat(sep=" ", timespec="minutes") if answered_at else "",
            "answered_by": answered_by,
        }
    return normalized


def normalize_reinforcement_payload(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raw = {
            "business_context_note": getattr(raw, "business_context_note", ""),
            "attendee_names": getattr(raw, "attendee_names", ""),
            "client_or_counterparty_name": getattr(raw, "client_or_counterparty_name", ""),
            "ceremonial_relation_note": getattr(raw, "ceremonial_relation_note", ""),
            "asset_usage_note": getattr(raw, "asset_usage_note", ""),
            "weekend_or_late_night_note": getattr(raw, "weekend_or_late_night_note", ""),
            "supporting_file_key": getattr(raw, "supporting_file_key", ""),
            "supporting_file_name": getattr(raw, "supporting_file_name", ""),
            "supporting_file_mime_type": getattr(raw, "supporting_file_mime_type", ""),
            "supporting_file_size_bytes": getattr(raw, "supporting_file_size_bytes", 0),
            "supporting_file_uploaded_at": getattr(raw, "supporting_file_uploaded_at", None),
            "updated_at": getattr(raw, "updated_at", None),
        }

    supporting_file_uploaded_at = _parse_datetime(raw.get("supporting_file_uploaded_at"))
    updated_at = _parse_datetime(raw.get("updated_at"))
    supporting_file_size_bytes = _parse_int_optional(raw.get("supporting_file_size_bytes")) or 0
    supporting_file_key = str(raw.get("supporting_file_key") or "").strip()
    supporting_file_name = _clean_text(raw.get("supporting_file_name"))
    normalized = {
        "business_context_note": _clean_text(raw.get("business_context_note")),
        "attendee_names": _clean_text(raw.get("attendee_names")),
        "client_or_counterparty_name": _clean_text(raw.get("client_or_counterparty_name")),
        "ceremonial_relation_note": _clean_text(raw.get("ceremonial_relation_note")),
        "asset_usage_note": _clean_text(raw.get("asset_usage_note")),
        "weekend_or_late_night_note": _clean_text(raw.get("weekend_or_late_night_note")),
        "supporting_file_key": supporting_file_key,
        "supporting_file_name": supporting_file_name,
        "supporting_file_mime_type": _clean_text(raw.get("supporting_file_mime_type")),
        "supporting_file_size_bytes": supporting_file_size_bytes,
        "supporting_file_uploaded_at": supporting_file_uploaded_at.isoformat(sep=" ", timespec="minutes")
        if supporting_file_uploaded_at
        else "",
        "updated_at": updated_at.isoformat(sep=" ", timespec="minutes") if updated_at else "",
    }
    normalized["supporting_file_present"] = bool(normalized["supporting_file_key"] or normalized["supporting_file_name"])
    return normalized


def extract_reinforcement_payload_from_form(form: Any, *, prefix: str = "reinforce__") -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    if form is None:
        return extracted
    try:
        keys = list(form.keys())
    except Exception:
        return extracted
    for raw_key in keys:
        key = str(raw_key or "")
        if not key.startswith(prefix):
            continue
        field_key = key[len(prefix) :]
        if field_key not in REINFORCEMENT_FIELD_SPECS:
            continue
        if field_key == "supporting_file":
            continue
        extracted[field_key] = form.get(raw_key)
    return extracted


def validate_reinforcement_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        unknown = [str(key or "") for key in raw.keys() if str(key or "") not in REINFORCEMENT_FIELD_SPECS and not str(key or "").startswith("supporting_file_")]
        if unknown:
            raise ValueError(f"invalid_reinforcement_key:{unknown[0]}")
    normalized = normalize_reinforcement_payload(raw)
    supported_fields = {
        "business_context_note",
        "attendee_names",
        "client_or_counterparty_name",
        "ceremonial_relation_note",
        "asset_usage_note",
        "weekend_or_late_night_note",
        "supporting_file_key",
        "supporting_file_name",
        "supporting_file_mime_type",
        "supporting_file_size_bytes",
        "supporting_file_uploaded_at",
    }
    return {key: value for key, value in normalized.items() if key in supported_fields and value}


def is_valid_follow_up_question_key(question_key: str) -> bool:
    return question_key in FOLLOW_UP_QUESTION_SPECS


def extract_follow_up_answers_from_form(form: Any, *, prefix: str = "followup__") -> dict[str, dict[str, Any]]:
    extracted: dict[str, dict[str, Any]] = {}
    if form is None:
        return extracted

    keys = []
    try:
        keys = list(form.keys())
    except Exception:
        return extracted

    for raw_key in keys:
        key = str(raw_key or "")
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix) :]
        if "__" not in suffix:
            continue
        question_key, field_name = suffix.split("__", 1)
        if question_key not in FOLLOW_UP_QUESTION_SPECS:
            continue
        bucket = extracted.setdefault(question_key, {"answer_value": "", "answer_text": ""})
        value = form.get(raw_key)
        if field_name == "value":
            bucket["answer_value"] = _normalize_answer_value(value)
        elif field_name == "text":
            bucket["answer_text"] = _clean_text(value)
    return normalize_follow_up_answers(extracted)


def validate_follow_up_answers_payload(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, dict):
        unknown = [str(key or "") for key in raw.keys() if str(key or "") not in FOLLOW_UP_QUESTION_SPECS]
        if unknown:
            raise ValueError(f"invalid_question_key:{unknown[0]}")
    elif isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            question_key = str(row.get("question_key") or "")
            if question_key and question_key not in FOLLOW_UP_QUESTION_SPECS:
                raise ValueError(f"invalid_question_key:{question_key}")
    normalized = normalize_follow_up_answers(raw)
    validated: dict[str, dict[str, Any]] = {}
    for question_key, answer in normalized.items():
        spec = FOLLOW_UP_QUESTION_SPECS[question_key]
        answer_value = str(answer.get("answer_value") or "")
        answer_text = _clean_text(answer.get("answer_text"))
        input_type = str(spec.get("input_type") or "")
        if input_type == "boolean":
            if answer_value not in {"yes", "no"}:
                raise ValueError(f"invalid_answer_value:{question_key}")
        elif input_type == "choice":
            choices = {str(row["value"]) for row in spec.get("choices") or []}
            if answer_value and answer_value not in choices:
                raise ValueError(f"invalid_answer_value:{question_key}")
        elif input_type == "text":
            if not answer_text and answer_value:
                answer_text = answer_value
                answer_value = ""
        if not answer_value and not answer_text:
            continue
        validated[question_key] = {
            "answer_value": answer_value,
            "answer_text": answer_text,
        }
    return validated


def _is_missing_follow_up_table_error(exc: Exception) -> bool:
    text = _clean_text(exc, lower=True)
    return "receipt_expense_followup_answers" in text and (
        "does not exist" in text or "undefinedtable" in text or "undefined table" in text
    )


def _is_missing_reinforcement_table_error(exc: Exception) -> bool:
    text = _clean_text(exc, lower=True)
    return "receipt_expense_reinforcements" in text and (
        "does not exist" in text or "undefinedtable" in text or "undefined table" in text
    )


def _follow_up_answer(normalized: ReceiptExpenseInput, question_key: str) -> dict[str, Any]:
    return dict(normalized.follow_up_answers.get(question_key) or {})


def _follow_up_value(normalized: ReceiptExpenseInput, question_key: str) -> str:
    return str(_follow_up_answer(normalized, question_key).get("answer_value") or "")


def _follow_up_text(normalized: ReceiptExpenseInput, question_key: str) -> str:
    return str(_follow_up_answer(normalized, question_key).get("answer_text") or "")


def _has_meaningful_text(value: str) -> bool:
    return len(_clean_text(value)) >= 4


def _text_has_business_context(value: str) -> bool:
    text = _clean_text(value, lower=True)
    if not text:
        return False
    return _contains_any(text, FOLLOW_UP_TEXT_PROMOTION_KEYWORDS)


def _build_follow_up_question(normalized: ReceiptExpenseInput, question_key: str) -> dict[str, Any]:
    spec = FOLLOW_UP_QUESTION_SPECS[question_key]
    answer = _follow_up_answer(normalized, question_key)
    return {
        "question_key": question_key,
        "prompt": spec["prompt"],
        "input_type": spec["input_type"],
        "choices": list(spec.get("choices") or []),
        "allow_text": bool(spec.get("allow_text")),
        "text_label": str(spec.get("text_label") or ""),
        "text_placeholder": str(spec.get("text_placeholder") or ""),
        "current_value": str(answer.get("answer_value") or ""),
        "current_text": str(answer.get("answer_text") or ""),
    }


def _summarize_applied_answer(question_key: str, *, answer_value: str, answer_text: str) -> str:
    spec = FOLLOW_UP_QUESTION_SPECS[question_key]
    if spec["input_type"] == "boolean":
        value_label = "네" if answer_value == "yes" else ("아니오" if answer_value == "no" else "미선택")
    elif spec["input_type"] == "choice":
        choices = {str(row["value"]): str(row["label"]) for row in spec.get("choices") or []}
        value_label = choices.get(answer_value, answer_value or "미선택")
    else:
        value_label = ""
    detail = _clean_text(answer_text)
    if value_label and detail:
        return f"{value_label} · {detail}"
    if detail:
        return detail
    return value_label


def _build_applied_follow_up_answer(normalized: ReceiptExpenseInput, question_key: str) -> dict[str, Any] | None:
    answer = _follow_up_answer(normalized, question_key)
    answer_value = str(answer.get("answer_value") or "")
    answer_text = str(answer.get("answer_text") or "")
    if not answer_value and not answer_text:
        return None
    return {
        "question_key": question_key,
        "prompt": FOLLOW_UP_QUESTION_SPECS[question_key]["prompt"],
        "answer_value": answer_value,
        "answer_text": answer_text,
        "summary": _summarize_applied_answer(question_key, answer_value=answer_value, answer_text=answer_text),
        "answered_at": str(answer.get("answered_at") or ""),
        "answered_by": answer.get("answered_by"),
    }


def _reinforcement_value(normalized: ReceiptExpenseInput, field_key: str) -> Any:
    if field_key == "business_context_note":
        return normalized.business_context_note
    if field_key == "attendee_names":
        return normalized.attendee_note
    if field_key == "client_or_counterparty_name":
        return normalized.client_or_counterparty_name
    if field_key == "ceremonial_relation_note":
        return normalized.ceremonial_relation_note
    if field_key == "asset_usage_note":
        return normalized.asset_usage_note
    if field_key == "weekend_or_late_night_note":
        return normalized.weekend_or_late_night_note
    if field_key == "supporting_file":
        return normalized.supporting_file_present
    return ""


def _has_reinforcement_value(normalized: ReceiptExpenseInput, field_key: str) -> bool:
    value = _reinforcement_value(normalized, field_key)
    if field_key == "supporting_file":
        return bool(value)
    text = _clean_text(str(value or ""))
    if field_key in {"client_or_counterparty_name", "attendee_names"}:
        return len(text) >= 2
    return _has_meaningful_text(text)


def _build_reinforcement_requirement(field_key: str) -> dict[str, Any]:
    spec = REINFORCEMENT_FIELD_SPECS[field_key]
    return {
        "field_key": field_key,
        "label": str(spec.get("label") or field_key),
        "input_type": str(spec.get("input_type") or "text"),
        "placeholder": str(spec.get("placeholder") or ""),
        "help": str(spec.get("help") or ""),
    }


def _build_applied_reinforcement(normalized: ReceiptExpenseInput, field_key: str) -> dict[str, Any] | None:
    spec = REINFORCEMENT_FIELD_SPECS[field_key]
    if field_key == "supporting_file":
        if not normalized.supporting_file_present:
            return None
        summary = normalized.supporting_file_name or "보강 파일 업로드됨"
    else:
        raw = _clean_text(_reinforcement_value(normalized, field_key))
        if not _has_meaningful_text(raw):
            return None
        summary = raw
    return {
        "field_key": field_key,
        "label": str(spec.get("label") or field_key),
        "summary": summary,
    }


def _build_reinforcement_state(
    normalized: ReceiptExpenseInput,
    *,
    required_keys: list[str] | tuple[str, ...] | None = None,
    optional_keys: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    required = [key for key in (required_keys or []) if key in REINFORCEMENT_FIELD_SPECS]
    optional = [key for key in (optional_keys or []) if key in REINFORCEMENT_FIELD_SPECS and key not in required]
    requirement_rows = [_build_reinforcement_requirement(key) for key in (*required, *optional)]
    applied_rows = [
        row
        for row in (
            _build_applied_reinforcement(normalized, key)
            for key in dict.fromkeys([*required, *optional])
        )
        if row
    ]
    remaining_gaps = [
        REINFORCEMENT_FIELD_SPECS[key]["label"]
        for key in required
        if not _has_reinforcement_value(normalized, key)
    ]
    satisfied_required = len(required) - len(remaining_gaps)
    if not required:
        readiness = "not_needed"
    elif satisfied_required <= 0:
        readiness = "none"
    elif not remaining_gaps:
        readiness = "sufficient"
    else:
        readiness = "partial"

    if readiness == "not_needed":
        summary = "지금 단계에서 추가 보강이 꼭 필요하진 않아요."
    elif readiness == "none":
        summary = "추가 보강 정보가 아직 없어요."
    elif readiness == "partial":
        summary = "일부 보강 정보가 반영됐지만 아직 확인할 항목이 남아 있어요."
    else:
        summary = "요청된 보강 정보가 대부분 반영됐어요."

    return {
        "reinforcement_requirements": requirement_rows,
        "applied_reinforcement": applied_rows,
        "remaining_gaps": remaining_gaps,
        "reinforcement_readiness": readiness,
        "reinforcement_summary": summary,
    }


def normalize_receipt_expense_input(
    payload: dict[str, Any] | None = None,
    *,
    tx: Any | None = None,
    draft: dict[str, Any] | None = None,
    focus_kind: str = "",
    receipt_type: str = "",
    follow_up_answers: Any | None = None,
    reinforcement_data: Any | None = None,
) -> ReceiptExpenseInput:
    payload = dict(payload or {})
    draft = dict(draft or {})
    reinforcement = normalize_reinforcement_payload(reinforcement_data)

    merchant_name = _clean_text(
        payload.get("merchant_name")
        or draft.get("merchant")
        or getattr(tx, "counterparty", ""),
    )
    approved_at = _parse_datetime(
        payload.get("approved_at")
        or draft.get("paid_at")
        or getattr(tx, "occurred_at", None)
    )
    amount_krw = _parse_amount(
        payload.get("amount_krw")
        or draft.get("total_krw")
        or getattr(tx, "amount_krw", 0)
    )
    payment_method = _clean_text(payload.get("payment_method") or draft.get("payment_method") or "")
    source_text_raw = _clean_text(payload.get("source_text_raw") or draft.get("source_text_raw") or "")
    source_text_normalized = _clean_text(
        payload.get("source_text_normalized") or draft.get("source_text_normalized") or ""
    )
    if not source_text_normalized and source_text_raw:
        source_text_normalized = _normalize_source_text(source_text_raw)

    candidate_transaction_id = _parse_int_optional(
        payload.get("candidate_transaction_id") or getattr(tx, "id", None)
    )
    counterparty = _clean_text(payload.get("counterparty") or getattr(tx, "counterparty", ""))
    memo = _clean_text(payload.get("memo") or getattr(tx, "memo", ""))

    weekend_flag = _parse_bool(payload.get("weekend_flag"))
    late_night_flag = _parse_bool(payload.get("late_night_flag"))
    if approved_at is not None:
        if weekend_flag is None:
            weekend_flag = approved_at.weekday() >= 5
        if late_night_flag is None:
            late_night_flag = approved_at.hour >= 22 or approved_at.hour < 6

    normalized = ReceiptExpenseInput(
        merchant_name=merchant_name,
        approved_at=approved_at,
        amount_krw=int(amount_krw or 0),
        payment_method=payment_method,
        source_text_raw=source_text_raw,
        source_text_normalized=source_text_normalized,
        candidate_transaction_id=candidate_transaction_id,
        counterparty=counterparty,
        memo=memo,
        weekend_flag=bool(weekend_flag),
        late_night_flag=bool(late_night_flag),
        receipt_type=_clean_text(payload.get("receipt_type") or receipt_type or draft.get("receipt_type") or ""),
        business_context_note=_clean_text(
            payload.get("business_context_note")
            or reinforcement.get("business_context_note")
            or draft.get("business_context_note")
            or ""
        ),
        attendee_note=_clean_text(
            payload.get("attendee_note")
            or payload.get("attendee_names")
            or reinforcement.get("attendee_names")
            or draft.get("attendee_note")
            or ""
        ),
        client_or_counterparty_name=_clean_text(
            payload.get("client_or_counterparty_name")
            or reinforcement.get("client_or_counterparty_name")
            or ""
        ),
        ceremonial_relation_note=_clean_text(
            payload.get("ceremonial_relation_note")
            or reinforcement.get("ceremonial_relation_note")
            or ""
        ),
        asset_usage_note=_clean_text(
            payload.get("asset_usage_note")
            or reinforcement.get("asset_usage_note")
            or ""
        ),
        weekend_or_late_night_note=_clean_text(
            payload.get("weekend_or_late_night_note")
            or reinforcement.get("weekend_or_late_night_note")
            or ""
        ),
        supporting_file_present=bool(
            _parse_bool(payload.get("supporting_file_present"))
            if _parse_bool(payload.get("supporting_file_present")) is not None
            else reinforcement.get("supporting_file_present")
        ),
        supporting_file_name=_clean_text(
            payload.get("supporting_file_name")
            or reinforcement.get("supporting_file_name")
            or ""
        ),
        evidence_kind=_clean_text(payload.get("evidence_kind") or draft.get("evidence_kind") or ""),
        focus_kind=_clean_text(payload.get("focus_kind") or focus_kind or "", lower=True),
        follow_up_answers=normalize_follow_up_answers(
            payload.get("follow_up_answers") if isinstance(payload.get("follow_up_answers"), (dict, list)) else follow_up_answers
        ),
    )
    return normalized


def _build_decision(
    level: str,
    *,
    summary: str,
    why: str,
    follow_up_questions: list[dict[str, Any]] | None = None,
    applied_follow_up_answers: list[dict[str, Any]] | None = None,
    evidence_requirements: list[str] | None = None,
    official_source_refs: list[str] | None = None,
    confidence_note: str | None = None,
    reinforcement_requirements: list[dict[str, Any]] | None = None,
    reinforcement_summary: str | None = None,
    remaining_gaps: list[str] | None = None,
    reinforcement_readiness: str | None = None,
    applied_reinforcement: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    meta = RECEIPT_EXPENSE_LEVELS[level]
    return {
        "level": level,
        "label": meta["label"],
        "summary": summary,
        "why": why,
        "guide_anchor": meta["guide_anchor"],
        "follow_up_questions": list(follow_up_questions or []),
        "applied_follow_up_answers": list(applied_follow_up_answers or []),
        "evidence_requirements": list(evidence_requirements or []),
        "official_source_refs": list(official_source_refs or []),
        "confidence_note": str(confidence_note or DEFAULT_CONFIDENCE_NOTE),
        "reinforcement_requirements": list(reinforcement_requirements or []),
        "reinforcement_summary": str(reinforcement_summary or ""),
        "remaining_gaps": list(remaining_gaps or []),
        "reinforcement_readiness": str(reinforcement_readiness or "not_needed"),
        "applied_reinforcement": list(applied_reinforcement or []),
    }


def evaluate_receipt_expense(input_data: ReceiptExpenseInput | dict[str, Any]) -> dict[str, Any]:
    normalized = (
        input_data
        if isinstance(input_data, ReceiptExpenseInput)
        else normalize_receipt_expense_input(dict(input_data or {}))
    )
    text = _build_context_text(normalized)
    has_transport = _contains_any(text, TRANSPORT_KEYWORDS)
    has_books_edu_print = _contains_any(text, BOOKS_EDU_PRINT_KEYWORDS)
    has_office_supplies = _contains_any(text, OFFICE_SUPPLIES_KEYWORDS)
    has_meal_like = _contains_any(text, MEAL_CAFE_KEYWORDS)
    has_business_meal_context = _contains_any(text, BUSINESS_MEAL_KEYWORDS) or bool(
        normalized.attendee_note or normalized.client_or_counterparty_name
    )
    has_personal_context = _contains_any(text, PERSONAL_SPEND_KEYWORDS)
    has_high_value_asset = _contains_any(text, HIGH_VALUE_ASSET_KEYWORDS) or normalized.amount_krw >= 1_000_000
    has_gift_condolence = _contains_any(text, CONDOLENCE_GIFT_KEYWORDS)
    has_mixed_spending = _contains_any(text, MIXED_SPENDING_KEYWORDS)
    has_risky_time = bool(normalized.weekend_flag or normalized.late_night_flag)
    confidence_note = DEFAULT_CONFIDENCE_NOTE

    def q(key: str) -> list[dict[str, Any]]:
        return [_build_follow_up_question(normalized, key)]

    def applied(*keys: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in keys:
            row = _build_applied_follow_up_answer(normalized, key)
            if row:
                rows.append(row)
        return rows

    def reinforce(*, required: tuple[str, ...] = (), optional: tuple[str, ...] = ()) -> dict[str, Any]:
        return _build_reinforcement_state(normalized, required_keys=list(required), optional_keys=list(optional))

    def business_meal_reinforced() -> bool:
        return _has_reinforcement_value(normalized, "business_context_note") and _has_reinforcement_value(
            normalized, "attendee_names"
        )

    if has_gift_condolence:
        ceremonial_value = _follow_up_value(normalized, "ceremonial_business_related")
        ceremonial_text = _follow_up_text(normalized, "ceremonial_business_related")
        why = "경조사비나 선물은 일반 지출과 다른 제한 규정과 사실관계 확인이 필요할 수 있습니다."
        if ceremonial_value == "yes" and _has_meaningful_text(ceremonial_text):
            why = "업무 관련 경조사비라고 답했지만 제한 규정과 사실관계 확인이 더 필요해 세무 검토를 유지합니다."
            confidence_note = "사용자 답변이 반영됐지만, 경조사비/선물은 세무 검토가 필요한 항목으로 유지합니다."
        return _build_decision(
            "consult_tax_review",
            summary="경조사비·선물성 지출은 일반 경비처럼 바로 판단하지 않아요.",
            why=why,
            follow_up_questions=q("ceremonial_business_related"),
            applied_follow_up_answers=applied("ceremonial_business_related"),
            evidence_requirements=[
                "지출 목적 메모",
                "업무 관련성 설명 자료",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_35, SOURCE_REF_ARTICLE_160_2],
            confidence_note=confidence_note,
            **reinforce(
                required=("ceremonial_relation_note", "business_context_note"),
                optional=("client_or_counterparty_name", "supporting_file"),
            ),
        )

    if has_high_value_asset:
        asset_value = _follow_up_value(normalized, "asset_vs_consumable")
        asset_text = _follow_up_text(normalized, "asset_vs_consumable")
        why = "자산 취득이나 감가상각 검토가 필요할 수 있어 자동 비용처리 후보로 올리지 않습니다."
        if asset_value or _has_meaningful_text(asset_text):
            why = "사용 목적 답변이 있어도 고가 장비·전자기기는 자산 처리 가능성이 있어 세무 검토를 유지합니다."
            confidence_note = "사용자 답변이 반영됐지만, 고가 장비·전자기기는 자산 검토가 우선일 수 있습니다."
        return _build_decision(
            "consult_tax_review",
            summary="고가 장비·전자기기·가구는 바로 비용처리보다 세무 검토가 먼저일 수 있어요.",
            why=why,
            follow_up_questions=q("asset_vs_consumable"),
            applied_follow_up_answers=applied("asset_vs_consumable"),
            evidence_requirements=[
                "품목명과 사용 목적 메모",
                "업무용 사용 근거",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
            confidence_note=confidence_note,
            **reinforce(
                required=("asset_usage_note", "business_context_note"),
                optional=("supporting_file",),
            ),
        )

    if has_meal_like and has_personal_context:
        exception_text = _follow_up_text(normalized, "personal_meal_exception_reason")
        if _has_meaningful_text(exception_text) and _text_has_business_context(exception_text):
            return _build_decision(
                "needs_review",
                summary="업무 관련 메모가 들어왔지만 개인 식사 가능성이 남아 있어 추가 확인이 필요해요.",
                why="개인 식사로 보일 수 있는 지출이지만, 업무 관련 설명이 있어 자동 차단 대신 추가 확인 단계로 유지합니다.",
                follow_up_questions=q("personal_meal_exception_reason"),
                applied_follow_up_answers=applied("personal_meal_exception_reason"),
                evidence_requirements=[
                    "업무 관련 메모",
                    "참석자 또는 거래 목적 설명",
                    "영수증 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_33, SOURCE_REF_ARTICLE_160_2],
                confidence_note="사용자 메모를 반영했지만, 개인 식비와 업무 식비 구분은 추가 확인이 필요합니다.",
                **reinforce(
                    required=("business_context_note",),
                    optional=("attendee_names", "client_or_counterparty_name"),
                ),
            )
        return _build_decision(
            "do_not_auto_allow",
            summary="본인 식사나 생활형 소비로 읽히는 지출은 자동으로 비용처리하지 않아요.",
            why="가사 관련 경비나 개인 소비일 가능성이 높아 영수증만으로 자동 인정하지 않습니다.",
            follow_up_questions=q("personal_meal_exception_reason"),
            applied_follow_up_answers=applied("personal_meal_exception_reason"),
            evidence_requirements=[
                "업무 관련 메모가 있으면 함께 남겨 주세요.",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_33, SOURCE_REF_ARTICLE_160_2],
            **reinforce(required=("business_context_note",), optional=("attendee_names",)),
        )

    if has_meal_like and has_business_meal_context:
        meal_value = _follow_up_value(normalized, "business_meal_with_client")
        meal_text = _follow_up_text(normalized, "business_meal_with_client")
        if meal_value == "yes" and _has_meaningful_text(meal_text) and not has_risky_time and business_meal_reinforced():
            return _build_decision(
                "high_likelihood",
                summary="거래처 식사와 목적·참석자 보강이 함께 있어 비용처리 설명 가능성이 높아졌어요.",
                why="거래처 식사 여부, 사용 목적, 참석자 또는 상대방 정보가 함께 있어 업무 관련성을 설명하기 쉬운 편입니다.",
                follow_up_questions=q("business_meal_with_client"),
                applied_follow_up_answers=applied("business_meal_with_client"),
                evidence_requirements=[
                    "참석자 또는 거래처 메모",
                    "사용 목적 메모",
                    "적격증빙 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_35, SOURCE_REF_ARTICLE_160_2],
                confidence_note="사용자 답변이 반영됐지만, 접대비 후보는 적격증빙과 사실관계 확인이 계속 중요합니다.",
                **reinforce(
                    required=("business_context_note", "attendee_names"),
                    optional=("supporting_file", "client_or_counterparty_name"),
                ),
            )
        return _build_decision(
            "needs_review",
            summary="거래처 식사·접대비 후보라면 참석자와 목적 같은 보강 정보가 더 필요해요.",
            why="접대비 후보는 영수증과 질문 답변만으로 충분하지 않아 거래처, 참석자, 사용 목적을 함께 봐야 합니다.",
            follow_up_questions=q("business_meal_with_client"),
            applied_follow_up_answers=applied("business_meal_with_client"),
            evidence_requirements=[
                "참석자 또는 거래처 메모",
                "사용 목적 메모",
                "적격증빙 보관",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_35, SOURCE_REF_ARTICLE_160_2],
            **reinforce(
                required=("business_context_note", "attendee_names"),
                optional=("supporting_file", "client_or_counterparty_name"),
            ),
        )

    if has_transport:
        risky_text = _follow_up_text(normalized, "weekend_or_late_night_business_reason")
        risky_reinforcement = _clean_text(
            " ".join(
                part
                for part in (
                    risky_text,
                    normalized.weekend_or_late_night_note,
                    normalized.business_context_note,
                )
                if part
            )
        )
        if has_risky_time:
            if _has_meaningful_text(risky_reinforcement):
                return _build_decision(
                    "high_likelihood",
                    summary="업무 관련 이동 사유가 확인돼 교통비 후보로 설명 가능성이 높아졌어요.",
                    why="주말·심야 이동이더라도 업무 사유가 함께 있으면 교통비 설명이 쉬워질 수 있습니다.",
                    follow_up_questions=q("weekend_or_late_night_business_reason"),
                    applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
                    evidence_requirements=[
                        "출장 또는 방문 목적 메모",
                        "영수증 보관",
                    ],
                    official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
                    confidence_note="사용자 사유가 반영됐지만, 실제 업무 이동 여부는 증빙과 거래 사실에 따라 달라질 수 있습니다.",
                    **reinforce(
                        required=("weekend_or_late_night_note",),
                        optional=("business_context_note",),
                    ),
                )
            return _build_decision(
                "needs_review",
                summary="교통비 성격은 맞아 보여도 주말·심야 결제라 맥락을 한 번 더 확인하는 편이 안전해요.",
                why="시간대만으로 업무 관련성을 단정하기 어려워 추가 설명을 먼저 받는 것이 안전합니다.",
                follow_up_questions=q("weekend_or_late_night_business_reason"),
                applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
                evidence_requirements=[
                    "출장 또는 방문 목적 메모",
                    "영수증 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
                **reinforce(
                    required=("weekend_or_late_night_note",),
                    optional=("business_context_note",),
                ),
            )
        return _build_decision(
            "high_likelihood",
            summary="교통비·통행료처럼 업무 이동과 직접 연결되기 쉬운 지출로 보고 있어요.",
            why="출장·거래처 방문 같은 업무 이동과 연결되면 필요경비 설명이 비교적 쉬운 항목입니다.",
            evidence_requirements=[
                "영수증 보관",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
            **reinforce(optional=("business_context_note",)),
        )

    if has_books_edu_print:
        risky_text = _follow_up_text(normalized, "weekend_or_late_night_business_reason")
        risky_reinforcement = _clean_text(
            " ".join(
                part
                for part in (
                    risky_text,
                    normalized.weekend_or_late_night_note,
                    normalized.business_context_note,
                )
                if part
            )
        )
        if has_risky_time:
            if _has_meaningful_text(risky_reinforcement):
                return _build_decision(
                    "high_likelihood",
                    summary="업무 관련 자료·교육 사유가 확인돼 비용처리 설명 가능성이 높아졌어요.",
                    why="업무와 직접 관련된 자료나 교육이라는 설명이 있어 보수 단계에서 한 단계 올렸습니다.",
                    follow_up_questions=q("weekend_or_late_night_business_reason"),
                    applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
                    evidence_requirements=[
                        "업무 관련 메모",
                        "영수증 보관",
                    ],
                    official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
                    confidence_note="사용자 답변을 반영했지만, 실제 업무 관련성은 자료 성격과 사용 목적에 따라 달라질 수 있습니다.",
                    **reinforce(required=("business_context_note",), optional=("supporting_file",)),
                )
            return _build_decision(
                "needs_review",
                summary="교육·도서·인쇄 성격은 괜찮아 보여도 결제 맥락을 한 번 더 확인해요.",
                why="시간대나 실제 사용 목적이 불명확하면 보수적으로 추가 확인이 필요합니다.",
                follow_up_questions=q("weekend_or_late_night_business_reason"),
                applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
                evidence_requirements=[
                    "업무 관련 메모",
                    "영수증 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
                **reinforce(required=("business_context_note",), optional=("supporting_file",)),
            )
        return _build_decision(
            "high_likelihood",
            summary="도서·교육·인쇄처럼 업무 수행과 직접 연결되기 쉬운 지출로 보고 있어요.",
            why="업무용 자료, 교육, 출력비는 사업 관련성을 비교적 설명하기 쉬운 편입니다.",
            evidence_requirements=[
                "영수증 보관",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
            **reinforce(optional=("business_context_note",)),
        )

    if has_office_supplies:
        risky_text = _follow_up_text(normalized, "weekend_or_late_night_business_reason")
        risky_reinforcement = _clean_text(
            " ".join(
                part
                for part in (
                    risky_text,
                    normalized.weekend_or_late_night_note,
                    normalized.business_context_note,
                )
                if part
            )
        )
        if has_risky_time:
            if _has_meaningful_text(risky_reinforcement):
                return _build_decision(
                    "high_likelihood",
                    summary="업무용 소모품 사유가 확인돼 비용처리 설명 가능성이 높아졌어요.",
                    why="사무용 소모품이라는 설명이 있고 업무용 메모가 함께 있어 보수 단계에서 한 단계 올렸습니다.",
                    follow_up_questions=q("weekend_or_late_night_business_reason"),
                    applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
                    evidence_requirements=[
                        "품목 메모",
                        "영수증 보관",
                    ],
                    official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
                    confidence_note="사용자 답변을 반영했지만, 개인용 구입과 혼재 여부는 추가 확인이 필요할 수 있습니다.",
                    **reinforce(required=("business_context_note",), optional=("supporting_file",)),
                )
            return _build_decision(
                "needs_review",
                summary="사무용 소모품 후보지만 결제 시간대나 사용 맥락을 같이 확인할게요.",
                why="사무용품이라도 개인용 구입과 섞일 수 있어 보수적으로 추가 확인합니다.",
                follow_up_questions=q("weekend_or_late_night_business_reason"),
                applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
                evidence_requirements=[
                    "품목 메모",
                    "영수증 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
                **reinforce(required=("business_context_note",), optional=("supporting_file",)),
            )
        return _build_decision(
            "high_likelihood",
            summary="문구·사무용 소모품처럼 업무 관련성이 비교적 드러나기 쉬운 지출로 보고 있어요.",
            why="소모품 성격이 명확하면 필요경비 후보로 설명하기 쉬운 편입니다.",
            evidence_requirements=[
                "영수증 보관",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
            **reinforce(optional=("business_context_note",)),
        )

    if has_meal_like:
        meal_value = _follow_up_value(normalized, "business_meal_with_client")
        meal_text = _follow_up_text(normalized, "business_meal_with_client")
        if has_risky_time:
            return _build_decision(
                "needs_review",
                summary="카페·식비이면서 주말·심야 결제라 추가 확인이 더 필요해요.",
                why="식비·음료는 개인 소비와 섞이기 쉬운데 시간대까지 겹치면 더 보수적으로 봐야 합니다.",
                follow_up_questions=q("business_meal_with_client"),
                applied_follow_up_answers=applied("business_meal_with_client"),
                evidence_requirements=[
                    "거래 목적 메모",
                    "영수증 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_33],
                **reinforce(required=("business_context_note",), optional=("attendee_names", "client_or_counterparty_name")),
            )
        if meal_value == "yes" and _has_meaningful_text(meal_text) and _text_has_business_context(meal_text):
            return _build_decision(
                "needs_review",
                summary="업무 관련 식사라고 답했지만 식비 성격이라 한 번 더 확인이 필요해요.",
                why="업무 관련 설명이 있어도 카페·식비는 개인 소비와 혼재되기 쉬워 추가 확인 단계를 유지합니다.",
                follow_up_questions=q("business_meal_with_client"),
                applied_follow_up_answers=applied("business_meal_with_client"),
                evidence_requirements=[
                    "거래 목적 메모",
                    "참석자 또는 상대방 메모",
                    "영수증 보관",
                ],
                official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_33, SOURCE_REF_ARTICLE_160_2],
                confidence_note="사용자 답변이 반영됐지만, 카페·식비는 개인 사용과 혼재되기 쉬워 보수적으로 유지합니다.",
                **reinforce(required=("business_context_note",), optional=("attendee_names", "client_or_counterparty_name")),
            )
        return _build_decision(
            "needs_review",
            summary="카페·식비·음료는 업무와 개인 사용이 섞이기 쉬워 추가 확인이 필요해요.",
            why="영수증만으로는 회의비인지 개인 소비인지 단정하기 어렵습니다.",
            follow_up_questions=q("business_meal_with_client"),
            applied_follow_up_answers=applied("business_meal_with_client"),
            evidence_requirements=[
                "거래 목적 메모",
                "영수증 보관",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_33],
            **reinforce(required=("business_context_note",), optional=("attendee_names", "client_or_counterparty_name")),
        )

    if has_mixed_spending:
        mixed_text = _follow_up_text(normalized, "mixed_spend_business_context")
        return _build_decision(
            "needs_review",
            summary="개인·업무 사용이 섞일 수 있는 지출이라 자동 판단하지 않아요.",
            why="편의점·온라인쇼핑·생활형 소비는 영수증만으로 업무 관련성을 설명하기 어렵습니다.",
            follow_up_questions=q("mixed_spend_business_context"),
            applied_follow_up_answers=applied("mixed_spend_business_context"),
            evidence_requirements=[
                "구매 항목 메모",
                "업무 관련 메모",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_33],
            confidence_note=(
                "업무 관련 메모가 반영됐지만, 개인 사용과의 혼재 가능성은 계속 확인이 필요합니다."
                if _has_meaningful_text(mixed_text)
                else DEFAULT_CONFIDENCE_NOTE
            ),
            **reinforce(required=("business_context_note",), optional=("supporting_file",)),
        )

    if has_personal_context:
        return _build_decision(
            "do_not_auto_allow",
            summary="개인·가사 관련 지출 가능성이 커서 자동으로 인정하지 않아요.",
            why="가사 관련 경비는 필요경비에 산입되지 않을 수 있어 보수적으로 차단합니다.",
            follow_up_questions=q("personal_meal_exception_reason"),
            applied_follow_up_answers=applied("personal_meal_exception_reason"),
            evidence_requirements=[
                "업무 관련 메모가 있으면 함께 남겨 주세요.",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_33],
            **reinforce(required=("business_context_note",)),
        )

    if has_risky_time:
        risky_text = _follow_up_text(normalized, "weekend_or_late_night_business_reason")
        risky_reinforcement = _clean_text(
            " ".join(
                part
                for part in (
                    risky_text,
                    normalized.weekend_or_late_night_note,
                    normalized.business_context_note,
                )
                if part
            )
        )
        return _build_decision(
            "needs_review",
            summary="주말·심야 결제는 업무 관련성을 한 번 더 확인하는 편이 안전해요.",
            why="시간대만으로는 업무 지출인지 개인 지출인지 판단하기 어렵습니다."
            if not _has_meaningful_text(risky_reinforcement)
            else "업무 관련 메모가 반영됐지만 시간대 리스크가 있어 추가 확인 단계를 유지합니다.",
            follow_up_questions=q("weekend_or_late_night_business_reason"),
            applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
            evidence_requirements=[
                "업무 목적 메모",
                "영수증 보관",
            ],
            official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
            confidence_note=(
                "사용자 메모가 반영됐지만, 시간대만으로는 업무 관련성을 확정할 수 없습니다."
                if _has_meaningful_text(risky_reinforcement)
                else DEFAULT_CONFIDENCE_NOTE
            ),
            **reinforce(required=("weekend_or_late_night_note",), optional=("business_context_note",)),
        )

    return _build_decision(
        "needs_review",
        summary="자동으로 단정하지 않고 추가 확인 단계로 먼저 안내하고 있어요.",
        why="거래 목적, 사용 맥락, 증빙 품질이 더 필요해 보수적으로 추가 확인이 필요합니다.",
        follow_up_questions=q("weekend_or_late_night_business_reason"),
        applied_follow_up_answers=applied("weekend_or_late_night_business_reason"),
        evidence_requirements=[
            "영수증 보관",
            "업무 목적 메모",
        ],
        official_source_refs=[SOURCE_REF_ARTICLE_27, SOURCE_REF_ARTICLE_160_2],
        **reinforce(required=("business_context_note",)),
    )


def evaluate_receipt_expense_with_follow_up(
    *,
    tx: Any | None = None,
    draft: dict[str, Any] | None = None,
    focus_kind: str = "",
    receipt_type: str = "",
    follow_up_answers: Any | None = None,
    reinforcement_data: Any | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_receipt_expense_input(
        payload or {},
        tx=tx,
        draft=draft,
        focus_kind=focus_kind,
        receipt_type=receipt_type,
        follow_up_answers=follow_up_answers,
        reinforcement_data=reinforcement_data,
    )
    return evaluate_receipt_expense(normalized)


def load_receipt_follow_up_answers_map(
    db_session: Any,
    AnswerModel: Any,
    *,
    user_pk: int,
    transaction_ids: list[int] | tuple[int, ...],
) -> dict[int, dict[str, dict[str, Any]]]:
    tx_ids = sorted({int(tx_id) for tx_id in (transaction_ids or []) if int(tx_id or 0) > 0})
    if not tx_ids:
        return {}
    try:
        rows = (
            db_session.query(AnswerModel)
            .filter(AnswerModel.user_pk == int(user_pk))
            .filter(AnswerModel.transaction_id.in_(tx_ids))
            .all()
        )
    except ProgrammingError as exc:
        if _is_missing_follow_up_table_error(exc):
            try:
                db_session.rollback()
            except Exception:
                pass
            return {}
        raise
    answer_map: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        tx_id = int(getattr(row, "transaction_id", 0) or 0)
        question_key = _clean_text(getattr(row, "question_key", ""))
        if tx_id <= 0 or question_key not in FOLLOW_UP_QUESTION_SPECS:
            continue
        tx_bucket = answer_map.setdefault(tx_id, {})
        answered_at = getattr(row, "answered_at", None)
        tx_bucket[question_key] = {
            "answer_value": _normalize_answer_value(getattr(row, "answer_value", "")),
            "answer_text": _clean_text(getattr(row, "answer_text", "")),
            "answered_at": answered_at.isoformat(sep=" ", timespec="minutes") if answered_at else "",
            "answered_by": getattr(row, "answered_by", None),
        }
    return answer_map


def load_receipt_reinforcement_map(
    db_session: Any,
    ReinforcementModel: Any,
    *,
    user_pk: int,
    transaction_ids: list[int] | tuple[int, ...],
) -> dict[int, dict[str, Any]]:
    tx_ids = sorted({int(tx_id) for tx_id in (transaction_ids or []) if int(tx_id or 0) > 0})
    if not tx_ids:
        return {}
    try:
        rows = (
            db_session.query(ReinforcementModel)
            .filter(ReinforcementModel.user_pk == int(user_pk))
            .filter(ReinforcementModel.transaction_id.in_(tx_ids))
            .all()
        )
    except ProgrammingError as exc:
        if _is_missing_reinforcement_table_error(exc):
            try:
                db_session.rollback()
            except Exception:
                pass
            return {}
        raise
    reinforcement_map: dict[int, dict[str, Any]] = {}
    for row in rows:
        tx_id = int(getattr(row, "transaction_id", 0) or 0)
        if tx_id <= 0:
            continue
        reinforcement_map[tx_id] = normalize_reinforcement_payload(row)
    return reinforcement_map


def save_receipt_follow_up_answers_and_re_evaluate(
    db_session: Any,
    AnswerModel: Any,
    *,
    user_pk: int,
    answered_by: int | None,
    tx: Any,
    evidence_item: Any | None = None,
    answers_payload: Any,
    draft: dict[str, Any] | None = None,
    focus_kind: str = "",
    receipt_type: str = "",
) -> dict[str, Any]:
    tx_id = int(getattr(tx, "id", 0) or 0)
    if tx_id <= 0:
        raise ValueError("invalid_transaction")

    validated_answers = validate_follow_up_answers_payload(answers_payload)
    if not validated_answers:
        raise ValueError("missing_follow_up_answers")

    try:
        existing_rows = (
            db_session.query(AnswerModel)
            .filter(AnswerModel.user_pk == int(user_pk))
            .filter(AnswerModel.transaction_id == tx_id)
            .all()
        )
    except ProgrammingError as exc:
        if _is_missing_follow_up_table_error(exc):
            try:
                db_session.rollback()
            except Exception:
                pass
            raise ValueError("follow_up_storage_not_ready")
        raise
    existing_map = {str(getattr(row, "question_key", "") or ""): row for row in existing_rows}

    now = utcnow()
    for question_key, answer in validated_answers.items():
        row = existing_map.get(question_key)
        if row is None:
            row = AnswerModel(
                user_pk=int(user_pk),
                transaction_id=tx_id,
                evidence_item_id=int(getattr(evidence_item, "id", 0) or 0) or None,
                question_key=question_key,
                created_at=now,
            )
            db_session.add(row)
        row.evidence_item_id = int(getattr(evidence_item, "id", 0) or 0) or None
        row.answer_value = answer.get("answer_value") or None
        row.answer_text = answer.get("answer_text") or None
        row.answered_at = now
        row.answered_by = int(answered_by or 0) or None
        row.updated_at = now

    try:
        db_session.flush()
    except ProgrammingError as exc:
        if _is_missing_follow_up_table_error(exc):
            try:
                db_session.rollback()
            except Exception:
                pass
            raise ValueError("follow_up_storage_not_ready")
        raise
    stored_answers = load_receipt_follow_up_answers_map(
        db_session,
        AnswerModel,
        user_pk=int(user_pk),
        transaction_ids=[tx_id],
    ).get(tx_id, {})
    decision = evaluate_receipt_expense_with_follow_up(
        tx=tx,
        draft=draft,
        focus_kind=focus_kind,
        receipt_type=receipt_type,
        follow_up_answers=stored_answers,
    )
    return {
        "transaction_id": tx_id,
        "follow_up_answers": stored_answers,
        "decision": decision,
    }


def save_receipt_reinforcement_and_re_evaluate(
    db_session: Any,
    ReinforcementModel: Any,
    AnswerModel: Any,
    *,
    user_pk: int,
    updated_by: int | None,
    tx: Any,
    evidence_item: Any | None = None,
    reinforcement_payload: Any,
    draft: dict[str, Any] | None = None,
    focus_kind: str = "",
    receipt_type: str = "",
    month_key: str = "",
    supporting_file: Any | None = None,
    store_supporting_file_fn: Any | None = None,
    delete_supporting_file_fn: Any | None = None,
) -> dict[str, Any]:
    tx_id = int(getattr(tx, "id", 0) or 0)
    if tx_id <= 0:
        raise ValueError("invalid_transaction")

    validated_payload = validate_reinforcement_payload(reinforcement_payload)
    has_text_payload = bool(validated_payload)

    file_provided = bool(getattr(supporting_file, "filename", "") or "")
    if not has_text_payload and not file_provided:
        raise ValueError("missing_reinforcement_payload")

    try:
        row = (
            db_session.query(ReinforcementModel)
            .filter(ReinforcementModel.user_pk == int(user_pk))
            .filter(ReinforcementModel.transaction_id == tx_id)
            .first()
        )
    except ProgrammingError as exc:
        if _is_missing_reinforcement_table_error(exc):
            try:
                db_session.rollback()
            except Exception:
                pass
            raise ValueError("reinforcement_storage_not_ready")
        raise

    now = utcnow()
    if row is None:
        row = ReinforcementModel(
            user_pk=int(user_pk),
            transaction_id=tx_id,
            evidence_item_id=int(getattr(evidence_item, "id", 0) or 0) or None,
            created_at=now,
        )
        db_session.add(row)

    row.evidence_item_id = int(getattr(evidence_item, "id", 0) or 0) or None
    for field_name in (
        "business_context_note",
        "attendee_names",
        "client_or_counterparty_name",
        "ceremonial_relation_note",
        "asset_usage_note",
        "weekend_or_late_night_note",
    ):
        if field_name in validated_payload:
            setattr(row, field_name, validated_payload.get(field_name) or None)

    if file_provided:
        if store_supporting_file_fn is None:
            raise ValueError("supporting_file_storage_unavailable")
        old_file_key = str(getattr(row, "supporting_file_key", "") or "")
        try:
            stored = store_supporting_file_fn(
                user_pk=int(user_pk),
                tx_id=tx_id,
                month_key=month_key,
                files=[supporting_file],
            )
        except Exception as exc:
            raise ValueError(f"invalid_supporting_file:{exc}") from exc
        row.supporting_file_key = str(getattr(stored, "file_key", "") or "") or None
        row.supporting_file_name = str(getattr(stored, "original_filename", "") or "") or None
        row.supporting_file_mime_type = str(getattr(stored, "mime_type", "") or "") or None
        row.supporting_file_size_bytes = int(getattr(stored, "size_bytes", 0) or 0) or None
        row.supporting_file_uploaded_at = now
        if (
            delete_supporting_file_fn is not None
            and old_file_key
            and old_file_key != row.supporting_file_key
        ):
            try:
                delete_supporting_file_fn(old_file_key)
            except Exception:
                pass

    row.updated_at = now
    row.updated_by = int(updated_by or 0) or None

    try:
        db_session.flush()
    except ProgrammingError as exc:
        if _is_missing_reinforcement_table_error(exc):
            try:
                db_session.rollback()
            except Exception:
                pass
            raise ValueError("reinforcement_storage_not_ready")
        raise

    follow_up_answers = load_receipt_follow_up_answers_map(
        db_session,
        AnswerModel,
        user_pk=int(user_pk),
        transaction_ids=[tx_id],
    ).get(tx_id, {})
    stored_reinforcement = load_receipt_reinforcement_map(
        db_session,
        ReinforcementModel,
        user_pk=int(user_pk),
        transaction_ids=[tx_id],
    ).get(tx_id, {})
    decision = evaluate_receipt_expense_with_follow_up(
        tx=tx,
        draft=draft,
        focus_kind=focus_kind,
        receipt_type=receipt_type,
        follow_up_answers=follow_up_answers,
        reinforcement_data=stored_reinforcement,
    )
    return {
        "transaction_id": tx_id,
        "follow_up_answers": follow_up_answers,
        "reinforcement": stored_reinforcement,
        "decision": decision,
    }

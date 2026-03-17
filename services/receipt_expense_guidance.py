from __future__ import annotations

from typing import Any

from services.receipt_expense_rules import (
    DEFAULT_CONFIDENCE_NOTE,
    RECEIPT_EXPENSE_LEVELS,
    evaluate_receipt_expense_with_follow_up,
)


GUIDE_DISCLAIMER_LINES = (
    DEFAULT_CONFIDENCE_NOTE,
    "최종 필요경비 인정 여부는 실제 거래 사실, 증빙, 법령, 과세관청 판단에 따라 달라질 수 있습니다.",
    "개인지출 또는 가사 관련 경비는 필요경비에 산입되지 않을 수 있습니다.",
)


GUIDE_SOURCES = (
    {
        "title": "국가법령정보센터 · 소득세법 제27조, 제33조, 제35조",
        "url": "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95",
        "note": "사업 관련 필요경비, 가사 관련 경비 불산입, 접대비 관련 제한 규정을 함께 확인합니다.",
    },
    {
        "title": "국가법령정보센터 · 소득세법 제160조의2",
        "url": "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95",
        "note": "사업자가 증빙을 수취·보관해야 하는 기본 원칙을 확인합니다.",
    },
    {
        "title": "국가법령정보센터 · 소득세법 시행령 제208조의2",
        "url": "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95%EC%8B%9C%ED%96%89%EB%A0%B9",
        "note": "증빙 수취 예외·범위와 해석 시 주의할 항목을 함께 봅니다.",
    },
)


def get_receipt_expense_guidance_content() -> dict[str, Any]:
    return {
        "title": "비용처리 안내",
        "summary": "영수증을 올리기 전에 어떤 지출이 비교적 설명되기 쉽고, 어떤 지출은 추가 확인이 필요한지 먼저 안내합니다.",
        "quick_buckets": (
            {
                "anchor": RECEIPT_EXPENSE_LEVELS["high_likelihood"]["guide_anchor"],
                "title": "비용처리 가능성이 높은 항목",
                "label": RECEIPT_EXPENSE_LEVELS["high_likelihood"]["label"],
                "intro": "업무 관련성이 비교적 설명되기 쉬운 항목입니다. 그래도 실제 사용 목적과 증빙은 함께 확인해야 합니다.",
                "items": (
                    {"name": "교통비, 주차비, 통행료", "why": "출장·거래처 방문처럼 업무 이동과 직접 연결되는 경우가 많습니다."},
                    {"name": "도서, 교육, 인쇄, 복사", "why": "업무 수행에 직접 쓰인 자료·교육·출력이라면 설명 가능성이 높습니다."},
                    {"name": "사무용 소모품, 배송비", "why": "소모품과 발송비는 업무 필요성이 비교적 드러나기 쉽습니다."},
                ),
            },
            {
                "anchor": RECEIPT_EXPENSE_LEVELS["needs_review"]["guide_anchor"],
                "title": "추가 확인이 필요한 항목",
                "label": RECEIPT_EXPENSE_LEVELS["needs_review"]["label"],
                "intro": "개인 사용과 섞이기 쉬워서 거래 목적, 참석자, 업무 맥락을 더 확인해야 합니다.",
                "items": (
                    {"name": "거래처 식사·접대비 후보", "why": "접대 목적과 참석자, 적격증빙 여부를 같이 확인해야 합니다."},
                    {"name": "카페, 식비, 음료", "why": "업무 미팅인지 개인 소비인지 영수증만으로는 구분이 어렵습니다."},
                    {"name": "주말·심야 결제, 개인·업무 혼합 가능 지출", "why": "시간대나 사용 장소만으로는 업무 관련성을 단정하기 어렵습니다."},
                ),
            },
            {
                "anchor": RECEIPT_EXPENSE_LEVELS["do_not_auto_allow"]["guide_anchor"],
                "title": "자동 인정하지 않는 항목",
                "label": RECEIPT_EXPENSE_LEVELS["do_not_auto_allow"]["label"],
                "intro": "개인지출·가사 관련 가능성이 크거나 사실관계 확인 없이 자동으로 인정하면 위험한 항목입니다.",
                "items": (
                    {"name": "본인 식비·음료, 생활비 성격 지출", "why": "가사 관련 경비는 필요경비에 산입되지 않을 수 있어 자동 인정하지 않습니다."},
                    {"name": "개인 쇼핑, 가정용 소비", "why": "업무 관련성이 별도로 입증되지 않으면 자동 비용처리 대상으로 보지 않습니다."},
                ),
            },
            {
                "anchor": RECEIPT_EXPENSE_LEVELS["consult_tax_review"]["guide_anchor"],
                "title": "세무 검토 권장 항목",
                "label": RECEIPT_EXPENSE_LEVELS["consult_tax_review"]["label"],
                "intro": "증빙만으로 바로 비용처리하기보다 자산 처리, 감가상각, 특수 규정 검토가 필요할 수 있습니다.",
                "items": (
                    {"name": "고가 전자기기, 가구, 장비", "why": "단순 소모품이 아니라 자산 취득으로 볼 여지가 있어 세무 검토가 필요할 수 있습니다."},
                    {"name": "경조사비, 선물, 특수 목적 지출", "why": "일반 경비와 다른 제한 규정이 적용될 수 있어 자동 판단을 피해야 합니다."},
                ),
            },
        ),
        "confusing_cases": (
            {"title": "카페 영수증이 항상 업무비는 아니에요", "body": "회의 목적, 참석자, 메모가 같이 있어야 설명이 쉬워집니다."},
            {"title": "주말·심야 결제는 맥락을 더 봐야 해요", "body": "출장, 야간 업무, 행사 준비처럼 업무 사유가 있으면 설명 자료를 남겨 두는 편이 안전합니다."},
            {"title": "비싼 장비는 즉시 비용보다 자산 검토가 먼저일 수 있어요", "body": "노트북·카메라·가구처럼 고가 품목은 세무 검토를 거쳐 처리하는 편이 안전합니다."},
        ),
        "input_tips": (
            "거래 목적이나 참석자를 메모에 남기면 설명이 쉬워집니다.",
            "전자영수증이라면 상호, 결제시각, 금액이 보이게 붙여 주세요.",
            "거래처 식사·접대비 후보는 누구와 어떤 목적으로 사용했는지 함께 남겨 주세요.",
        ),
        "disclaimer_lines": GUIDE_DISCLAIMER_LINES,
        "sources": GUIDE_SOURCES,
    }


def build_receipt_expense_inline_guidance(
    *,
    tx: Any | None = None,
    draft: dict[str, Any] | None = None,
    focus_kind: str = "",
    receipt_type: str = "",
    follow_up_answers: Any | None = None,
    reinforcement_data: Any | None = None,
) -> dict[str, Any] | None:
    direction = str(getattr(tx, "direction", "out") or "out").strip().lower()
    if direction and direction != "out":
        return None

    decision = evaluate_receipt_expense_with_follow_up(
        tx=tx,
        draft=draft,
        focus_kind=focus_kind,
        receipt_type=receipt_type,
        follow_up_answers=follow_up_answers,
        reinforcement_data=reinforcement_data,
    )
    meta = RECEIPT_EXPENSE_LEVELS[decision["level"]]
    return {
        **decision,
        "tone": meta["tone"],
        "anchor": decision["guide_anchor"],
    }

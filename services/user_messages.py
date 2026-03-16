from __future__ import annotations

from typing import Any


_PATTERN_MESSAGE_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "requestentitytoolarge",
            "413",
            "too large",
            "payload too large",
        ),
        "파일이 너무 커요. 더 작은 파일로 다시 시도해 주세요.",
    ),
    (
        (
            "openai",
            "api key",
            "model",
            "not configured",
            "no module named openai",
        ),
        "지금은 자동 인식이 준비되지 않았어요. 잠시 후 다시 시도해 주세요.",
    ),
    (
        (
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "connectionerror",
            "network",
        ),
        "연결이 불안정해요. 잠시 후 다시 시도해 주세요.",
    ),
    (
        (
            "json",
            "decode",
            "unexpected token",
            "mismatch",
            "contract",
        ),
        "요청 처리 중 형식 문제가 있었어요. 새로고침 후 다시 시도해 주세요.",
    ),
    (
        (
            "keyerror",
            "attributeerror",
            "typeerror",
            "valueerror",
            "indexerror",
        ),
        "입력값 확인 중 문제가 있었어요. 값을 다시 확인하고 저장해 주세요.",
    ),
)


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip().lower()


def to_user_message(
    *,
    raw_message: str | None = None,
    exc: Exception | None = None,
    fallback: str = "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
) -> str:
    parts = [_normalize_text(raw_message)]
    if exc is not None:
        parts.append(_normalize_text(type(exc).__name__))
        parts.append(_normalize_text(exc))
    haystack = " ".join([p for p in parts if p]).strip()

    if haystack:
        for patterns, user_message in _PATTERN_MESSAGE_MAP:
            if any(p in haystack for p in patterns):
                return str(user_message)

    msg = str(raw_message or "").strip()
    if not msg:
        return str(fallback)
    if len(msg) > 120:
        return str(fallback)
    if any(token in msg.lower() for token in ("traceback", "exception", "error:", "keyerror", "requestentitytoolarge")):
        return str(fallback)
    return msg


def with_retry_hint(message: str) -> str:
    base = str(message or "").strip()
    if not base:
        return "잠시 후 다시 시도해 주세요."
    if "다시 시도" in base:
        return base
    return f"{base} 다시 시도해 주세요."

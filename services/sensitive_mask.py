from __future__ import annotations

import re

# 숫자/공백/하이픈 혼합 시퀀스 후보
_DIGIT_SEQ_RE = re.compile(r"\d[\d\-\s]{6,23}\d")
# 계좌 관련 키워드 근처면 우선 마스킹
_ACCOUNT_HINT_RE = re.compile(r"(계좌|출금계좌|입금계좌|계좌번호|account|acct|bank)", re.IGNORECASE)


def _normalize_digits(raw: str) -> str:
    return re.sub(r"[^0-9]", "", str(raw or ""))


def _looks_like_phone(digits: str) -> bool:
    # 전화번호(특히 휴대폰) 오탐 최소화
    if len(digits) in {10, 11} and digits.startswith("0"):
        return True
    return False


def mask_sensitive_numbers(text: str | None) -> str:
    """
    계좌번호로 의심되는 긴 숫자를 ****1234 형태로 마스킹한다.
    - 12자리 이상 숫자는 키워드 없이도 마스킹
    - 8~11자리는 계좌 키워드가 가까울 때만 마스킹
    - 전화번호로 보이는 값은 제외
    """
    src = str(text or "")
    if not src:
        return ""

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = _normalize_digits(raw)
        if len(digits) < 8 or len(digits) > 20:
            return raw
        if _looks_like_phone(digits):
            return raw

        start = max(0, match.start() - 24)
        end = min(len(src), match.end() + 24)
        nearby = src[start:end]
        hinted = bool(_ACCOUNT_HINT_RE.search(nearby))

        if len(digits) >= 12 or hinted:
            return f"****{digits[-4:]}"
        return raw

    return _DIGIT_SEQ_RE.sub(_replace, src)

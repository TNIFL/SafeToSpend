from __future__ import annotations

import re

from domain.models import UserBankAccount
from services.bank_accounts import fingerprint as account_fingerprint
from services.bank_accounts import last4 as account_last4
from services.bank_accounts import normalize_account_number

_KEYWORD_RE = re.compile(
    r"(계좌번호|계좌|출금계좌|입금계좌|입출금계좌|계좌정보|account|acct)",
    re.IGNORECASE,
)
_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[\d\-\s]{6,24}\d)(?!\d)")


def _decode_best_effort(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def detect_account_from_lines(lines: list[str]) -> dict:
    best: dict | None = None
    for idx, raw_line in enumerate(lines[:30]):
        line = str(raw_line or "").strip()
        if not line:
            continue
        if not _KEYWORD_RE.search(line):
            continue
        for m in _CANDIDATE_RE.finditer(line):
            candidate = normalize_account_number(m.group(0))
            if len(candidate) < 8 or len(candidate) > 20:
                continue
            fp = account_fingerprint(candidate)
            l4 = account_last4(candidate)
            if not fp or not l4:
                continue
            confidence = 90
            if "계좌번호" in line:
                confidence = 95
            found = {
                "found": True,
                "fingerprint": fp,
                "last4": l4,
                "confidence": confidence,
                "raw_kind": "keyword_line",
                "line_no": idx + 1,
            }
            if (best is None) or (int(found["confidence"]) > int(best.get("confidence") or 0)):
                best = found

    if best:
        return best
    return {
        "found": False,
        "fingerprint": None,
        "last4": None,
        "confidence": 0,
        "raw_kind": "not_found",
        "line_no": None,
    }


def detect_account_from_file_head(filepath: str, *, max_lines: int = 30) -> dict:
    try:
        with open(filepath, "rb") as f:
            raw = f.read(128_000)
    except Exception:
        return {
            "found": False,
            "fingerprint": None,
            "last4": None,
            "confidence": 0,
            "raw_kind": "read_error",
            "line_no": None,
        }

    text = _decode_best_effort(raw)
    lines = text.splitlines()[: max(1, int(max_lines))]
    return detect_account_from_lines(lines)


def find_account_by_fingerprint(user_pk: int, fp: str | None) -> UserBankAccount | None:
    key = str(fp or "").strip()
    if not key:
        return None
    return (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.account_fingerprint == key)
        .first()
    )

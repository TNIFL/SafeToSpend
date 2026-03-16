from __future__ import annotations

import logging
import re
from typing import Any


_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)\b(authkey|paymentkey|billingkey|cardnumber|cardno|cvc|cardpassword|card_pw|cardpw)=([^&\s]+)"
)
_SENSITIVE_BEARER_RE = re.compile(r"(?i)\b(authorization:\s*bearer\s+)([a-z0-9\-\._~\+\/]+=*)")


def sanitize_log_text(text: str | None) -> str:
    raw = str(text or "")
    redacted = _SENSITIVE_QUERY_RE.sub(lambda m: f"{m.group(1)}=***", raw)
    redacted = _SENSITIVE_BEARER_RE.sub(lambda m: f"{m.group(1)}***", redacted)
    return redacted


def _sanitize_arg(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_log_text(value)
    return value


class SensitiveLogFilter(logging.Filter):
    """결제 콜백 querystring 같은 민감값이 로그에 남지 않도록 최소 마스킹."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanitize_log_text(str(record.msg))
            if isinstance(record.args, tuple):
                record.args = tuple(_sanitize_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _sanitize_arg(v) for k, v in record.args.items()}
            elif record.args:
                record.args = _sanitize_arg(record.args)
        except Exception:
            # 로깅 필터 실패로 본 처리 흐름이 막히면 안 된다.
            return True
        return True

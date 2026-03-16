from __future__ import annotations

import json

from flask import current_app, has_app_context, request


def audit_event(
    event: str,
    *,
    user_pk: int | None = None,
    outcome: str = "ok",
    detail: str = "",
    extra: dict | None = None,
) -> None:
    payload = {
        "event": str(event or "").strip() or "unknown",
        "outcome": str(outcome or "ok").strip() or "ok",
        "user_pk": int(user_pk) if user_pk else None,
        "path": (request.path if has_app_context() else ""),
        "method": (request.method if has_app_context() else ""),
        "detail": (str(detail or "").strip()[:300] if detail else ""),
    }
    if isinstance(extra, dict) and extra:
        payload["extra"] = extra
    line = f"[SECURITY_AUDIT] {json.dumps(payload, ensure_ascii=False, default=str)}"
    try:
        if has_app_context():
            current_app.logger.info(line)
        else:
            print(line)
    except Exception:
        return


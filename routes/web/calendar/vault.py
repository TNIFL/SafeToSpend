from __future__ import annotations

from flask import redirect, request, url_for


def legacy_vault_redirect(month_key: str):
    """Legacy URL adapter: /dashboard/vault-legacy -> /dashboard/vault."""
    return redirect(url_for("web_vault.index", month=month_key))


def register_vault_routes(*, bp):
    @bp.get("/vault-legacy")
    def vault():
        """레거시 URL 호환: 실제 구현은 web_vault.index로 위임."""
        month_key = (request.args.get("month") or "").strip()
        return legacy_vault_redirect(month_key)

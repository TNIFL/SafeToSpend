# routes/web/auth.py
from pathlib import Path

from flask import Blueprint, abort, render_template, request, redirect, url_for, flash, session

from services.auth import register_user, authenticate
from services.dashboard_state import save_state
from services.legal_documents import required_signup_consents

web_auth_bp = Blueprint("web_auth", __name__)
_LEGAL_DOC_ROOT = Path(__file__).resolve().parents[2] / "docs" / "legal"
_LEGAL_DOCUMENTS = {
    "terms": {
        "title": "이용약관",
        "subtitle": "현재 구현 기준 초안",
        "filename": "terms_of_service_draft.md",
    },
    "privacy": {
        "title": "개인정보처리방침",
        "subtitle": "현재 구현 기준 초안",
        "filename": "privacy_policy_draft.md",
    },
}


def _maybe_save_guest_to_db(user_id: int) -> None:
    # 게스트가 /main 에 입력했던 값이 있으면 로그인 직후 계정 상태로 저장
    if session.get("g_dirty"):
        rev = int(session.get("g_rev") or 0)
        exp = int(session.get("g_exp") or 0)
        rate = float(session.get("g_rate") or 0.15)
        save_state(user_id, rev, exp, rate)
        session.pop("g_dirty", None)


def _required_consents_given() -> bool:
    return (
        request.form.get("agree_terms") == "on"
        and request.form.get("agree_privacy") == "on"
    )


def _load_legal_document(kind: str) -> dict:
    spec = _LEGAL_DOCUMENTS.get(kind)
    if spec is None:
        abort(404)

    path = _LEGAL_DOC_ROOT / spec["filename"]
    if not path.exists():
        abort(404)

    return {
        "title": spec["title"],
        "subtitle": spec["subtitle"],
        "body_text": path.read_text(encoding="utf-8"),
    }


@web_auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email") or ""
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        if not _required_consents_given():
            flash("이용약관과 개인정보처리방침에 동의해야 가입할 수 있습니다.", "error")
            return redirect(url_for("web_auth.register"))

        if password != password2:
            flash("비밀번호가 서로 다릅니다.", "error")
            return redirect(url_for("web_auth.register"))

        ok, msg = register_user(email, password, consents=required_signup_consents())
        if not ok:
            flash(msg, "error")
            return redirect(url_for("web_auth.register"))

        flash("가입이 완료되었습니다. 로그인해 주세요.", "success")
        return redirect(url_for("web_auth.login"))

    return render_template("register.html")


@web_auth_bp.get("/legal/terms")
def terms_document():
    return render_template("legal_document.html", **_load_legal_document("terms"))


@web_auth_bp.get("/legal/privacy")
def privacy_document():
    return render_template("legal_document.html", **_load_legal_document("privacy"))


@web_auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # NOTE: 프로젝트에 web_dashboard 블루프린트가 없음 → overview가 기본 랜딩
    next_url = request.args.get("next") or url_for("web_overview.overview")

    if request.method == "POST":
        identifier = request.form.get("identifier") or ""
        password = request.form.get("password") or ""

        ok, msg, user_id = authenticate(identifier, password)
        if not ok:
            flash(msg, "error")
            return redirect(url_for("web_auth.login", next=next_url))

        session["user_id"] = user_id
        _maybe_save_guest_to_db(user_id)

        flash("로그인되었습니다.", "success")
        return redirect(next_url)

    return render_template("login.html")


@web_auth_bp.route("/logout", methods=["GET"])
def logout():
    session.clear()
    flash("로그아웃되었습니다.", "success")
    return redirect(url_for("web_main.landing"))

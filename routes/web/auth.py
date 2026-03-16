# routes/web/auth.py
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from services.auth import register_user, authenticate
from services.dashboard_state import save_state
from services.input_sanitize import safe_str, validate_email
from services.onboarding import onboarding_is_done, save_onboarding
from services.rate_limit import client_ip, hit_limit
from services.security_audit import audit_event

web_auth_bp = Blueprint("web_auth", __name__)


def _maybe_save_guest_to_db(user_id: int) -> None:
    # 게스트가 /main 에 입력했던 값이 있으면 로그인 직후 계정 상태로 저장
    if session.get("g_dirty"):
        rev = int(session.get("g_rev") or 0)
        exp = int(session.get("g_exp") or 0)
        rate = float(session.get("g_rate") or 0.15)
        save_state(user_id, rev, exp, rate)
        session.pop("g_dirty", None)


def _safe_next_url(raw: str | None, fallback: str) -> str:
    if not raw:
        return fallback
    raw = (raw or "").strip()
    if raw in {"", "None", "/None", "null", "/null", "undefined", "/undefined"}:
        return fallback
    try:
        u = urlparse(raw)
        if u.scheme or u.netloc:
            return fallback
        if not u.path.startswith("/"):
            return fallback
        if u.path in {"/None", "/null", "/undefined"}:
            return fallback
        return u.path + (f"?{u.query}" if u.query else "")
    except Exception:
        return fallback


def _onboarding_redirect(next_url: str):
    safe_next = _safe_next_url(next_url, fallback=url_for("web_overview.overview"))
    return redirect(url_for("web_auth.onboarding", next=safe_next))


@web_auth_bp.route("/register", methods=["GET", "POST"])
def register():
    next_url = _safe_next_url(
        request.values.get("next"),
        fallback=url_for("web_inbox.import_page"),
    )

    if request.method == "POST":
        ip = client_ip()
        limited, wait_sec = hit_limit(key=f"web:register:ip:{ip}", limit=15, window_seconds=60)
        if limited:
            flash(f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요.", "error")
            return redirect(url_for("web_auth.register", next=next_url))

        email_raw = safe_str(request.form.get("email"), max_len=254)
        email = validate_email(email_raw)
        password = str(request.form.get("password") or "")[:256]
        password2 = str(request.form.get("password2") or "")[:256]

        if not email:
            flash("이메일 형식을 다시 확인해 주세요.", "error")
            return redirect(url_for("web_auth.register", next=next_url))

        if password != password2:
            flash("비밀번호가 서로 다릅니다.", "error")
            return redirect(url_for("web_auth.register", next=next_url))

        ok, msg = register_user(email, password)
        if not ok:
            flash(msg, "error")
            audit_event("register_failed", outcome="denied", detail=msg, extra={"ip": ip, "email": email[:120]})
            return redirect(url_for("web_auth.register", next=next_url))

        # 가입 직후 바로 로그인 상태로 붙여 첫 행동(가져오기/정리)으로 연결
        ok2, _, user_id = authenticate(email, password)
        if ok2 and user_id:
            session["user_id"] = user_id
            session.permanent = True
            _maybe_save_guest_to_db(user_id)
            flash("가입이 완료되었습니다. 바로 시작할게요.", "success")
            audit_event("register_success", user_pk=int(user_id), outcome="ok", extra={"ip": ip})
            return _onboarding_redirect(next_url)

        flash("가입이 완료되었습니다. 로그인해 주세요.", "success")
        return redirect(url_for("web_auth.login", next=next_url))

    return render_template("register.html", next_url=next_url)


@web_auth_bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next_url(
        request.values.get("next"),
        fallback=url_for("web_overview.overview"),
    )

    if request.method == "POST":
        ip = client_ip()
        identifier = safe_str(request.form.get("identifier"), max_len=254)
        password = str(request.form.get("password") or "")[:256]

        limited_ip, wait_ip = hit_limit(key=f"web:login:ip:{ip}", limit=30, window_seconds=60)
        limited_id, wait_id = hit_limit(
            key=f"web:login:id:{(identifier or '').strip().lower()}",
            limit=20,
            window_seconds=60,
        )
        if limited_ip or limited_id:
            flash(f"요청이 많아요. {max(wait_ip, wait_id)}초 후 다시 시도해 주세요.", "error")
            return redirect(url_for("web_auth.login", next=next_url))

        ok, msg, user_id = authenticate(identifier, password)
        if not ok:
            flash(msg, "error")
            audit_event(
                "login_failed",
                outcome="denied",
                detail=msg,
                extra={"ip": ip, "identifier": (identifier or "").strip()[:120]},
            )
            return redirect(url_for("web_auth.login", next=next_url))

        session["user_id"] = user_id
        session.permanent = True
        _maybe_save_guest_to_db(user_id)

        if not onboarding_is_done(user_id):
            audit_event("login_success", user_pk=int(user_id), outcome="ok", detail="onboarding pending", extra={"ip": ip})
            return _onboarding_redirect(next_url)

        flash("로그인되었습니다.", "success")
        audit_event("login_success", user_pk=int(user_id), outcome="ok", extra={"ip": ip})
        return redirect(next_url)

    return render_template("login.html", next_url=next_url)


@web_auth_bp.route("/onboarding", methods=["GET", "POST"], strict_slashes=False)
def onboarding():
    user_pk = session.get("user_id")
    if not user_pk:
        return redirect(url_for("web_auth.login", next=url_for("web_auth.onboarding")))

    next_url = _safe_next_url(
        request.values.get("next"),
        fallback=url_for("web_overview.overview"),
    )

    if onboarding_is_done(user_pk):
        return redirect(next_url)

    if request.method == "POST":
        ok, msg = save_onboarding(
            user_pk=user_pk,
            freelancer_type=safe_str(request.form.get("freelancer_type"), max_len=40),
            monthly_income_band=safe_str(request.form.get("monthly_income_band"), max_len=40),
            work_mode=safe_str(request.form.get("work_mode"), max_len=40),
            primary_goal=safe_str(request.form.get("primary_goal"), max_len=40),
        )
        if not ok:
            flash(msg, "error")
            return redirect(url_for("web_auth.onboarding", next=next_url))
        flash("설정이 저장되었습니다. 지금 상태 기준 결과를 먼저 보여드릴게요.", "success")
        return redirect(url_for("web_overview.overview"))

    return render_template("onboarding.html", next_url=next_url)


@web_auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method != "POST":
        flash("보안을 위해 로그아웃은 버튼으로 진행해 주세요.", "error")
        return redirect(url_for("web_main.landing"))
    session.clear()
    flash("로그아웃되었습니다.", "success")
    return redirect(url_for("web_main.landing"))

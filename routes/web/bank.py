from __future__ import annotations

import re
from uuid import uuid4

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import desc

from core.auth import login_required
from core.extensions import db
from core.security import sanitize_next_url
from domain.models import BankAccountLink, ImportJob
from services.input_sanitize import safe_str
from services.bank_sync_scheduler import run_manual_bank_sync_batch
from services.bank_accounts import (
    create_alias_account,
    fingerprint as account_fingerprint,
    get_or_create_by_fingerprint,
    last4 as account_last4,
    normalize_account_number,
)
from services.plan import (
    PlanPermissionError,
    can_activate_more_bank_links,
    ensure_can_link_bank_account,
    get_user_entitlements,
    plan_label_ko,
)
from services.popbill_easyfinbank import (
    PopbillApiError,
    PopbillConfigError,
    get_bank_account_mgt_url,
    list_bank_accounts,
)
from services.popbill_bank_quickguide import POPBILL_QUICKGUIDE_DOC_URL, load_popbill_bank_quickguide
from services.sensitive_mask import mask_sensitive_numbers

web_bank_bp = Blueprint("web_bank", __name__)

# 팝빌 권장 팝업 크기(문서/샘플 기준)
POPBILL_POPUP_W = 1550
POPBILL_POPUP_H = 680

# 은행 기관코드(일부) -> 사용자 친화 표기
BANK_CODE_NAME = {
    "0002": "산업은행",
    "0003": "IBK기업",
    "0004": "KB국민",
    "0007": "수협은행",
    "0011": "NH농협",
    "0020": "우리",
    "0023": "SC제일",
    "0027": "씨티",
    "0031": "iM뱅크(대구)",
    "0032": "부산",
    "0034": "광주",
    "0035": "제주",
    "0037": "전북",
    "0039": "경남",
    "0045": "새마을금고",
    "0048": "신협중앙회",
    "0071": "우체국",
    "0081": "하나",
    "0088": "신한",
}

_BANK_CODE_RE = re.compile(r"^\d{4}$")
_ACCOUNT_NO_RE = re.compile(r"^[0-9\-]{4,40}$")
_RAINBOW_COLORS = {
    "#DC2626",  # 빨강
    "#EA580C",  # 주황
    "#CA8A04",  # 노랑
    "#16A34A",  # 초록
    "#2563EB",  # 파랑
    "#1E3A8A",  # 남색
    "#7C3AED",  # 보라
}
_QUICK_SERVICE_PATTERNS = (
    "빠른조회",
    "스피드조회",
    "즉시조회",
    "해당 기관의 빠른조회 서비스를 이용 중인 계좌",
    "이용 중인 계좌가 아닙니다",
    "quick service",
)


def _mask_account(num: str) -> str:
    n = (num or "").strip()
    if len(n) <= 4:
        return n
    return f"****{n[-4:]}"


def _bank_name(code: str) -> str:
    c = (code or "").strip()
    return BANK_CODE_NAME.get(c, f"은행({c})" if c else "은행")


def _is_valid_account_identifiers(bank_code: str, account_number: str) -> bool:
    return bool(_BANK_CODE_RE.fullmatch(bank_code) and _ACCOUNT_NO_RE.fullmatch(account_number))


def _resolve_account_identifiers() -> tuple[str, str]:
    token = safe_str(request.form.get("account_token"), max_len=120)
    if token:
        token_map = session.get("bank_form_tokens")
        if isinstance(token_map, dict):
            picked = token_map.get(token)
            if isinstance(picked, dict):
                bank_code = safe_str(picked.get("bank_code"), max_len=8)
                account_number = safe_str(picked.get("account_number"), max_len=40)
                if bank_code and account_number:
                    return bank_code, account_number

    bank_code = safe_str(request.form.get("bank_code"), max_len=8)
    account_number = safe_str(request.form.get("account_number"), max_len=40)
    return bank_code, account_number


def _job_badge(job: ImportJob) -> str:
    """최근 동기화 로그에 보여줄 간단 상태."""
    if job.failed_rows and job.failed_rows > 0:
        return "warn"
    if job.inserted_rows and job.inserted_rows > 0:
        return "good"
    # inserted 0이면 중복만 있거나, 가져올게 없었던 상태
    return "ghost"


def _job_title(job: ImportJob) -> str:
    if job.failed_rows and job.failed_rows > 0:
        return "동기화(일부 실패)"
    if job.inserted_rows and job.inserted_rows > 0:
        return "동기화(완료)"
    return "동기화(변경 없음)"


def _normalize_rainbow_color(raw: str | None) -> str:
    color = str(raw or "").strip().upper()
    if not color:
        return "#2563EB"
    if not color.startswith("#"):
        color = f"#{color}"
    return color if color in _RAINBOW_COLORS else "#2563EB"


def _looks_like_quick_service_error(*parts: str) -> bool:
    haystack = " ".join([str(p or "").strip().lower() for p in parts if p]).strip()
    if not haystack:
        return False
    return any(str(token).lower() in haystack for token in _QUICK_SERVICE_PATTERNS)


def _extract_bank_code_from_text(*parts: str) -> str | None:
    for raw in parts:
        text = str(raw or "")
        if not text:
            continue
        m = re.search(r"\b(\d{4})-\*{4}", text)
        if m:
            code = str(m.group(1))
            if code in BANK_CODE_NAME:
                return code
        m = re.search(r"\b(\d{4})\b", text)
        if m:
            code = str(m.group(1))
            if code in BANK_CODE_NAME:
                return code
    return None


def _extract_link_id(raw: str | None) -> int | None:
    token = safe_str(raw, max_len=20)
    if not token or not token.isdigit():
        return None
    value = int(token)
    return value if value > 0 else None


def _account_obj_get(obj, key: str) -> str | None:
    if isinstance(obj, dict):
        val = obj.get(key)
    else:
        val = getattr(obj, key, None)
    return str(val).strip() if val is not None else None


@web_bank_bp.get("/bank")
@login_required
def index():
    user_id = int(session.get("user_id"))
    ent = get_user_entitlements(user_id)
    can_bank_link = bool(ent.can_bank_link)

    # 로컬 설정된 링크(= 이 앱에서 동기화 대상으로 선택한 계좌)
    links = (
        BankAccountLink.query.filter(BankAccountLink.user_pk == user_id)
        .order_by(BankAccountLink.bank_code.asc(), BankAccountLink.account_number.asc())
        .all()
    )
    link_map = {(l.bank_code, l.account_number): l for l in links}

    active_count = sum(1 for l in links if l.is_active)
    last_synced_at = None
    for l in links:
        if l.last_synced_at and (last_synced_at is None or l.last_synced_at > last_synced_at):
            last_synced_at = l.last_synced_at

    # 기본은 로컬 링크 목록만 노출(일반 GET에서 외부 호출 금지)
    popbill_accounts: list[dict[str, str]] = [
        {
            "bankCode": str(link.bank_code or "").strip(),
            "accountNumber": str(link.account_number or "").strip(),
            "accountName": str(link.alias or "").strip() or "계좌",
        }
        for link in links
    ]
    popbill_error = None
    popbill_configured = True
    refresh_accounts = safe_str(request.args.get("refresh_accounts"), max_len=4) == "1"
    if can_bank_link and refresh_accounts:
        try:
            popbill_accounts = list_bank_accounts()
        except PopbillConfigError as e:
            popbill_configured = False
            current_app.logger.warning("[WARN][계좌 연동][설정 누락] %s", mask_sensitive_numbers(str(e))[:220])
            popbill_error = "계좌 연동 설정이 아직 준비되지 않았어요(개발용 설정 필요). 잠시 후 다시 시도해주세요."
        except PopbillApiError as e:
            current_app.logger.warning("[WARN][계좌 연동][API 오류] %s", mask_sensitive_numbers(str(e))[:220])
            popbill_error = "계좌 정보를 불러오지 못했어요. 잠시 후 다시 시도해주세요."
        except Exception as e:
            current_app.logger.exception("[ERROR][계좌 연동][계좌 목록 조회 실패]")
            popbill_error = "계좌 정보를 불러오지 못했어요. 잠시 후 다시 시도해주세요."

    guide_snapshot = load_popbill_bank_quickguide()
    guide_doc_url = str(guide_snapshot.get("official_doc_url") or POPBILL_QUICKGUIDE_DOC_URL)
    guide_rows = guide_snapshot.get("banks") if isinstance(guide_snapshot.get("banks"), list) else []
    guide_map: dict[str, dict[str, str | list[str]]] = {}
    for row in guide_rows:
        if not isinstance(row, dict):
            continue
        bank_code = safe_str(row.get("bank_code"), max_len=8)
        if not bank_code:
            continue
        corporate_steps = [
            safe_str(x, max_len=160) for x in (row.get("corporate_steps") or []) if safe_str(x, max_len=160)
        ][:8]
        personal_steps = [
            safe_str(x, max_len=160) for x in (row.get("personal_steps") or []) if safe_str(x, max_len=160)
        ][:8]
        if not corporate_steps and personal_steps:
            corporate_steps = list(personal_steps)
        if not personal_steps and corporate_steps:
            personal_steps = list(corporate_steps)
        guide_map[bank_code] = {
            "bank_code": bank_code,
            "bank_name": safe_str(row.get("bank_name"), max_len=40) or _bank_name(bank_code),
            "service_name": safe_str(row.get("service_name"), max_len=60) or "빠른조회 서비스",
            "intro_notice": safe_str(row.get("intro_notice"), max_len=180) or "이 은행은 먼저 빠른조회 등록이 필요해요.",
            "homepage_url": safe_str(row.get("homepage_url"), max_len=300) or guide_doc_url,
            "corporate_steps": corporate_steps,
            "personal_steps": personal_steps,
            "extra_note": safe_str(row.get("extra_note"), max_len=200),
            "official_doc_url": guide_doc_url,
        }

    account_form_tokens: dict[str, str] = {}
    if popbill_accounts:
        token_map: dict[str, dict[str, str]] = {}
        for acc in popbill_accounts:
            bank_code = safe_str(_account_obj_get(acc, "bankCode"), max_len=8)
            account_number = safe_str(_account_obj_get(acc, "accountNumber"), max_len=40)
            if not _is_valid_account_identifiers(bank_code, account_number):
                continue
            token = uuid4().hex
            token_map[token] = {"bank_code": bank_code, "account_number": account_number}
            account_form_tokens[f"{bank_code}:{account_number}"] = token
        session["bank_form_tokens"] = token_map

    available_bank_codes: set[str] = set(guide_map.keys())
    bank_guide_options = [
        {"bank_code": code, "bank_name": safe_str((guide_map.get(code) or {}).get("bank_name"), max_len=40) or _bank_name(code)}
        for code in sorted(available_bank_codes)
        if _BANK_CODE_RE.fullmatch(code)
    ]

    selected_guide_bank = safe_str(request.args.get("guide_bank") or request.args.get("bank_code"), max_len=8)
    if not _BANK_CODE_RE.fullmatch(selected_guide_bank or ""):
        selected_guide_bank = ""
    if selected_guide_bank and selected_guide_bank not in {x["bank_code"] for x in bank_guide_options}:
        selected_guide_bank = ""
    selected_guide_mode = safe_str(request.args.get("guide_mode"), max_len=16).lower()
    if selected_guide_mode not in {"personal", "corporate"}:
        selected_guide_mode = "personal"

    link_fail_kind = safe_str(request.args.get("link_fail"), max_len=24).lower()
    show_link_fallback = link_fail_kind in {"quick_service", "sync_error"}
    if show_link_fallback and (not selected_guide_bank):
        selected_guide_bank = safe_str(request.args.get("bank_code"), max_len=8)
        if not _BANK_CODE_RE.fullmatch(selected_guide_bank or ""):
            selected_guide_bank = ""
    fallback_official_url = guide_doc_url
    if selected_guide_bank and isinstance(guide_map.get(selected_guide_bank), dict):
        fallback_official_url = safe_str(
            guide_map[selected_guide_bank].get("homepage_url"),
            max_len=300,
        ) or guide_doc_url

    # 최근 동기화 작업 로그(신뢰/디버깅)
    recent_jobs = (
        ImportJob.query.filter(ImportJob.user_pk == user_id, ImportJob.source == "popbill")
        .order_by(desc(ImportJob.created_at))
        .limit(5)
        .all()
    )

    return render_template(
        "bank/index.html",
        can_bank_link=can_bank_link,
        plan_code=ent.plan_code,
        plan_label=plan_label_ko(ent.plan_code),
        plan_status=ent.plan_status,
        sync_interval_minutes=ent.sync_interval_minutes,
        max_linked_accounts=int(ent.max_linked_accounts),
        popbill_accounts=popbill_accounts,
        popbill_configured=popbill_configured,
        popbill_error=popbill_error,
        link_map=link_map,
        total_count=len(links),
        active_count=active_count,
        last_synced_at=last_synced_at,
        bank_name=_bank_name,
        mask_account=_mask_account,
        popup_w=POPBILL_POPUP_W,
        popup_h=POPBILL_POPUP_H,
        recent_jobs=recent_jobs,
        job_badge=_job_badge,
        job_title=_job_title,
        account_form_tokens=account_form_tokens,
        bank_guide_options=bank_guide_options,
        bank_guide_map=guide_map,
        guide_doc_url=guide_doc_url,
        guide_updated_at=safe_str(guide_snapshot.get("updated_at"), max_len=64),
        selected_guide_bank=selected_guide_bank,
        selected_guide_mode=selected_guide_mode,
        show_link_fallback=show_link_fallback,
        link_fail_kind=link_fail_kind,
        fallback_official_url=fallback_official_url,
        refresh_accounts=refresh_accounts,
    )


@web_bank_bp.get("/bank/popbill-url")
@login_required
def popbill_url():
    """팝빌 '계좌 등록/관리' 팝업 URL은 유효시간이 짧을 수 있어 클릭 시점에 생성한다."""
    user_id = int(session.get("user_id"))
    try:
        ensure_can_link_bank_account(user_id)
    except PlanPermissionError as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(e),
                }
            ),
            403,
        )
    try:
        url = get_bank_account_mgt_url()
        return jsonify({"ok": True, "url": url, "w": POPBILL_POPUP_W, "h": POPBILL_POPUP_H})
    except PopbillConfigError as e:
        current_app.logger.warning("[WARN][계좌 연동][설정 누락] %s", mask_sensitive_numbers(str(e))[:220])
        return jsonify({"ok": False, "error": "계좌 연동 설정이 아직 준비되지 않았어요(개발용 설정 필요). 잠시 후 다시 시도해주세요."}), 400
    except PopbillApiError as e:
        current_app.logger.warning("[WARN][계좌 연동][API 오류] %s", mask_sensitive_numbers(str(e))[:220])
        return jsonify({"ok": False, "error": "계좌 연동 정보를 불러오지 못했어요. 잠시 후 다시 시도해주세요."}), 400
    except Exception as e:
        current_app.logger.exception("[ERROR][계좌 연동][팝업 URL 생성 실패]")
        return jsonify({"ok": False, "error": "일시적인 오류가 발생했어요. 잠시 후 다시 시도해주세요."}), 503


@web_bank_bp.post("/bank/toggle")
@login_required
def toggle():
    user_id = int(session.get("user_id"))
    bank_code, account_number = _resolve_account_identifiers()
    # checkbox는 체크 시 'on' / 미체크 시 None
    is_active = request.form.get("is_active") is not None

    if not _is_valid_account_identifiers(bank_code, account_number):
        flash("계좌 정보가 올바르지 않습니다.", "error")
        return redirect(url_for("web_bank.index"))

    link = BankAccountLink.query.filter_by(
        user_pk=user_id, bank_code=bank_code, account_number=account_number
    ).first()

    # OFF로 끄는 동작은 플랜과 무관하게 허용(다운그레이드 사용자 보호)
    if not is_active:
        if not link:
            flash("변경할 계좌를 찾지 못했어요.", "error")
            return redirect(url_for("web_bank.index"))
        link.is_active = False
        db.session.add(link)
        db.session.commit()
        flash("동기화 대상을 해제했어요.", "success")
        return redirect(url_for("web_bank.index"))

    # ON으로 켜는 동작은 베이직 이상 플랜에서만 허용
    try:
        ensure_can_link_bank_account(user_id)
    except PlanPermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("web_main.pricing"))

    # 신규 ON 또는 OFF->ON 전환 시 플랜 허용 계좌 수를 서버에서 강제한다.
    additional = 0
    if not link or not bool(link.is_active):
        additional = 1
    can_add, max_allowed = can_activate_more_bank_links(user_id, additional=additional)
    if not can_add:
        flash(f"현재 플랜에서 연결 가능한 계좌는 최대 {max_allowed}개예요.", "error")
        return redirect(url_for("web_bank.index"))

    digits = normalize_account_number(account_number)
    fp = account_fingerprint(digits)
    l4 = account_last4(digits)
    account_row = get_or_create_by_fingerprint(
        user_pk=int(user_id),
        bank_code_opt=bank_code,
        account_fingerprint=fp,
        account_last4=l4,
        alias_opt=None,
    )

    if not link:
        link = BankAccountLink(
            user_pk=user_id,
            bank_code=bank_code,
            account_number=account_number,
            bank_account_id=int(account_row.id),
            is_active=is_active,
            alias=None,
            last_synced_at=None,
        )
        db.session.add(link)
    else:
        link.is_active = is_active
        link.bank_account_id = int(account_row.id)

    db.session.commit()

    flash("동기화 대상이 업데이트되었습니다.", "success")
    return redirect(url_for("web_bank.index"))


@web_bank_bp.post("/bank/alias")
@login_required
def alias():
    user_id = int(session.get("user_id"))
    try:
        ensure_can_link_bank_account(user_id)
    except PlanPermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("web_main.pricing"))

    bank_code, account_number = _resolve_account_identifiers()
    alias = safe_str(request.form.get("alias"), max_len=80)

    if not _is_valid_account_identifiers(bank_code, account_number):
        flash("계좌 정보가 올바르지 않습니다.", "error")
        return redirect(url_for("web_bank.index"))

    link = BankAccountLink.query.filter_by(
        user_pk=user_id, bank_code=bank_code, account_number=account_number
    ).first()

    if not link:
        flash("먼저 해당 계좌를 '동기화 ON'으로 켜주세요.", "error")
        return redirect(url_for("web_bank.index"))

    link.alias = alias or None
    if getattr(link, "bank_account_id", None):
        account_row = get_or_create_by_fingerprint(
            user_pk=int(user_id),
            bank_code_opt=bank_code,
            account_fingerprint=account_fingerprint(normalize_account_number(account_number)),
            account_last4=account_last4(normalize_account_number(account_number)),
            alias_opt=(alias or None),
        )
        link.bank_account_id = int(account_row.id)
    db.session.commit()

    flash("별칭이 저장되었습니다.", "success")
    return redirect(url_for("web_bank.index"))


@web_bank_bp.post("/bank/sync")
@login_required
def sync_now():
    user_id = int(session.get("user_id"))
    try:
        ensure_can_link_bank_account(user_id)
    except PlanPermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("web_main.pricing"))

    mode = safe_str(request.form.get("mode"), max_len=40).lower()
    link_id = _extract_link_id(request.form.get("link_id"))
    use_backfill_3m = mode in {"backfill_3m", "refresh_3m", "backfill"}

    try:
        result = run_manual_bank_sync_batch(
            user_pk=int(user_id),
            use_backfill_3m=bool(use_backfill_3m),
            link_id=(int(link_id) if (use_backfill_3m and link_id is not None) else None),
            dry_run=False,
        )
        if int(result.total_links) <= 0:
            flash("동기화할 활성 계좌가 없어요. 먼저 계좌를 연동해 주세요.", "warn")
            return redirect(url_for("web_bank.index"))

        if result.failed_count or result.failed_rows_total:
            has_quick_service_issue = False
            detected_bank_code = None
            for err in (result.errors or []):
                if not isinstance(err, dict):
                    continue
                account_hint = safe_str(err.get("account"), max_len=120)
                error_text = safe_str(err.get("error"), max_len=260)
                reason_text = safe_str(err.get("reason"), max_len=260)
                if _looks_like_quick_service_error(error_text, reason_text):
                    has_quick_service_issue = True
                    detected_bank_code = _extract_bank_code_from_text(account_hint, error_text, reason_text)
                    break

            if has_quick_service_issue:
                flash("이 계좌는 아직 바로 연결할 수 없어요. 은행 사이트에서 빠른조회 등록 후 다시 시도해 주세요.", "warn")
                return redirect(
                    url_for(
                        "web_bank.index",
                        link_fail="quick_service",
                        bank_code=(detected_bank_code or None),
                        guide_bank=(detected_bank_code or None),
                    )
                )

            if use_backfill_3m:
                flash(
                    f"최근 3개월 중 일부 기간은 가져오지 못했어요. 잠시 후 다시 시도해 주세요. (추가 {result.inserted_rows_total}건, 중복 {result.duplicate_rows_total}건)",
                    "warn",
                )
            else:
                flash(
                    f"동기화 완료: {result.inserted_rows_total}건 추가, {result.duplicate_rows_total}건 중복, {result.failed_rows_total}건 실패",
                    "warn",
                )
            return redirect(url_for("web_bank.index", link_fail="sync_error"))
        else:
            if use_backfill_3m:
                flash(
                    f"팝빌에서 조회 가능한 최근 3개월 내역을 가져왔어요. (추가 {result.inserted_rows_total}건, 중복 {result.duplicate_rows_total}건)",
                    "success",
                )
            else:
                flash(
                    f"동기화 완료: {result.inserted_rows_total}건 추가, {result.duplicate_rows_total}건 중복",
                    "success",
                )

    except PopbillConfigError as e:
        current_app.logger.warning("[WARN][계좌 연동][설정 누락] %s", mask_sensitive_numbers(str(e))[:220])
        flash("계좌 연동 설정이 아직 준비되지 않았어요(개발용 설정 필요). 잠시 후 다시 시도해주세요.", "error")
    except Exception as e:
        current_app.logger.exception("[ERROR][계좌 연동][동기화 실패]")
        flash("동기화 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")
        return redirect(url_for("web_bank.index", link_fail="sync_error"))

    return redirect(url_for("web_bank.index"))


@web_bank_bp.post("/bank/account-create")
@login_required
def account_create():
    user_id = int(session.get("user_id"))
    alias = safe_str(request.form.get("alias"), max_len=64)
    account_number_raw = safe_str(request.form.get("account_number"), max_len=40)
    color_hex = _normalize_rainbow_color(request.form.get("color_hex"))
    next_url = sanitize_next_url(request.form.get("next"), fallback=url_for("web_bank.index"))

    if not alias:
        flash("계좌 별명을 입력해 주세요.", "error")
        return redirect(next_url)

    digits = normalize_account_number(account_number_raw)
    if account_number_raw and (not digits or len(digits) < 8 or len(digits) > 20):
        flash("계좌번호를 다시 확인해 주세요. 숫자 8~20자리로 입력해 주세요.", "error")
        return redirect(next_url)

    try:
        if digits:
            row = get_or_create_by_fingerprint(
                user_pk=int(user_id),
                bank_code_opt=None,
                account_fingerprint=account_fingerprint(digits),
                account_last4=account_last4(digits),
                alias_opt=alias,
            )
            row.alias = alias
            row.color_hex = color_hex
            db.session.add(row)
        else:
            create_alias_account(user_pk=int(user_id), alias=alias, color_hex=color_hex)
        db.session.commit()
        flash("계좌를 추가했어요.", "success")
    except Exception:
        db.session.rollback()
        flash("계좌 추가 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.", "error")
    return redirect(next_url)

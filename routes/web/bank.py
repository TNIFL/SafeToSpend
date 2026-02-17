from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import desc

from core.auth import login_required
from core.extensions import db
from domain.models import BankAccountLink, ImportJob
from services.import_popbill import PopbillImportError, sync_popbill_for_user
from services.popbill_easyfinbank import (
    PopbillApiError,
    PopbillConfigError,
    get_bank_account_mgt_url,
    list_bank_accounts,
)

web_bank_bp = Blueprint("web_bank", __name__)

# 팝빌 권장 팝업 크기(문서/샘플 기준)
POPBILL_POPUP_W = 1550
POPBILL_POPUP_H = 680

# 은행 기관코드(일부) -> 사용자 친화 표기
BANK_CODE_NAME = {
    "0003": "IBK기업",
    "0004": "KB국민",
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
    "0081": "하나",
    "0088": "신한",
    "0090": "카카오뱅크",
    "0092": "토스뱅크",
}


def _mask_account(num: str) -> str:
    n = (num or "").strip()
    if len(n) <= 4:
        return n
    return f"****{n[-4:]}"


def _bank_name(code: str) -> str:
    c = (code or "").strip()
    return BANK_CODE_NAME.get(c, f"은행({c})" if c else "은행")


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


@web_bank_bp.get("/bank")
@login_required
def index():
    user_id = session.get("user_id")

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

    # 팝빌에 등록된 계좌 목록(표시용)
    popbill_accounts = []
    popbill_error = None
    popbill_configured = True
    try:
        popbill_accounts = list_bank_accounts()
    except PopbillConfigError as e:
        popbill_configured = False
        popbill_error = str(e)
    except PopbillApiError as e:
        popbill_error = str(e)
    except Exception as e:
        popbill_error = f"알 수 없는 오류: {e}"

    # 최근 동기화 작업 로그(신뢰/디버깅)
    recent_jobs = (
        ImportJob.query.filter(ImportJob.user_pk == user_id, ImportJob.source == "popbill")
        .order_by(desc(ImportJob.created_at))
        .limit(5)
        .all()
    )

    return render_template(
        "bank/index.html",
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
    )


@web_bank_bp.get("/bank/popbill-url")
@login_required
def popbill_url():
    """팝빌 '계좌 등록/관리' 팝업 URL은 유효시간이 짧을 수 있어 클릭 시점에 생성한다."""
    try:
        url = get_bank_account_mgt_url()
        return jsonify({"ok": True, "url": url, "w": POPBILL_POPUP_W, "h": POPBILL_POPUP_H})
    except PopbillConfigError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except PopbillApiError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"알 수 없는 오류: {e}"}), 500


@web_bank_bp.post("/bank/toggle")
@login_required
def toggle():
    user_id = session.get("user_id")

    bank_code = (request.form.get("bank_code") or "").strip()
    account_number = (request.form.get("account_number") or "").strip()
    # checkbox는 체크 시 'on' / 미체크 시 None
    is_active = request.form.get("is_active") is not None

    if not bank_code or not account_number:
        flash("계좌 정보가 올바르지 않습니다.", "error")
        return redirect(url_for("web_bank.index"))

    link = BankAccountLink.query.filter_by(
        user_pk=user_id, bank_code=bank_code, account_number=account_number
    ).first()

    if not link:
        link = BankAccountLink(
            user_pk=user_id,
            bank_code=bank_code,
            account_number=account_number,
            is_active=is_active,
            alias=None,
            last_synced_at=None,
        )
        db.session.add(link)
    else:
        link.is_active = is_active

    db.session.commit()

    flash("동기화 대상이 업데이트되었습니다.", "success")
    return redirect(url_for("web_bank.index"))


@web_bank_bp.post("/bank/alias")
@login_required
def alias():
    user_id = session.get("user_id")

    bank_code = (request.form.get("bank_code") or "").strip()
    account_number = (request.form.get("account_number") or "").strip()
    alias = (request.form.get("alias") or "").strip()

    if not bank_code or not account_number:
        flash("계좌 정보가 올바르지 않습니다.", "error")
        return redirect(url_for("web_bank.index"))

    link = BankAccountLink.query.filter_by(
        user_pk=user_id, bank_code=bank_code, account_number=account_number
    ).first()

    if not link:
        flash("먼저 해당 계좌를 '동기화 ON'으로 켜주세요.", "error")
        return redirect(url_for("web_bank.index"))

    link.alias = alias or None
    db.session.commit()

    flash("별칭이 저장되었습니다.", "success")
    return redirect(url_for("web_bank.index"))


@web_bank_bp.post("/bank/sync")
@login_required
def sync_now():
    user_id = session.get("user_id")

    try:
        result = sync_popbill_for_user(user_id)
        if result.failed_rows:
            flash(
                f"동기화 완료: {result.inserted_rows}건 추가, {result.duplicate_rows}건 중복, {result.failed_rows}건 실패",
                "warn",
            )
        else:
            flash(
                f"동기화 완료: {result.inserted_rows}건 추가, {result.duplicate_rows}건 중복",
                "success",
            )

    except PopbillConfigError as e:
        flash(str(e), "error")
    except PopbillImportError as e:
        flash(f"동기화 실패: {e}", "error")
    except Exception as e:
        flash(f"동기화 실패(알 수 없는 오류): {e}", "error")

    return redirect(url_for("web_bank.index"))

# routes/web/inbox.py
import os
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_file, current_app

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, ExpenseLabel, IncomeLabel, UserBankAccount
from services.evidence_vault import attach_evidence_file, delete_evidence_file, evidence_abs_path
from services.input_sanitize import parse_bool_yn, safe_str
from services.risk import compute_inbox, compute_inbox_counts
from services.rate_limit import client_ip, hit_limit
from services.security_audit import audit_event
from services.bank_accounts import get_or_create_by_fingerprint, list_accounts_for_ui
from services.upload_account_detect import detect_account_from_file_head, find_account_by_fingerprint

from services.import_csv import (
    CsvImportError,
    normalize_csv_import_error,
    save_temp_upload,
    read_csv_preview,
    detect_mapping_for_preview,
    temp_upload_path,
    save_cached_mapping,
    import_csv_to_db,
)

web_inbox_bp = Blueprint("web_inbox", __name__)


def _safe_next_url(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        u = urlparse(raw)
        if u.scheme or u.netloc:
            return None
        if not u.path.startswith("/"):
            return None
        return u.path + (f"?{u.query}" if u.query else "")
    except Exception:
        return None


def _cleanup_tmp_csv(user_pk: int, token: str) -> None:
    try:
        p = temp_upload_path(user_pk, token)
        if p and os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


@web_inbox_bp.route("/inbox", methods=["GET"])
@login_required
def index():
    user_pk = session["user_id"]
    tab = safe_str(request.args.get("tab") or "evidence", max_len=16)
    if tab not in ("evidence", "mixed", "income"):
        tab = "evidence"

    counts = compute_inbox_counts(user_pk)
    items = compute_inbox(user_pk, tab=tab, limit=60)
    show_import_done = (request.args.get("imported") or "").strip() == "1"

    return render_template(
        "inbox.html",
        tab=tab,
        counts=counts,
        items=items,
        show_import_done=show_import_done,
        review_url=url_for("web_calendar.review"),
    )


# ---------- CSV Import ----------

@web_inbox_bp.route("/inbox/import", methods=["GET"])
@login_required
def import_page():
    return render_template("inbox_import.html")


@web_inbox_bp.route("/inbox/import/preview", methods=["POST"])
@login_required
def import_preview():
    user_pk = session["user_id"]
    f = request.files.get("csv")
    token = None

    try:
        token, filepath, filename = save_temp_upload(f, user_pk=user_pk)
        headers, rows, delimiter = read_csv_preview(filepath)

        sig, mapping, conf, date_rate, amt_rate, src = detect_mapping_for_preview(
            user_pk=user_pk,
            headers=headers,
            rows=rows,
            delimiter=delimiter,
        )

        detected_account = detect_account_from_file_head(filepath)
        detected_match = None
        if detected_account.get("found"):
            detected_match = find_account_by_fingerprint(user_pk=user_pk, fp=detected_account.get("fingerprint"))
        account_options = list_accounts_for_ui(user_pk)
        matched_account_id = int(detected_match.id) if detected_match else 0

        session["csv_import"] = {
            "token": token,
            "filename": filename,
            "signature": sig,
            "delimiter": delimiter,
            "detected_account": {
                "found": bool(detected_account.get("found")),
                "fingerprint": detected_account.get("fingerprint"),
                "last4": detected_account.get("last4"),
                "confidence": int(detected_account.get("confidence") or 0),
                "matched_account_id": matched_account_id or None,
            },
        }

        auto_ok = (
            conf >= 90
            and date_rate >= 0.90
            and amt_rate >= 0.90
            and (mapping.get("date") or "").strip()
            and (
                (mapping.get("amount") or "").strip()
                or (mapping.get("in_amount") or "").strip()
                or (mapping.get("out_amount") or "").strip()
            )
        )
        if auto_ok:
            flash("자동 인식이 완료됐어요. 계좌만 확인하고 바로 가져오면 됩니다.", "success")

        return render_template(
            "inbox_import_map.html",
            token=token,
            filename=filename,
            delimiter=delimiter,
            headers=headers,
            rows=rows,
            mapping=mapping,
            confidence=conf,
            src=src,
            detected_account=detected_account,
            matched_account_id=matched_account_id,
            account_options=account_options,
        )

    except CsvImportError as e:
        if token:
            _cleanup_tmp_csv(user_pk, token)
        flash(normalize_csv_import_error(str(e)), "error")
        return redirect(url_for("web_inbox.import_page"))
    except Exception:
        if token:
            _cleanup_tmp_csv(user_pk, token)
        current_app.logger.exception("[import_preview] CSV 처리 실패")
        flash("CSV 처리 중 오류가 발생했습니다. 파일 형식을 확인하고 다시 시도해주세요.", "error")
        return redirect(url_for("web_inbox.import_page"))


@web_inbox_bp.route("/inbox/import/commit", methods=["POST"])
@login_required
def import_commit():
    user_pk = session["user_id"]
    payload = session.get("csv_import") or {}
    token = safe_str(request.form.get("token"), max_len=120)

    if not payload or token != payload.get("token"):
        flash("업로드 세션이 만료되었습니다. 다시 업로드해주세요.", "error")
        return redirect(url_for("web_inbox.import_page"))

    filepath = temp_upload_path(user_pk, token)
    filename = payload.get("filename") or "upload.csv"
    sig = payload.get("signature") or ""
    delimiter = payload.get("delimiter") or ","
    detected_payload = payload.get("detected_account") if isinstance(payload.get("detected_account"), dict) else {}

    mapping = {
        "date": safe_str(request.form.get("map_date"), max_len=64),
        "amount": safe_str(request.form.get("map_amount"), max_len=64),
        "in_amount": safe_str(request.form.get("map_in_amount"), max_len=64),
        "out_amount": safe_str(request.form.get("map_out_amount"), max_len=64),
        "direction": safe_str(request.form.get("map_direction"), max_len=64),
        "counterparty": safe_str(request.form.get("map_counterparty"), max_len=64),
        "memo": safe_str(request.form.get("map_memo"), max_len=64),
    }
    selected_bank_account_raw = safe_str(request.form.get("bank_account_id"), max_len=32)
    detected_create_new = parse_bool_yn(request.form.get("detected_create_new"))
    detected_alias = safe_str(request.form.get("detected_alias"), max_len=64) or None

    try:
        bank_account_id = None
        if selected_bank_account_raw:
            try:
                selected_bank_account_id = int(selected_bank_account_raw)
            except Exception:
                selected_bank_account_id = 0
            if selected_bank_account_id > 0:
                owned = (
                    UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
                    .filter(UserBankAccount.id == int(selected_bank_account_id))
                    .first()
                )
                if owned:
                    bank_account_id = int(owned.id)
        if not bank_account_id and detected_create_new:
            fp = str(detected_payload.get("fingerprint") or "").strip()
            l4 = str(detected_payload.get("last4") or "").strip()
            if fp and l4:
                created = get_or_create_by_fingerprint(
                    user_pk=int(user_pk),
                    bank_code_opt=None,
                    account_fingerprint=fp,
                    account_last4=l4,
                    alias_opt=detected_alias,
                )
                bank_account_id = int(created.id)

        res = import_csv_to_db(
            user_pk=user_pk,
            filepath=filepath,
            filename=filename,
            mapping=mapping,
            bank_account_id=bank_account_id,
        )

        if sig:
            save_cached_mapping(
                user_pk=user_pk,
                signature=sig,
                delimiter=delimiter,
                mapping=mapping,
                meta={"source": "user", "confidence": 100},
            )

        flash(
            f"CSV 가져오기 완료: 총 {res.total_rows}행 / 추가 {res.inserted_rows} / 중복 {res.duplicate_rows} / 실패 {res.failed_rows}",
            "success",
        )
        session.pop("csv_import", None)
        _cleanup_tmp_csv(user_pk, token)
        return redirect(url_for("web_inbox.index", tab="evidence", imported=1))

    except CsvImportError as e:
        flash(normalize_csv_import_error(str(e)), "error")
        return redirect(url_for("web_inbox.import_page"))
    except Exception:
        current_app.logger.exception("[import_commit] CSV 가져오기 실패")
        flash("가져오기 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", "error")
        return redirect(url_for("web_inbox.import_page"))


# ---------- Evidence actions ----------

@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/mark", methods=["POST"])
@login_required
def evidence_mark(evidence_id: int):
    user_pk = session["user_id"]
    status = safe_str(request.form.get("status") or "attached", max_len=16)
    tab = safe_str(request.form.get("tab") or "evidence", max_len=16)

    if status not in ("attached", "not_needed", "missing"):
        status = "attached"

    row = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not row:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab=tab))

    row.status = status
    db.session.commit()
    flash("처리되었습니다.", "success")
    nxt = _safe_next_url(request.form.get("next") or request.referrer)
    return redirect(nxt or url_for("web_inbox.index", tab=tab))


@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/upload", methods=["POST"])
@login_required
def evidence_upload(evidence_id: int):
    user_pk = session["user_id"]
    tab = safe_str(request.form.get("tab") or "evidence", max_len=16)
    f = request.files.get("file")
    ip = client_ip()
    limited, wait_sec = hit_limit(key=f"web:evidence:upload:ip:{ip}", limit=40, window_seconds=60)
    if limited:
        flash(f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요.", "error")
        nxt = _safe_next_url(request.form.get("next") or request.referrer)
        return redirect(nxt or url_for("web_inbox.index", tab=tab))

    try:
        attach_evidence_file(
            user_pk=user_pk,
            evidence_id=evidence_id,
            uploaded=f,
            max_bytes=int(request.max_content_length or 0) or (20 * 1024 * 1024),
        )
        flash("증빙이 업로드되었습니다.", "success")
        audit_event("evidence_upload", user_pk=int(user_pk), outcome="ok", extra={"ip": ip, "evidence_id": int(evidence_id)})
    except Exception as e:
        msg = str(e or "").strip()
        if ("허용되지 않는 파일 형식" in msg) or ("파일이 없습니다" in msg) or ("용량" in msg):
            flash(msg, "error")
        else:
            current_app.logger.exception("[ERROR][내역 가져오기][증빙 업로드 실패]")
            flash("업로드 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")
        audit_event("evidence_upload_failed", user_pk=int(user_pk), outcome="denied", detail=(msg or "upload failed"), extra={"ip": ip, "evidence_id": int(evidence_id)})

    nxt = _safe_next_url(request.form.get("next") or request.referrer)
    return redirect(nxt or url_for("web_inbox.index", tab=tab))


@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/download", methods=["GET"])
@login_required
def evidence_download(evidence_id: int):
    user_pk = session["user_id"]
    ev = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not ev or not ev.file_key:
        flash("파일이 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab="evidence"))

    try:
        p = evidence_abs_path(ev.file_key)
    except Exception:
        flash("파일을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab="evidence"))
    if not p.exists():
        flash("파일을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab="evidence"))

    return send_file(
        p,
        as_attachment=True,
        download_name=(ev.original_filename or p.name),
        mimetype=(ev.mime_type or "application/octet-stream"),
        max_age=0,
    )


@web_inbox_bp.route("/inbox/evidence/<int:evidence_id>/delete", methods=["POST"])
@login_required
def evidence_delete(evidence_id: int):
    user_pk = session["user_id"]
    tab = safe_str(request.form.get("tab") or "evidence", max_len=16)
    try:
        delete_evidence_file(user_pk=user_pk, evidence_id=evidence_id)
        flash("즉시 삭제되었습니다.", "success")
    except Exception as e:
        msg = str(e or "").strip()
        if "대상을 찾을 수 없습니다" in msg:
            flash("대상을 찾을 수 없습니다.", "error")
        else:
            current_app.logger.exception("[ERROR][내역 가져오기][증빙 삭제 실패]")
            flash("삭제 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")
    nxt = _safe_next_url(request.form.get("next") or request.referrer)
    return redirect(nxt or url_for("web_inbox.index", tab=tab))


@web_inbox_bp.route("/inbox/expense/<int:label_id>/label", methods=["POST"])
@login_required
def expense_label(label_id: int):
    user_pk = session["user_id"]
    status = safe_str(request.form.get("status") or "business", max_len=16)
    tab = safe_str(request.form.get("tab") or "mixed", max_len=16)

    if status not in ("business", "personal", "mixed", "unknown"):
        status = "business"

    row = ExpenseLabel.query.filter_by(id=label_id, user_pk=user_pk).first()
    if not row:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab=tab))

    row.status = status
    row.labeled_by = "user"
    db.session.commit()
    flash("라벨이 저장되었습니다.", "success")
    return redirect(url_for("web_inbox.index", tab=tab))


@web_inbox_bp.route("/inbox/income/<int:label_id>/label", methods=["POST"])
@login_required
def income_label(label_id: int):
    user_pk = session["user_id"]
    status = safe_str(request.form.get("status") or "income", max_len=16)
    tab = safe_str(request.form.get("tab") or "income", max_len=16)

    if status not in ("income", "non_income", "unknown"):
        status = "income"

    row = IncomeLabel.query.filter_by(id=label_id, user_pk=user_pk).first()
    if not row:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(url_for("web_inbox.index", tab=tab))

    row.status = status
    row.labeled_by = "user"
    db.session.commit()
    flash("라벨이 저장되었습니다.", "success")
    return redirect(url_for("web_inbox.index", tab=tab))

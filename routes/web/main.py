# routes/web/main.py
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, inspect
from sqlalchemy.exc import IntegrityError

from core.extensions import db
from core.time import utcnow
from domain.models import (
    ActionLog,
    BankAccountLink,
    CounterpartyExpenseRule,
    CounterpartyRule,
    CsvFormatMapping,
    DashboardEntry,
    DashboardSnapshot,
    EvidenceItem,
    ExpenseLabel,
    HoldDecision,
    ImportJob,
    IncomeLabel,
    ReceiptBatch,
    ReceiptItem,
    RecurringCandidate,
    RecurringRule,
    SafeToSpendSettings,
    TaxBufferLedger,
    TaxProfile,
    Transaction,
    User,
    UserDashboardState,
    WeeklyTask,
)
from services.import_csv import (
    CsvImportError,
    detect_mapping_for_preview,
    import_csv_to_db,
    normalize_csv_import_error,
    read_csv_preview,
    save_temp_upload,
)
from services.risk import compute_overview, compute_tax_estimate
from services.plan import get_user_entitlements

web_main_bp = Blueprint("web_main", __name__)
KST = ZoneInfo("Asia/Seoul")


def _month_key_now_kst() -> str:
    return datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m")


def _ensure_preview_user() -> int:
    token = str(session.get("preview_user_token") or "").strip()
    if not token:
        token = secrets.token_hex(12)
        session["preview_user_token"] = token
        session.modified = True

    email = f"preview+{token}@safetospend.local"
    user = User.query.filter_by(email=email).first()
    if user:
        return int(user.id)

    user = User(email=email)
    user.set_password(secrets.token_urlsafe(18))
    db.session.add(user)
    try:
        db.session.commit()
        return int(user.id)
    except IntegrityError:
        db.session.rollback()
        existing = User.query.filter_by(email=email).first()
        if existing:
            return int(existing.id)
        raise


def _table_exists(name: str) -> bool:
    try:
        return bool(inspect(db.engine).has_table(name))
    except Exception:
        return False


def _clear_preview_user_data(user_pk: int) -> None:
    # 거래 참조 데이터 우선 삭제
    EvidenceItem.query.filter(EvidenceItem.user_pk == user_pk).delete(synchronize_session=False)
    ExpenseLabel.query.filter(ExpenseLabel.user_pk == user_pk).delete(synchronize_session=False)
    IncomeLabel.query.filter(IncomeLabel.user_pk == user_pk).delete(synchronize_session=False)

    # 영수증/액션/후보
    if _table_exists("receipt_items"):
        ReceiptItem.query.filter(ReceiptItem.user_pk == user_pk).delete(synchronize_session=False)
    if _table_exists("receipt_batches"):
        ReceiptBatch.query.filter(ReceiptBatch.user_pk == user_pk).delete(synchronize_session=False)
    if _table_exists("action_logs"):
        ActionLog.query.filter(ActionLog.user_pk == user_pk).delete(synchronize_session=False)
    if _table_exists("recurring_candidates"):
        RecurringCandidate.query.filter(RecurringCandidate.user_pk == user_pk).delete(synchronize_session=False)

    # 사용자 단위 부가 데이터
    CounterpartyRule.query.filter(CounterpartyRule.user_pk == user_pk).delete(synchronize_session=False)
    CounterpartyExpenseRule.query.filter(CounterpartyExpenseRule.user_pk == user_pk).delete(synchronize_session=False)
    RecurringRule.query.filter(RecurringRule.user_pk == user_pk).delete(synchronize_session=False)
    WeeklyTask.query.filter(WeeklyTask.user_pk == user_pk).delete(synchronize_session=False)
    DashboardSnapshot.query.filter(DashboardSnapshot.user_pk == user_pk).delete(synchronize_session=False)
    DashboardEntry.query.filter(DashboardEntry.user_pk == user_pk).delete(synchronize_session=False)
    HoldDecision.query.filter(HoldDecision.user_pk == user_pk).delete(synchronize_session=False)
    UserDashboardState.query.filter(UserDashboardState.user_pk == user_pk).delete(synchronize_session=False)
    BankAccountLink.query.filter(BankAccountLink.user_pk == user_pk).delete(synchronize_session=False)
    TaxBufferLedger.query.filter(TaxBufferLedger.user_pk == user_pk).delete(synchronize_session=False)
    CsvFormatMapping.query.filter(CsvFormatMapping.user_pk == user_pk).delete(synchronize_session=False)
    TaxProfile.query.filter(TaxProfile.user_pk == user_pk).delete(synchronize_session=False)
    SafeToSpendSettings.query.filter(SafeToSpendSettings.user_pk == user_pk).delete(synchronize_session=False)

    # 마지막에 거래/가져오기
    Transaction.query.filter(Transaction.user_pk == user_pk).delete(synchronize_session=False)
    ImportJob.query.filter(ImportJob.user_pk == user_pk).delete(synchronize_session=False)
    db.session.commit()


def _purge_stale_preview_users(*, keep_user_pk: int, max_age_hours: int = 48, limit: int = 30) -> None:
    cutoff = utcnow() - timedelta(hours=max(1, int(max_age_hours)))
    stale_users = (
        User.query.filter(User.email.like("preview+%@safetospend.local"))
        .filter(User.created_at < cutoff)
        .order_by(User.created_at.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    if not stale_users:
        return

    for u in stale_users:
        uid = int(u.id)
        if uid == int(keep_user_pk):
            continue
        try:
            _clear_preview_user_data(uid)
            User.query.filter(User.id == uid).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()


def _resolve_preview_month_key(user_pk: int) -> str:
    now_key = _month_key_now_kst()
    latest_dt = (
        db.session.query(func.max(Transaction.occurred_at))
        .filter(Transaction.user_pk == user_pk)
        .scalar()
    )
    if not latest_dt:
        return now_key
    try:
        latest_key = latest_dt.strftime("%Y-%m")
    except Exception:
        return now_key
    # 기본은 현재 달, 단 업로드 데이터가 다른 달만 있으면 최신 달을 사용.
    return latest_key if latest_key != now_key else now_key


def _build_preview_metrics_from_csv(*, user_pk: int, csv_path: str, filename: str) -> dict:
    headers, rows, delimiter = read_csv_preview(csv_path)
    _sig, mapping, _conf, _date_rate, _amt_rate, _src = detect_mapping_for_preview(
        user_pk=user_pk,
        headers=headers,
        rows=rows,
        delimiter=delimiter,
    )

    has_date = bool((mapping.get("date") or "").strip())
    has_amount = bool((mapping.get("amount") or "").strip() or (mapping.get("in_amount") or "").strip() or (mapping.get("out_amount") or "").strip())
    if (not has_date) or (not has_amount):
        raise CsvImportError("파일 형식을 자동으로 인식하지 못했어요. CSV 헤더를 확인해 주세요.")

    import_result = import_csv_to_db(
        user_pk=user_pk,
        filepath=csv_path,
        filename=(filename or "preview.csv"),
        mapping=mapping,
    )
    month_key = _resolve_preview_month_key(user_pk)

    overview = compute_overview(user_pk=user_pk, month_key=month_key)
    est = compute_tax_estimate(user_pk=user_pk, month_key=month_key)

    payload = dict(overview)
    warnings = list(est.warnings or ())
    if int(import_result.inserted_rows or 0) <= 0:
        if int(import_result.duplicate_rows or 0) > 0:
            warnings.append("새로 반영된 거래가 없어요. 중복 거래로 인식된 항목이 있어요.")
        else:
            warnings.append("새로 반영된 거래가 없어요. 파일 형식/내용을 확인해 주세요.")
    if int(import_result.failed_rows or 0) > 0:
        warnings.append("일부 행은 읽지 못했어요. 날짜/금액 형식을 확인해 주세요.")

    payload.update(
        {
            "import_total_rows": int(import_result.total_rows or 0),
            "import_inserted_rows": int(import_result.inserted_rows or 0),
            "import_duplicate_rows": int(import_result.duplicate_rows or 0),
            "import_failed_rows": int(import_result.failed_rows or 0),
            "income_sum_krw": int(est.income_sum_krw or 0),
            "expense_sum_krw": int(est.expense_sum_krw or 0),
            "net_est_krw": int(est.net_est_krw or 0),
            "tax_est_before_withheld_krw": int(est.tax_est_before_withheld_krw or 0),
            "local_tax_est_krw": int(est.local_tax_est_krw or 0),
            "withheld_est_krw": int(est.withheld_est_krw or 0),
            "withholding_base_krw": int(est.withholding_base_krw or 0),
            "withholding_mode": str(est.withholding_mode or "not_applied"),
            "tax_due_est_krw": int(est.tax_due_est_krw or 0),
            "warnings": warnings,
        }
    )

    if current_app.debug or (os.getenv("FLASK_ENV") == "development"):
        current_app.logger.info(
            "[preview-debug] month_key=%s income_sum=%s expense_sum=%s withheld_est=%s nhis_buffer=%s tax_due_est=%s required_missing_count=%s",
            payload.get("month_key"),
            payload.get("income_sum_krw", 0),
            payload.get("expense_sum_krw", 0),
            payload.get("withheld_est_krw", 0),
            payload.get("health_insurance_buffer", 0),
            payload.get("tax_due_est_krw", 0),
            payload.get("required_missing_count", 0),
        )
    return payload


def _empty_preview_payload(month_key: str) -> dict:
    return {
        "month_key": month_key,
        "total_setaside_recommended": 0,
        "tax_setaside_recommended": 0,
        "health_insurance_buffer": 0,
        "required_missing_count": 0,
        "review_needed_count": 0,
        "package_badge": "확인 필요",
        "package_hint": "파일을 진단하면 이번 달 상태를 볼 수 있어요.",
        "income_sum_krw": 0,
        "expense_sum_krw": 0,
        "withheld_est_krw": 0,
        "withholding_base_krw": 0,
        "withholding_mode": "not_applied",
        "local_tax_est_krw": 0,
        "tax_due_est_krw": 0,
        "warnings": [],
    }

@web_main_bp.route("/", methods=["GET"])
def landing():
    return render_template("landing.html")


@web_main_bp.route("/pricing", methods=["GET"])
def pricing():
    pricing_plan_code = "free"
    pricing_plan_status = "active"
    user_id = int(session.get("user_id") or 0)
    if user_id > 0:
        ent = get_user_entitlements(user_id)
        pricing_plan_code = str(ent.plan_code or "free")
        pricing_plan_status = str(ent.plan_status or "active")
    return render_template(
        "pricing.html",
        pricing_plan_code=pricing_plan_code,
        pricing_plan_status=pricing_plan_status,
    )


@web_main_bp.route("/preview", methods=["GET", "POST"])
def preview():
    root = Path(__file__).resolve().parents[2]
    sample_csv = root / "sample_data" / "sample_bank.csv"
    preview_source = "sample"
    upload_filename = None
    preview_user_pk = _ensure_preview_user()
    _purge_stale_preview_users(keep_user_pk=preview_user_pk)
    preview_data: dict = {}
    sample_error = None

    if request.method == "POST":
        uploaded = request.files.get("csv")
        tmp_path = None
        try:
            _token, tmp_path, upload_filename = save_temp_upload(uploaded, user_pk=preview_user_pk)
            _clear_preview_user_data(preview_user_pk)
            preview_data = _build_preview_metrics_from_csv(
                user_pk=preview_user_pk,
                csv_path=str(tmp_path),
                filename=(upload_filename or "upload.csv"),
            )
            preview_source = "upload"
        except CsvImportError as e:
            flash(normalize_csv_import_error(str(e)), "error")
        except Exception:
            current_app.logger.exception("[preview] 파일 진단 실패")
            flash("파일 진단에 실패했습니다. 다시 시도해 주세요.", "error")
        finally:
            if tmp_path:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    if not preview_data:
        try:
            _clear_preview_user_data(preview_user_pk)
            preview_data = _build_preview_metrics_from_csv(
                user_pk=preview_user_pk,
                csv_path=str(sample_csv),
                filename="sample_bank.csv",
            )
        except Exception:
            sample_error = "샘플 진단을 불러오지 못했어요."
            preview_data = _empty_preview_payload(_month_key_now_kst())

    if sample_error and request.method == "GET":
        flash(sample_error, "error")

    return render_template(
        "landing_preview.html",
        preview=preview_data,
        preview_source=preview_source,
        upload_filename=upload_filename,
        import_url=f"{url_for('web_inbox.import_page')}?from=landing",
        login_url=url_for("web_auth.login", next=url_for("web_inbox.import_page")),
        register_url=url_for("web_auth.register", next=url_for("web_inbox.import_page")),
    )


@web_main_bp.route("/main", methods=["GET", "POST"])
def main():
    # 레거시 템플릿/링크 호환: 실제 메인 동선은 dashboard로 통일
    return redirect(url_for("web_dashboard.index"))

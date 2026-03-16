# app.py
import json
import logging
import os, click, sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, flash, g, jsonify, redirect, request, session, url_for
from flask_migrate import Migrate
from werkzeug.exceptions import RequestEntityTooLarge

from core.extensions import db
from core.admin_guard import is_admin_user
from core.log_sanitize import SensitiveLogFilter
from core.runtime_secret_guard import (
    is_insecure_secret_key,
    resolve_runtime_bind_host,
    validate_runtime_secret_key,
)
from core.security import (
    get_or_create_csrf_token,
    is_valid_csrf_token,
    safe_referrer_or_fallback,
    wants_json_response,
)
from core.time import utcnow
from services.api_tokens import verify_access_token
from services.billing.startup_checks import BillingStartupCheckError, run_billing_startup_checks
from services.evidence_vault import purge_expired_evidence

load_dotenv()

if __name__ == "__main__":
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("FLASK_RUN_HOST", "127.0.0.1")

BASE_DIR = Path(__file__).resolve().parent
migrate = Migrate()


def create_app():
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        template_folder=str(BASE_DIR / "templates"),
    )

    # ✅ env 우선순위: SQLALCHEMY_DATABASE_URI -> DATABASE_URL
    db_uri = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if not db_uri:
        raise RuntimeError("SQLALCHEMY_DATABASE_URI 또는 DATABASE_URL 환경변수가 없습니다.")

    app_env = str(os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["APP_ENV"] = app_env
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
    bind_host = resolve_runtime_bind_host(os.environ)
    app.config["RUNTIME_BIND_HOST"] = bind_host
    validate_runtime_secret_key(
        secret=app.config["SECRET_KEY"],
        app_env=app_env,
        bind_host=bind_host,
        environ=os.environ,
        argv=sys.argv,
    )
    if is_insecure_secret_key(app.config["SECRET_KEY"]):
        app.logger.warning(
            "[보안주의] SECRET_KEY 기본값을 사용 중입니다. localhost 전용 개발 환경에서만 허용됩니다."
        )

    # 로컬에서 쿠키가 거부되어 로그인 안되는 상황 방지용 기본값
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    secure_override = os.getenv("SESSION_COOKIE_SECURE")
    secure_default_envs = {"production", "prod", "staging", "stage"}
    if secure_override is None:
        app.config["SESSION_COOKIE_SECURE"] = app_env in secure_default_envs
    else:
        app.config["SESSION_COOKIE_SECURE"] = secure_override.strip().lower() in {"1", "true", "yes", "on"}
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        seconds=int(os.getenv("PERMANENT_SESSION_LIFETIME_SECONDS") or (60 * 60 * 12))
    )
    app.config["TRUST_PROXY_X_FORWARDED_FOR"] = (
        str(os.getenv("TRUST_PROXY_X_FORWARDED_FOR") or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    app.config["RATE_LIMIT_REDIS_URL"] = str(os.getenv("RATE_LIMIT_REDIS_URL") or "").strip()
    
    app.config["EVIDENCE_UPLOAD_DIR"] = os.getenv("EVIDENCE_UPLOAD_DIR") or str(BASE_DIR / "uploads" / "evidence")
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES") or (20 * 1024 * 1024))
    max_request_bytes = int(os.getenv("MAX_REQUEST_BYTES") or (100 * 1024 * 1024))
    app.config["MAX_UPLOAD_BYTES"] = max_upload_bytes
    app.config["MAX_CONTENT_LENGTH"] = max_request_bytes
    app.config["RECEIPT_BATCH_DELAY_SECONDS"] = int(os.getenv("RECEIPT_BATCH_DELAY_SECONDS") or 45)
    app.config["RECEIPT_BATCH_WORKER_CHECK_SECONDS"] = int(os.getenv("RECEIPT_BATCH_WORKER_CHECK_SECONDS") or 30)
    app.config["RECEIPT_BATCH_STALE_MINUTES"] = int(os.getenv("RECEIPT_BATCH_STALE_MINUTES") or 5)
    app.config["TOSS_PAYMENTS_CLIENT_KEY"] = str(os.getenv("TOSS_PAYMENTS_CLIENT_KEY") or "").strip()
    app.config["TOSS_PAYMENTS_SECRET_KEY"] = str(os.getenv("TOSS_PAYMENTS_SECRET_KEY") or "").strip()
    app.config["BILLING_KEY_ENCRYPTION_SECRET"] = str(os.getenv("BILLING_KEY_ENCRYPTION_SECRET") or "").strip()
    app.config["BILLING_GUARD_MODE"] = str(os.getenv("BILLING_GUARD_MODE") or "").strip()

    def _install_sensitive_log_filters() -> None:
        filter_type = SensitiveLogFilter
        for logger_name in {app.logger.name, "werkzeug"}:
            logger = logging.getLogger(logger_name)
            if any(isinstance(f, filter_type) for f in logger.filters):
                continue
            logger.addFilter(SensitiveLogFilter())

    _install_sensitive_log_filters()
    
    @app.cli.command("purge-evidence")
    def purge_evidence_cmd():
        n = purge_expired_evidence()
        click.echo(f"purged: {n}")

    @app.cli.command("refresh-nhis-rates")
    def refresh_nhis_rates_cmd():
        try:
            from services.nhis_rates import refresh_nhis_rates

            snap = refresh_nhis_rates(timeout=10)
            click.echo(
                "updated nhis rates: "
                f"year={int(snap.effective_year)} "
                f"health_rate={float(snap.health_insurance_rate or 0):.4f} "
                f"ltc_ratio={float(snap.long_term_care_ratio_of_health or 0):.4f}"
            )
        except Exception as e:
            click.echo(f"failed to refresh nhis rates: {type(e).__name__}", err=True)

    @app.cli.command("refresh-official-snapshots")
    @click.option("--timeout", default=10, type=int, help="refresh timeout(초)")
    @click.option("--verify-timeout", default=12, type=int, help="verify timeout(초)")
    @click.option("--verify-offline", is_flag=True, help="공식 검증을 오프라인 스냅샷만으로 실행")
    def refresh_official_snapshots_cmd(timeout: int, verify_timeout: int, verify_offline: bool):
        try:
            from scripts.refresh_official_snapshots import run_refresh

            code = run_refresh(
                timeout=max(3, int(timeout)),
                verify_timeout=max(4, int(verify_timeout)),
                verify_offline=bool(verify_offline),
            )
            if int(code) != 0:
                raise SystemExit(int(code))
        except SystemExit:
            raise
        except Exception as e:
            click.echo(f"failed to refresh official snapshots: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("refresh-popbill-bank-guides")
    @click.option("--timeout", default=12, type=int, help="HTTP timeout(초)")
    def refresh_popbill_bank_guides_cmd(timeout: int):
        try:
            from scripts.refresh_popbill_bank_guides import run_refresh

            code = run_refresh(timeout=max(3, int(timeout)))
            if int(code) != 0:
                raise SystemExit(int(code))
        except SystemExit:
            raise
        except Exception as e:
            click.echo(f"failed to refresh popbill bank guides: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("bank-sync-run-due")
    @click.option("--dry-run", is_flag=True, help="실제 동기화 없이 due 대상만 점검")
    @click.option("--limit", default=0, type=int, help="최대 처리 계좌 수(0=제한 없음)")
    @click.option("--account-id", default=0, type=int, help="특정 link id만 실행")
    @click.option("--user-pk", default=0, type=int, help="특정 사용자만 실행")
    def bank_sync_run_due_cmd(dry_run: bool, limit: int, account_id: int, user_pk: int):
        from services.bank_sync_scheduler import run_due_bank_sync_batch

        try:
            result = run_due_bank_sync_batch(
                dry_run=bool(dry_run),
                limit=(int(limit) if int(limit or 0) > 0 else None),
                account_id=(int(account_id) if int(account_id or 0) > 0 else None),
                user_pk=(int(user_pk) if int(user_pk or 0) > 0 else None),
            )
            summary = {
                "mode": result.mode,
                "dry_run": bool(result.dry_run),
                "total_links": int(result.total_links),
                "due_links": int(result.due_links),
                "processed_links": int(result.processed_links),
                "success_count": int(result.success_count),
                "failed_count": int(result.failed_count),
                "skipped_interval_count": int(result.skipped_interval_count),
                "skipped_plan_count": int(result.skipped_plan_count),
                "skipped_lock_count": int(result.skipped_lock_count),
                "skipped_limit_count": int(result.skipped_limit_count),
                "inserted_rows_total": int(result.inserted_rows_total),
                "duplicate_rows_total": int(result.duplicate_rows_total),
                "failed_rows_total": int(result.failed_rows_total),
            }
            click.echo(json.dumps(summary, ensure_ascii=False))
            if result.errors:
                click.echo(
                    json.dumps({"errors": result.errors[:20]}, ensure_ascii=False),
                    err=True,
                )
            if (not bool(dry_run)) and int(result.failed_count) > 0:
                raise SystemExit(2)
        except SystemExit:
            raise
        except Exception as e:
            click.echo(f"bank sync due run failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-startup-check")
    def billing_startup_check_cmd():
        try:
            with app.app_context():
                report = run_billing_startup_checks(app)
            if report.ok:
                click.echo(f"billing startup check ok (mode={report.mode})")
                return
            click.echo(f"billing startup check warning (mode={report.mode})", err=True)
            for msg in report.errors:
                click.echo(f"- {msg}", err=True)
        except BillingStartupCheckError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(f"billing startup check failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("set-admin-role")
    @click.option("--email", required=True, help="권한을 변경할 사용자 이메일")
    @click.option("--grant/--revoke", default=True, help="관리자 권한 부여/회수")
    def set_admin_role_cmd(email: str, grant: bool):
        from services.auth import set_user_admin_role

        ok, msg, user_pk = set_user_admin_role(email=email, is_admin=bool(grant))
        if not ok or not user_pk:
            click.echo(msg, err=True)
            raise SystemExit(2)
        state = "granted" if grant else "revoked"
        click.echo(json.dumps({"ok": True, "user_pk": int(user_pk), "email": str(email).strip().lower(), "state": state}, ensure_ascii=False))

    @app.cli.command("cleanup-billing-registration-attempts")
    @click.option("--abandoned-hours", default=2, type=int, help="미완료 started 상태를 abandoned 처리할 시간(시간)")
    @click.option("--retention-days", default=90, type=int, help="failed/canceled 보관 기간(일)")
    @click.option("--dry-run", is_flag=True, help="실제 삭제 없이 대상 건수만 출력")
    def cleanup_billing_registration_attempts_cmd(abandoned_hours: int, retention_days: int, dry_run: bool):
        try:
            from services.billing.service import (
                cleanup_registration_attempts,
                normalize_registration_attempts_abandoned,
            )

            normalized = normalize_registration_attempts_abandoned(
                abandoned_after_hours=max(1, int(abandoned_hours or 2))
            )
            result = cleanup_registration_attempts(
                retention_days=max(1, int(retention_days or 90)),
                dry_run=bool(dry_run),
            )
            click.echo(
                "cleanup completed "
                f"(normalized_abandoned={int(normalized)}, "
                f"purged={int(result.get('purged_count') or 0)}, "
                f"dry_run={bool(dry_run)})"
            )
        except Exception as e:
            click.echo(f"cleanup failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-reconcile")
    @click.option("--order-id", default="", help="결제 orderId")
    @click.option("--payment-key", default="", help="결제 paymentKey")
    @click.option("--dry-run", is_flag=True, help="DB 반영 없이 검증만 수행")
    def billing_reconcile_cmd(order_id: str, payment_key: str, dry_run: bool):
        from services.billing.reconcile import (
            BillingReconcileError,
            BillingReconcileNotFound,
            reconcile_by_order_id,
            reconcile_by_payment_key,
        )

        oid = str(order_id or "").strip()
        pkey = str(payment_key or "").strip()
        if bool(oid) == bool(pkey):
            click.echo("--order-id 또는 --payment-key 중 하나만 지정해 주세요.", err=True)
            raise SystemExit(1)
        try:
            if oid:
                result = reconcile_by_order_id(
                    order_id=oid,
                    apply_projection=not bool(dry_run),
                    commit=not bool(dry_run),
                )
            else:
                result = reconcile_by_payment_key(
                    payment_key=pkey,
                    apply_projection=not bool(dry_run),
                    commit=not bool(dry_run),
                )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except BillingReconcileNotFound as e:
            click.echo(str(e), err=True)
            raise SystemExit(2)
        except BillingReconcileError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(f"billing reconcile failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-replay-event")
    @click.option("--event-id", default=0, type=int, help="payment_event id")
    @click.option("--transmission-id", default="", help="webhook transmission id")
    @click.option("--dry-run", is_flag=True, help="DB 반영 없이 검증만 수행")
    def billing_replay_event_cmd(event_id: int, transmission_id: str, dry_run: bool):
        from services.billing.reconcile import BillingReconcileNotFound, reconcile_from_payment_event

        eid = int(event_id or 0)
        tx = str(transmission_id or "").strip()
        if (eid <= 0) and (not tx):
            click.echo("--event-id 또는 --transmission-id 중 하나를 지정해 주세요.", err=True)
            raise SystemExit(1)
        try:
            result = reconcile_from_payment_event(
                payment_event_id=eid if eid > 0 else None,
                transmission_id=tx or None,
                apply_projection=not bool(dry_run),
                commit=not bool(dry_run),
            )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except BillingReconcileNotFound as e:
            click.echo(str(e), err=True)
            raise SystemExit(2)
        except Exception as e:
            click.echo(f"billing replay failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-reproject-entitlement")
    @click.option("--user-pk", required=True, type=int, help="대상 사용자 ID")
    @click.option("--source-id", default="", help="멱등 소스 식별자(옵션)")
    @click.option("--dry-run", is_flag=True, help="DB 반영 없이 재투영 결과 미리보기")
    def billing_reproject_entitlement_cmd(user_pk: int, source_id: str, dry_run: bool):
        from services.billing.projector import BillingProjectorError, reproject_entitlement_for_user

        try:
            result = reproject_entitlement_for_user(
                user_pk=int(user_pk),
                source_id=(str(source_id or "").strip() or None),
                source_type="ops_reproject",
                reason="운영자 수동 재투영",
                commit=not bool(dry_run),
            )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except BillingProjectorError as e:
            click.echo(str(e), err=True)
            raise SystemExit(2)
        except Exception as e:
            click.echo(f"billing reproject failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-run-recurring")
    @click.option("--dry-run", is_flag=True, help="실제 결제 요청 없이 대상만 점검")
    @click.option("--subscription-id", default=0, type=int, help="특정 구독만 실행")
    @click.option("--limit", default=100, type=int, help="최대 처리 구독 수")
    @click.option("--exclude-retry", is_flag=True, help="grace 재시도 대상을 제외")
    def billing_run_recurring_cmd(dry_run: bool, subscription_id: int, limit: int, exclude_retry: bool):
        from services.billing.recurring import run_recurring_batch

        try:
            result = run_recurring_batch(
                dry_run=bool(dry_run),
                subscription_id=(int(subscription_id) if int(subscription_id or 0) > 0 else None),
                limit=max(1, int(limit or 100)),
                include_retry=not bool(exclude_retry),
            )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            click.echo(f"billing recurring run failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-run-retry")
    @click.option("--dry-run", is_flag=True, help="실제 결제 요청 없이 대상만 점검")
    @click.option("--subscription-id", default=0, type=int, help="특정 구독만 실행")
    @click.option("--limit", default=100, type=int, help="최대 처리 구독 수")
    def billing_run_retry_cmd(dry_run: bool, subscription_id: int, limit: int):
        from services.billing.recurring import run_retry_batch

        try:
            result = run_retry_batch(
                dry_run=bool(dry_run),
                subscription_id=(int(subscription_id) if int(subscription_id or 0) > 0 else None),
                limit=max(1, int(limit or 100)),
            )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            click.echo(f"billing retry run failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-run-grace-expiry")
    @click.option("--dry-run", is_flag=True, help="실제 상태 전이 없이 대상만 점검")
    @click.option("--subscription-id", default=0, type=int, help="특정 구독만 실행")
    @click.option("--limit", default=100, type=int, help="최대 처리 구독 수")
    def billing_run_grace_expiry_cmd(dry_run: bool, subscription_id: int, limit: int):
        from services.billing.recurring import run_grace_expiry

        try:
            result = run_grace_expiry(
                dry_run=bool(dry_run),
                subscription_id=(int(subscription_id) if int(subscription_id or 0) > 0 else None),
                limit=max(1, int(limit or 100)),
            )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            click.echo(f"billing grace expiry run failed: {type(e).__name__}", err=True)
            raise SystemExit(1)

    @app.cli.command("billing-run-cancel-effective")
    @click.option("--dry-run", is_flag=True, help="실제 해지 반영 없이 대상만 점검")
    @click.option("--subscription-id", default=0, type=int, help="특정 구독만 실행")
    @click.option("--limit", default=100, type=int, help="최대 처리 구독 수")
    def billing_run_cancel_effective_cmd(dry_run: bool, subscription_id: int, limit: int):
        from services.billing.recurring import run_cancel_effective

        try:
            result = run_cancel_effective(
                dry_run=bool(dry_run),
                subscription_id=(int(subscription_id) if int(subscription_id or 0) > 0 else None),
                limit=max(1, int(limit or 100)),
            )
            if dry_run:
                db.session.rollback()
            click.echo(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            click.echo(f"billing cancel effective run failed: {type(e).__name__}", err=True)
            raise SystemExit(1)


    db.init_app(app)
    migrate.init_app(app, db)

    # ✅ 모델 로딩(마이그레이션/ORM용)
    import domain.models  # noqa: F401

    # ✅ 핵심: flask db 명령은 "웹 라우트"가 필요 없는데,
    # 라우트 import가 깨져 있으면 db 명령까지 같이 죽어버림.
    # 그래서 db 관련 명령일 땐 블루프린트를 등록하지 않음.
    if "db" not in sys.argv:
        from routes import register_blueprints
        register_blueprints(app)
        if "billing-startup-check" not in sys.argv:
            try:
                with app.app_context():
                    run_billing_startup_checks(app)
            except BillingStartupCheckError:
                raise
            except Exception as e:
                # startup check 자체 예외가 앱 시작을 500으로 바꾸지 않도록 명시적으로 남긴다.
                app.logger.error("[BILLING_STARTUP_CHECK] 실행 실패: %s", type(e).__name__)
        try:
            from services.bank_sync_scheduler import start_local_bank_sync_scheduler

            start_local_bank_sync_scheduler(app)
        except Exception:
            app.logger.exception("[BANK_AUTOSYNC] local scheduler bootstrap failed")

    @app.before_request
    def enforce_api_bearer_auth():
        if not request.path.startswith("/api/"):
            return None
        if request.method == "OPTIONS":
            return None
        if request.path in {"/api/auth/token", "/api/auth/refresh", "/api/billing/webhook"}:
            return None

        authz = str(request.headers.get("Authorization") or "").strip()
        if not authz.lower().startswith("bearer "):
            return jsonify({"ok": False, "message": "인증 토큰이 필요해요."}), 401
        token = authz[7:].strip()
        ok, user_pk, msg = verify_access_token(token)
        if not ok or not user_pk:
            return jsonify({"ok": False, "message": msg or "토큰 인증에 실패했어요."}), 401
        g.api_user_pk = int(user_pk)
        return None

    @app.before_request
    def enforce_web_csrf():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if request.path.startswith("/api/"):
            return None
        csrf = (
            request.form.get("csrf_token")
            or request.headers.get("X-CSRF-Token")
            or request.headers.get("X-CSRFToken")
        )
        if is_valid_csrf_token(csrf):
            return None

        msg = "보안을 위해 다시 시도해 주세요."
        if wants_json_response(req=request):
            return jsonify({"ok": False, "message": msg}), 400
        flash(msg, "error")
        return redirect(safe_referrer_or_fallback(req=request, fallback=url_for("web_main.landing")))

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": get_or_create_csrf_token()}

    @app.context_processor
    def inject_receipt_queue_nav():
        uid = session.get("user_id")
        if not uid:
            return {"nav_receipt_queue": None, "nav_is_admin": False, "nav_user_nickname": None}
        is_admin = False
        nav_user_nickname = None
        try:
            from domain.models import User

            user_row = User.query.filter_by(id=int(uid)).first()
            user_email = str((user_row.email if user_row else "") or "").strip().lower()
            is_admin = bool(is_admin_user(user_row))
            if user_email and "@" in user_email:
                nav_user_nickname = user_email.split("@", 1)[0].strip() or None
            elif user_email:
                nav_user_nickname = user_email
        except Exception:
            is_admin = False
            nav_user_nickname = None
        try:
            from services.receipt_batch import get_user_processing_summary

            summary = get_user_processing_summary(int(uid))
            count = int(summary.get("in_progress_count") or 0)
            batch_id = int(summary.get("batch_id") or 0)
            month_key = str(summary.get("month_key") or "").strip() or utcnow().strftime("%Y-%m")
            if count <= 0 or batch_id <= 0:
                return {"nav_receipt_queue": None, "nav_is_admin": is_admin, "nav_user_nickname": nav_user_nickname}

            page_kwargs = {"batch_id": batch_id, "month": month_key, "focus": "receipt_required", "q": "", "limit": 30}
            status_kwargs = {"batch_id": batch_id, "month": month_key, "focus": "receipt_required", "q": "", "limit": 30}

            nav = {
                "count": count,
                "url": url_for("web_calendar.receipt_new_upload_page", **page_kwargs),
                "status_url": url_for("web_calendar.receipt_batch_status", **status_kwargs),
            }
            return {"nav_receipt_queue": nav, "nav_is_admin": is_admin, "nav_user_nickname": nav_user_nickname}
        except Exception:
            return {"nav_receipt_queue": None, "nav_is_admin": is_admin, "nav_user_nickname": nav_user_nickname}

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(_error):
        msg = "업로드 용량이 너무 커요. 파일 개수를 줄이거나 더 작은 파일로 다시 시도해 주세요."
        wants_json = wants_json_response(req=request) or request.path.endswith("/batch")
        if wants_json:
            return jsonify({"ok": False, "message": msg}), 413
        flash(msg, "error")
        back = (request.referrer or "").strip()
        if back:
            try:
                parsed = urlparse(back)
                if (not parsed.netloc) or (parsed.netloc == request.host):
                    path = parsed.path or "/"
                    if path.startswith("/"):
                        safe_back = path + (f"?{parsed.query}" if parsed.query else "")
                        return redirect(safe_back)
            except Exception:
                pass
        return redirect(url_for("web_inbox.import_page"))

    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        endpoint = str(request.endpoint or "").strip()
        is_billing_page = endpoint.startswith("web_billing.")
        script_src = "script-src 'self' 'unsafe-inline'"
        connect_src = "connect-src 'self'"
        frame_src = None
        if is_billing_page:
            script_src = f"{script_src} https://js.tosspayments.com"
            connect_src = f"{connect_src} https://api.tosspayments.com https://*.tosspayments.com"
            frame_src = "frame-src 'self' https://js.tosspayments.com https://*.tosspayments.com"
        csp = "; ".join(
            [
                "default-src 'self'",
                "base-uri 'self'",
                "frame-ancestors 'none'",
                "form-action 'self'",
                "object-src 'none'",
                "img-src 'self' data:",
                "font-src 'self' data:",
                # 템플릿 내 inline script가 많아 단계적으로 nonce 전환 전까지 허용한다.
                script_src,
                "style-src 'self' 'unsafe-inline'",
                connect_src,
                frame_src or "",
            ]
        )
        csp = "; ".join([part for part in csp.split("; ") if part])
        response.headers.setdefault("Content-Security-Policy", csp)
        if app.config.get("SESSION_COOKIE_SECURE") and not app.debug:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    return app


app = create_app()

@app.route("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    app.run(host=os.getenv("FLASK_RUN_HOST", "127.0.0.1"), debug=True)

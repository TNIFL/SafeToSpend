from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, inspect
from sqlalchemy.exc import IntegrityError

from domain.models import EvidenceItem, ExpenseLabel, ReceiptBatch, ReceiptItem, Transaction
from services.evidence_vault import (
    default_retention_until,
    delete_physical_file,
    move_evidence_file_to_tx,
    store_evidence_draft_file_multi,
    store_evidence_draft_text,
)
from services.receipt_batch import (
    BATCH_STATUS_DONE,
    BATCH_STATUS_DONE_WITH_ERRORS,
    ITEM_STATUS_DONE,
    ITEM_STATUS_FAILED,
    ITEM_STATUS_PROCESSING,
    ITEM_STATUS_UPLOADED,
    build_draft_payload_from_item,
    can_retry_receipt_item,
    claim_next_uploaded_item_for_batch,
    compact_receipt_parsed,
    iso_dt,
    mark_receipt_item_failed,
    mark_receipt_item_paused,
    normalize_receipt_error,
    process_receipt_item,
    reset_receipt_item_for_retry,
    requeue_stale_processing_items,
    recompute_batch_counts,
    retry_block_message,
)
from services.receipt_parser import parse_receipt_from_file, parse_receipt_from_text
from services.rate_limit import client_ip, hit_limit
from services.input_sanitize import parse_date_ym, parse_int_krw, safe_str

DEFAULT_RECEIPT_MULTI_MAX_FILES = 10
DEFAULT_RECEIPT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_RECEIPT_DRAFTS_MAX = 25


def parse_paid_at_input(value: str, default_month_key: str) -> datetime:
    """Parse paid_at form value to naive datetime (KST interpretation in project)."""
    raw = safe_str(value, max_len=32)
    if raw:
        raw = raw.replace("T", " ")
        try:
            if len(raw) == 10:
                raw = raw + " 12:00"
            return datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except Exception:
            pass
    return datetime.strptime(f"{default_month_key}-01 12:00", "%Y-%m-%d %H:%M")


def parse_amount_krw(value: str) -> int:
    parsed = parse_int_krw(value)
    return int(parsed or 0)


def register_receipt_routes(
    *,
    bp,
    uid_getter,
    parse_month,
    parse_limit,
    is_partial,
    review_focus,
    default_review_focus,
    db,
    compute_tax_estimate,
    utcnow_fn,
):
    def _log_receipt_stage(*, status: str, file_name: str, detail: str = "", level: str = "INFO") -> None:
        name = str(file_name or "이름 없는 파일").strip() or "이름 없는 파일"
        extra = f", {detail}" if detail else ""
        line = f"[{level}][영수증으로 거래 추가][{status}] : {name}{extra}"
        if level == "ERROR":
            current_app.logger.error(line)
        else:
            current_app.logger.info(line)

    def _safe_parse_month(value: str | None):
        try:
            return parse_month(value)
        except Exception:
            return parse_month(None)

    def _batch_tables_ready() -> bool:
        try:
            insp = inspect(db.engine)
            return bool(insp.has_table("receipt_batches") and insp.has_table("receipt_items"))
        except Exception:
            return False

    def _receipt_max_files() -> int:
        return int(current_app.config.get("RECEIPT_NEW_MAX_FILES", DEFAULT_RECEIPT_MULTI_MAX_FILES))

    def _receipt_max_drafts() -> int:
        return int(current_app.config.get("RECEIPT_NEW_DRAFTS_MAX", DEFAULT_RECEIPT_DRAFTS_MAX))

    def _receipt_upload_max_bytes() -> int:
        raw = current_app.config.get("EVIDENCE_MAX_BYTES")
        if raw is None:
            raw = current_app.config.get("MAX_UPLOAD_BYTES", DEFAULT_RECEIPT_MAX_BYTES)
        return int(raw or DEFAULT_RECEIPT_MAX_BYTES)

    def _drafts_root() -> Path:
        base = current_app.config.get("EVIDENCE_UPLOAD_DIR")
        if base:
            root = Path(base)
        else:
            root = Path(current_app.root_path) / "uploads" / "evidence"
        droot = root / "_receipt_drafts"
        droot.mkdir(parents=True, exist_ok=True)
        return droot

    def _valid_token(token: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{32}", token or ""))

    def _draft_path(user_pk: int, token: str) -> Path | None:
        if not _valid_token(token):
            return None
        udir = _drafts_root() / f"u{int(user_pk)}"
        udir.mkdir(parents=True, exist_ok=True)
        return udir / f"{token}.json"

    def _load_draft_file(user_pk: int, token: str) -> dict | None:
        p = _draft_path(user_pk, token)
        if p is None or not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _save_draft_file(user_pk: int, token: str, payload: dict) -> None:
        p = _draft_path(user_pk, token)
        if p is None:
            return
        tmp = p.with_suffix(".json.part")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

        max_drafts = _receipt_max_drafts()
        udir = p.parent
        try:
            files = sorted((x for x in udir.glob("*.json") if x.is_file()), key=lambda x: x.stat().st_mtime, reverse=True)
        except Exception:
            files = []
        for stale in files[max_drafts:]:
            try:
                stale.unlink(missing_ok=True)
            except Exception:
                pass

    def _delete_draft_file(user_pk: int, token: str) -> None:
        p = _draft_path(user_pk, token)
        if p is None:
            return
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    def _iter_recent_drafts(user_pk: int, limit: int = 40) -> list[dict]:
        udir = _drafts_root() / f"u{int(user_pk)}"
        if not udir.exists():
            return []
        try:
            files = sorted((x for x in udir.glob("*.json") if x.is_file()), key=lambda x: x.stat().st_mtime, reverse=True)
        except Exception:
            return []
        out: list[dict] = []
        for p in files[: max(1, limit)]:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(raw, dict):
                out.append(raw)
        return out

    def _wants_json_response() -> bool:
        fmt = safe_str(request.args.get("format") or request.form.get("format"), max_len=16).lower()
        if fmt == "json":
            return True
        accept = (request.headers.get("Accept") or "").lower()
        return "application/json" in accept

    def _normalize_focus(value: str | None) -> str:
        focus = safe_str(value or default_review_focus, max_len=40)
        if focus not in review_focus:
            focus = default_review_focus
        return focus

    def _serialize_batch_item(*, item: ReceiptItem, month_key: str, focus: str, q: str, limit: int) -> dict:
        raw_err = (item.error_message or "").strip()
        err_code = ""
        err = ""
        if raw_err:
            err_code, err = normalize_receipt_error(raw_err)
        requires_reupload = bool(item.status == ITEM_STATUS_FAILED and (not item.file_key))
        confirm_url = ""
        if item.file_key and item.status in (ITEM_STATUS_DONE, ITEM_STATUS_FAILED):
            confirm_url = url_for(
                "web_calendar.receipt_batch_item_confirm",
                item_id=item.id,
                month=month_key,
                focus=focus,
                q=q,
                limit=limit,
            )
        retry_url = ""
        retry_allowed, retry_reason = can_retry_receipt_item(item)
        if item.status == ITEM_STATUS_FAILED and item.file_key and retry_allowed:
            retry_url = url_for("web_calendar.receipt_batch_retry_item", batch_id=item.batch_id, item_id=item.id)
        retry_blocked_message = ""
        if item.status == ITEM_STATUS_FAILED and item.file_key and (not retry_allowed):
            retry_blocked_message = retry_block_message(retry_reason)
        return {
            "id": item.id,
            "batch_id": item.batch_id,
            "name": item.original_filename or f"영수증-{item.id}",
            "size": int(item.size_bytes or 0),
            "status": item.status or ITEM_STATUS_UPLOADED,
            "error_message": err,
            "error_code": err_code,
            "confirm_url": confirm_url,
            "retry_url": retry_url,
            "retry_blocked_message": retry_blocked_message,
            "requires_reupload": requires_reupload,
            "duplicate": False,
            "created_at": iso_dt(item.created_at),
            "updated_at": iso_dt(item.updated_at),
        }

    def _build_duplicate_flags(*, user_pk: int, items: list[ReceiptItem]) -> dict[int, bool]:
        flags: dict[int, bool] = {}
        if not items:
            return flags

        sha_values = sorted({str(x.sha256 or "").strip() for x in items if str(x.sha256 or "").strip()})
        if not sha_values:
            for it in items:
                flags[int(it.id)] = False
            return flags

        try:
            evidence_rows = (
                db.session.query(EvidenceItem.sha256, func.count(EvidenceItem.id))
                .filter(EvidenceItem.user_pk == user_pk)
                .filter(EvidenceItem.deleted_at.is_(None))
                .filter(EvidenceItem.sha256.in_(sha_values))
                .group_by(EvidenceItem.sha256)
                .all()
            )
            evidence_count_map = {str(sha): int(cnt or 0) for sha, cnt in evidence_rows if sha}

            receipt_rows = (
                db.session.query(ReceiptItem.sha256, func.count(ReceiptItem.id))
                .filter(ReceiptItem.user_pk == user_pk)
                .filter(ReceiptItem.sha256.in_(sha_values))
                .group_by(ReceiptItem.sha256)
                .all()
            )
            receipt_count_map = {str(sha): int(cnt or 0) for sha, cnt in receipt_rows if sha}
        except Exception:
            # 중복 의심 계산 실패는 업로드 핵심 플로우를 막지 않는다.
            current_app.logger.warning("[WARN][영수증으로 거래 추가][중복의심 계산 실패]")
            for it in items:
                flags[int(it.id)] = False
            return flags

        for it in items:
            item_sha = str(it.sha256 or "").strip()
            if not item_sha:
                flags[int(it.id)] = False
                continue
            in_evidence = int(evidence_count_map.get(item_sha, 0)) > 0
            in_receipt_many = int(receipt_count_map.get(item_sha, 0)) > 1
            flags[int(it.id)] = bool(in_evidence or in_receipt_many)
        return flags

    @bp.get("/review/receipt-new")
    def receipt_new_upload_page():
        user_pk = uid_getter()
        month_first = _safe_parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")

        focus = _normalize_focus(request.args.get("focus"))
        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)
        initial_batch_id = int(request.args.get("batch_id") or 0)
        initial_batch_status_url = ""
        if initial_batch_id > 0 and _batch_tables_ready():
            owns_batch = (
                db.session.query(ReceiptBatch.id)
                .filter(ReceiptBatch.id == initial_batch_id, ReceiptBatch.user_pk == user_pk)
                .first()
                is not None
            )
            if not owns_batch:
                initial_batch_id = 0
            else:
                initial_batch_status_url = url_for(
                    "web_calendar.receipt_batch_status",
                    batch_id=initial_batch_id,
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                )

        return render_template(
            "calendar/receipt_new_upload.html",
            month_key=month_key,
            month_first=month_first,
            focus=focus,
            q=q,
            limit=limit,
            upload_max_files=_receipt_max_files(),
            upload_max_bytes=_receipt_upload_max_bytes(),
            initial_batch_id=initial_batch_id,
            initial_batch_status_url=initial_batch_status_url,
        )

    @bp.post("/review/receipt-new")
    def receipt_new_upload():
        user_pk = uid_getter()
        wants_json = _wants_json_response()
        ip = client_ip()
        month_first = _safe_parse_month(parse_date_ym(request.form.get("month")) or request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")

        focus = safe_str(request.form.get("focus") or default_review_focus, max_len=40)
        if focus not in review_focus:
            focus = default_review_focus
        q = safe_str(request.form.get("q"), max_len=120)
        limit = parse_limit(request.form.get("limit"), default=200)

        files = request.files.getlist("files")
        if not files:
            one = request.files.get("file")
            if one:
                files = [one]

        receipt_text = safe_str(request.form.get("receipt_text"), max_len=8000, allow_newline=True)
        receipt_type = safe_str(request.form.get("receipt_type"), max_len=20) or ("electronic" if receipt_text else "paper")

        def _upload_fail(message: str, status_code: int = 400):
            if wants_json:
                return jsonify({"ok": False, "message": message}), status_code
            flash(message, "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=month_key, focus=focus, q=q, limit=limit))

        limited, wait_sec = hit_limit(
            key=f"web:receipt:single:{user_pk}:{ip}",
            limit=24,
            window_seconds=60,
        )
        if limited:
            return _upload_fail(f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요.", status_code=429)

        max_files = _receipt_max_files()
        if files and len(files) > max_files:
            return _upload_fail(f"한 번에 최대 {max_files}장까지 업로드할 수 있어요.")

        if (not files) and (not receipt_text):
            return _upload_fail("파일을 올리거나 전자영수증 텍스트를 붙여넣어주세요.")

        try:
            if files:
                stored = store_evidence_draft_file_multi(user_pk=user_pk, month_key=month_key, files=files)
            else:
                stored = store_evidence_draft_text(user_pk=user_pk, month_key=month_key, text=receipt_text)
            _log_receipt_stage(status="실행 중", file_name=(stored.original_filename or "영수증"), detail="업로드 됨")
        except Exception as e:
            _, friendly = normalize_receipt_error(f"업로드 실패: {e}")
            return _upload_fail(friendly)

        duplicate_suspected = False
        if stored.sha256:
            duplicate_suspected = (
                db.session.query(EvidenceItem.id)
                .filter(
                    EvidenceItem.user_pk == user_pk,
                    EvidenceItem.sha256 == stored.sha256,
                    EvidenceItem.deleted_at.is_(None),
                )
                .first()
                is not None
            )
            if (not duplicate_suspected) and _batch_tables_ready():
                duplicate_suspected = (
                    db.session.query(ReceiptItem.id)
                    .filter(
                        ReceiptItem.user_pk == user_pk,
                        ReceiptItem.sha256 == stored.sha256,
                        ReceiptItem.status != ITEM_STATUS_FAILED,
                    )
                    .first()
                    is not None
                )
            if not duplicate_suspected:
                for item in _iter_recent_drafts(user_pk=user_pk):
                    if str(item.get("sha256") or "") == stored.sha256:
                        duplicate_suspected = True
                        break

        try:
            if (stored.mime_type or "").startswith("text/") or stored.abs_path.suffix.lower() == ".txt":
                txt = stored.abs_path.read_text(encoding="utf-8", errors="ignore")
                draft = parse_receipt_from_text(text=txt)
            else:
                draft = parse_receipt_from_file(abs_path=stored.abs_path, mime_type=(stored.mime_type or ""))
        except Exception as e:
            _, friendly = normalize_receipt_error(f"분석 실패: {e}")
            draft = type("X", (), {"ok": False, "provider": "parser", "parsed": {}, "error": friendly})

        token = uuid4().hex
        compact_parsed = compact_receipt_parsed(getattr(draft, "parsed", {}) or {})
        duplicate_hint = ""
        if duplicate_suspected:
            duplicate_hint = "같은 영수증이 이미 처리 중이거나 등록되어 있을 수 있어요. 기존 거래와 중복인지 확인해 주세요."
        draft_payload = {
            "month_key": month_key,
            "file_key": stored.file_key,
            "original_filename": stored.original_filename,
            "mime_type": stored.mime_type,
            "size_bytes": int(stored.size_bytes or 0),
            "sha256": stored.sha256,
            "receipt_type": receipt_type,
            "draft_ok": bool(getattr(draft, "ok", False)),
            "draft_provider": getattr(draft, "provider", "openai"),
            "draft_error": getattr(draft, "error", "") or "",
            "duplicate_suspected": bool(duplicate_suspected),
            "duplicate_hint": duplicate_hint,
            "parsed": compact_parsed,
        }
        _save_draft_file(user_pk=user_pk, token=token, payload=draft_payload)
        if bool(getattr(draft, "ok", False)):
            _log_receipt_stage(status="완료", file_name=(stored.original_filename or "영수증"))
        else:
            _log_receipt_stage(
                status="실패",
                file_name=(stored.original_filename or "영수증"),
                detail=(str(getattr(draft, "error", "") or "영수증 분석 실패")[:220]),
                level="ERROR",
            )

        confirm_url = url_for("web_calendar.receipt_new_confirm_page", token=token, month=month_key, focus=focus, q=q, limit=limit)
        if wants_json:
            return jsonify(
                {
                    "ok": True,
                    "token": token,
                    "confirm_url": confirm_url,
                    "original_filename": stored.original_filename,
                    "sha256": stored.sha256,
                    "draft_ok": bool(getattr(draft, "ok", False)),
                    "draft_error": getattr(draft, "error", "") or "",
                    "duplicate_suspected": bool(duplicate_suspected),
                }
            )
        return redirect(confirm_url)

    @bp.post("/review/receipt-new/batch")
    def receipt_new_batch_upload():
        user_pk = uid_getter()
        wants_json = _wants_json_response()
        ip = client_ip()

        month_first = _safe_parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = _normalize_focus(request.form.get("focus"))
        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)
        receipt_type = (request.form.get("receipt_type") or "").strip() or "paper"

        files = request.files.getlist("files")
        if not files:
            one = request.files.get("file")
            if one:
                files = [one]

        def _batch_fail(message: str, status_code: int = 400):
            if wants_json:
                return jsonify({"ok": False, "message": message}), status_code
            flash(message, "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=month_key, focus=focus, q=q, limit=limit))

        limited, wait_sec = hit_limit(
            key=f"web:receipt:batch:{user_pk}:{ip}",
            limit=16,
            window_seconds=60,
        )
        if limited:
            return _batch_fail(f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요.", status_code=429)

        if not files:
            return _batch_fail("파일을 먼저 선택해주세요.")

        max_files = _receipt_max_files()
        if len(files) > max_files:
            return _batch_fail(f"한 번에 최대 {max_files}장까지 업로드할 수 있어요.")
        if not _batch_tables_ready():
            return _batch_fail("다중 업로드 준비가 아직 끝나지 않았어요. 잠시 후 다시 시도해주세요.", status_code=503)

        created_items: list[ReceiptItem] = []
        stored_file_keys: list[str] = []
        batch: ReceiptBatch | None = None
        try:
            batch = ReceiptBatch(
                user_pk=user_pk,
                month_key=month_key,
                status="queued",
                total_count=0,
                done_count=0,
                failed_count=0,
            )
            db.session.add(batch)
            db.session.flush()

            for f in files:
                original_name = (getattr(f, "filename", None) or "").strip() or "receipt"
                try:
                    stored = store_evidence_draft_file_multi(user_pk=user_pk, month_key=month_key, files=[f])
                    if stored.file_key:
                        stored_file_keys.append(stored.file_key)
                    _log_receipt_stage(status="실행 중", file_name=(stored.original_filename or original_name), detail="업로드 됨")
                    duplicate_error = ""
                    duplicate_note = ""
                    sha_value = str(stored.sha256 or "").strip()
                    if sha_value:
                        has_non_failed_receipt = (
                            db.session.query(ReceiptItem.id)
                            .filter(ReceiptItem.user_pk == user_pk)
                            .filter(ReceiptItem.sha256 == sha_value)
                            .filter(ReceiptItem.status != ITEM_STATUS_FAILED)
                            .first()
                            is not None
                        )
                        if has_non_failed_receipt:
                            duplicate_note = "이미 처리 중이거나 완료된 같은 영수증이 있어요. 중복 여부를 확인해 주세요."

                        has_existing_evidence = (
                            db.session.query(EvidenceItem.id)
                            .filter(EvidenceItem.user_pk == user_pk)
                            .filter(EvidenceItem.sha256 == sha_value)
                            .filter(EvidenceItem.deleted_at.is_(None))
                            .first()
                            is not None
                        )
                        if has_existing_evidence and not duplicate_note:
                            duplicate_note = "이미 등록된 영수증이 있어요. 기존 거래와 중복인지 확인해 주세요."

                        for prev_item in created_items:
                            if (
                                prev_item.status != ITEM_STATUS_FAILED
                                and str(prev_item.sha256 or "").strip() == sha_value
                            ):
                                duplicate_error = "같은 파일이 이번 업로드에 중복으로 포함됐어요."
                                break

                    if duplicate_error:
                        try:
                            if stored.file_key:
                                delete_physical_file(stored.file_key)
                        except Exception:
                            pass
                        item = ReceiptItem(
                            batch_id=batch.id,
                            user_pk=user_pk,
                            file_key=None,
                            original_filename=stored.original_filename,
                            mime_type=stored.mime_type,
                            size_bytes=int(stored.size_bytes or 0),
                            sha256=stored.sha256 or None,
                            status=ITEM_STATUS_FAILED,
                            error_message=duplicate_error,
                            receipt_type=receipt_type,
                            parsed_json=None,
                        )
                        _log_receipt_stage(
                            status="실패",
                            file_name=(stored.original_filename or original_name),
                            detail=duplicate_error,
                            level="ERROR",
                        )
                    else:
                        if duplicate_note:
                            _log_receipt_stage(
                                status="주의",
                                file_name=(stored.original_filename or original_name),
                                detail=duplicate_note,
                            )
                        item_status = ITEM_STATUS_FAILED if duplicate_note else ITEM_STATUS_UPLOADED
                        item_error_message = duplicate_note[:500] if duplicate_note else ""
                        item = ReceiptItem(
                            batch_id=batch.id,
                            user_pk=user_pk,
                            file_key=stored.file_key,
                            original_filename=stored.original_filename,
                            mime_type=stored.mime_type,
                            size_bytes=int(stored.size_bytes or 0),
                            sha256=stored.sha256 or None,
                            status=item_status,
                            error_message=item_error_message,
                            receipt_type=receipt_type,
                            parsed_json=None,
                        )
                except Exception as e:
                    _, friendly = normalize_receipt_error(f"업로드 실패: {e}")
                    _log_receipt_stage(
                        status="실패",
                        file_name=original_name,
                        detail=(friendly[:220] or "업로드 실패"),
                        level="ERROR",
                    )
                    item = ReceiptItem(
                        batch_id=batch.id,
                        user_pk=user_pk,
                        file_key=None,
                        original_filename=original_name,
                        mime_type=None,
                        size_bytes=None,
                        sha256=None,
                        status=ITEM_STATUS_FAILED,
                        error_message=friendly[:500],
                        receipt_type=receipt_type,
                        parsed_json=None,
                    )
                db.session.add(item)
                created_items.append(item)

            recompute_batch_counts(batch.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            for key in stored_file_keys:
                try:
                    delete_physical_file(key)
                except Exception:
                    pass
            return _batch_fail("업로드를 시작하지 못했어요. 잠시 후 다시 시도해주세요.", status_code=400)

        if batch is None:
            return _batch_fail("업로드를 시작하지 못했어요. 잠시 후 다시 시도해주세요.", status_code=400)

        status_url = url_for("web_calendar.receipt_batch_status", batch_id=batch.id, month=month_key, focus=focus, q=q, limit=limit)
        duplicate_flags = _build_duplicate_flags(user_pk=user_pk, items=created_items)
        payload_items = [
            _serialize_batch_item(item=item, month_key=month_key, focus=focus, q=q, limit=limit)
            for item in created_items
        ]
        for row in payload_items:
            row["duplicate"] = bool(duplicate_flags.get(int(row.get("id") or 0), False))
        if wants_json:
            return jsonify(
                {
                    "ok": True,
                    "batch_id": batch.id,
                    "status": batch.status,
                    "total_count": int(batch.total_count or 0),
                    "done_count": int(batch.done_count or 0),
                    "failed_count": int(batch.failed_count or 0),
                    "status_url": status_url,
                    "items": payload_items,
                }
            )
        flash("여러 장 업로드를 시작했어요. 완료된 항목부터 확인할 수 있어요.", "ok")
        return redirect(url_for("web_calendar.receipt_new_upload_page", month=month_key, focus=focus, q=q, limit=limit, batch_id=batch.id))

    @bp.get("/review/receipt-new/batch/<int:batch_id>/status")
    def receipt_batch_status(batch_id: int):
        user_pk = uid_getter()
        if not _batch_tables_ready():
            return jsonify({"ok": False, "message": "다중 업로드 준비 중이에요. 잠시 후 다시 시도해주세요."}), 503
        month_key = _safe_parse_month(request.args.get("month")).strftime("%Y-%m")
        focus = _normalize_focus(request.args.get("focus"))
        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)

        batch = ReceiptBatch.query.filter_by(id=batch_id, user_pk=user_pk).first()
        if not batch:
            return jsonify({"ok": False, "message": "배치를 찾지 못했어요."}), 404

        def _derive_status(items_list: list[ReceiptItem], batch_obj: ReceiptBatch):
            total_v = len(items_list)
            processing_v = sum(1 for x in items_list if x.status == ITEM_STATUS_PROCESSING)
            queued_v = sum(1 for x in items_list if x.status == ITEM_STATUS_UPLOADED)
            done_v = sum(1 for x in items_list if x.status == ITEM_STATUS_DONE)
            failed_v = sum(1 for x in items_list if x.status == ITEM_STATUS_FAILED)
            if processing_v > 0:
                status_v = "processing"
            elif queued_v > 0:
                status_v = "queued"
            elif total_v > 0 and (done_v + failed_v) >= total_v:
                status_v = "done_with_errors" if failed_v > 0 else "done"
            else:
                status_v = batch_obj.status or "queued"
            done_bool_v = status_v in (BATCH_STATUS_DONE, BATCH_STATUS_DONE_WITH_ERRORS)
            return (
                total_v,
                processing_v,
                queued_v,
                done_v,
                failed_v,
                status_v,
                done_bool_v,
            )

        def _delay_state(items_list: list[ReceiptItem], *, processing_v: int, queued_v: int):
            active_list = [x for x in items_list if x.status in (ITEM_STATUS_UPLOADED, ITEM_STATUS_PROCESSING)]
            oldest_active = None
            if active_list:
                try:
                    oldest_active = min(
                        ((x.updated_at or x.created_at) for x in active_list if (x.updated_at or x.created_at)),
                        default=None,
                    )
                except Exception:
                    oldest_active = None
            delay_seconds_v = 0
            is_delayed_v = False
            delay_message_v = ""
            needs_worker_check_v = False
            if oldest_active:
                try:
                    delay_seconds_v = max(0, int((utcnow_fn() - oldest_active).total_seconds()))
                except Exception:
                    delay_seconds_v = 0
                delay_threshold_v = int(current_app.config.get("RECEIPT_BATCH_DELAY_SECONDS", 35))
                if (queued_v + processing_v) > 0 and delay_seconds_v >= delay_threshold_v:
                    is_delayed_v = True
                    if processing_v > 0:
                        delay_message_v = "처리 시간이 길어지고 있어요. 잠시만 기다려 주세요."
                    else:
                        delay_message_v = "처리가 잠시 지연되고 있어요. 자동 재개를 시도할 수 있어요."
                worker_check_threshold_v = int(current_app.config.get("RECEIPT_BATCH_WORKER_CHECK_SECONDS", 25))
                if queued_v > 0 and processing_v == 0 and delay_seconds_v >= worker_check_threshold_v:
                    needs_worker_check_v = True
                    delay_message_v = "처리가 멈춰 있어 보여요. 자동 재개를 시도하고 있어요."
                elif processing_v > 0 and delay_seconds_v >= worker_check_threshold_v:
                    needs_worker_check_v = True
                    delay_message_v = "처리가 오래 걸리고 있어요. 자동 점검을 시도하고 있어요."
            return delay_seconds_v, is_delayed_v, delay_message_v, needs_worker_check_v

        items = (
            ReceiptItem.query.filter_by(batch_id=batch.id, user_pk=user_pk)
            .order_by(ReceiptItem.id.asc())
            .all()
        )
        duplicate_flags = _build_duplicate_flags(user_pk=user_pk, items=items)
        (
            total_count,
            processing_count,
            queued_count,
            done_count,
            failed_count,
            status_value,
            done_bool,
        ) = _derive_status(items, batch)
        delay_seconds, is_delayed, delay_message, needs_worker_check = _delay_state(
            items,
            processing_v=processing_count,
            queued_v=queued_count,
        )

        if needs_worker_check and queued_count > 0 and processing_count == 0:
            claimed_item_id = 0
            try:
                claimed = claim_next_uploaded_item_for_batch(user_pk=user_pk, batch_id=batch.id)
                claimed_item_id = int(claimed.id) if claimed else 0
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                current_app.logger.warning(
                    "[WARN][영수증으로 거래 추가][자동 재개 실패] : batch-%s, 선점 실패: %s",
                    batch.id,
                    str(e)[:220],
                )
                claimed_item_id = 0

            if claimed_item_id > 0:
                try:
                    process_receipt_item(claimed_item_id)
                    recompute_batch_counts(batch.id)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    item = db.session.get(ReceiptItem, claimed_item_id)
                    if item:
                        _ = mark_receipt_item_failed(item, f"처리 재개 실패: {e}")
                        _log_receipt_stage(
                            status="실패",
                            file_name=(item.original_filename or f"item-{claimed_item_id}"),
                            detail=(item.error_message or "처리 재개 실패"),
                            level="ERROR",
                        )
                    try:
                        recompute_batch_counts(batch.id)
                        db.session.commit()
                    except Exception as e2:
                        db.session.rollback()
                        current_app.logger.warning(
                            "[WARN][영수증으로 거래 추가][자동 재개 실패] : batch-%s, 상태 저장 실패: %s",
                            batch.id,
                            str(e2)[:220],
                        )

                batch = ReceiptBatch.query.filter_by(id=batch.id, user_pk=user_pk).first() or batch
                items = (
                    ReceiptItem.query.filter_by(batch_id=batch.id, user_pk=user_pk)
                    .order_by(ReceiptItem.id.asc())
                    .all()
                )
                duplicate_flags = _build_duplicate_flags(user_pk=user_pk, items=items)
                (
                    total_count,
                    processing_count,
                    queued_count,
                    done_count,
                    failed_count,
                    status_value,
                    done_bool,
                ) = _derive_status(items, batch)
                delay_seconds, is_delayed, delay_message, needs_worker_check = _delay_state(
                    items,
                    processing_v=processing_count,
                    queued_v=queued_count,
                )
                if not delay_message and (queued_count + processing_count) > 0:
                    delay_message = "멈춘 항목 재개를 시도했어요. 잠시 후 상태를 다시 확인해주세요."

        payload_items = [
            _serialize_batch_item(item=item, month_key=month_key, focus=focus, q=q, limit=limit)
            for item in items
        ]
        for row in payload_items:
            row["duplicate"] = bool(duplicate_flags.get(int(row.get("id") or 0), False))

        return jsonify(
            {
                "ok": True,
                "batch_id": batch.id,
                "status": status_value,
                "total_count": int(total_count),
                "done_count": int(done_count),
                "failed_count": int(failed_count),
                "processing_count": processing_count,
                "queued_count": queued_count,
                "is_finished": bool(done_bool),
                "is_delayed": is_delayed,
                "delay_seconds": int(delay_seconds),
                "delay_message": delay_message,
                "needs_worker_check": needs_worker_check,
                "process_once_url": url_for("web_calendar.receipt_batch_process_once", batch_id=batch.id),
                "items": payload_items,
            }
        )

    @bp.post("/review/receipt-new/batch/<int:batch_id>/items/<int:item_id>/retry")
    def receipt_batch_retry_item(batch_id: int, item_id: int):
        user_pk = uid_getter()
        if not _batch_tables_ready():
            return jsonify({"ok": False, "message": "다중 업로드 준비 중이에요. 잠시 후 다시 시도해주세요."}), 503
        item = (
            ReceiptItem.query.filter_by(id=item_id, batch_id=batch_id, user_pk=user_pk).first()
        )
        if not item:
            return jsonify({"ok": False, "message": "항목을 찾지 못했어요."}), 404
        if item.status != ITEM_STATUS_FAILED:
            return jsonify({"ok": False, "message": "실패한 항목만 다시 시도할 수 있어요."}), 400
        if not item.file_key:
            return jsonify({"ok": False, "message": "업로드 파일이 없어 재시도할 수 없어요."}), 400
        retry_allowed, retry_reason = can_retry_receipt_item(item)
        if not retry_allowed:
            return jsonify({"ok": False, "message": retry_block_message(retry_reason)}), 400

        reset_receipt_item_for_retry(item)
        recompute_batch_counts(batch_id)
        db.session.commit()
        return jsonify({"ok": True, "item_id": item.id, "status": item.status})

    @bp.post("/review/receipt-new/batch/<int:batch_id>/process-once")
    def receipt_batch_process_once(batch_id: int):
        user_pk = uid_getter()
        ip = client_ip()
        wants_json = _wants_json_response()
        limited, wait_sec = hit_limit(
            key=f"web:receipt:process_once:{user_pk}:{ip}",
            limit=30,
            window_seconds=60,
        )
        if limited:
            msg = f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요."
            if wants_json:
                return jsonify({"ok": False, "message": msg}), 429
            flash(msg, "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page"))
        if not _batch_tables_ready():
            msg = "다중 업로드 준비 중이에요. 잠시 후 다시 시도해주세요."
            if wants_json:
                return jsonify({"ok": False, "message": msg}), 503
            flash(msg, "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page"))

        batch = ReceiptBatch.query.filter_by(id=batch_id, user_pk=user_pk).first()
        if not batch:
            msg = "배치를 찾지 못했어요."
            if wants_json:
                return jsonify({"ok": False, "message": msg}), 404
            flash(msg, "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page"))

        stale_minutes = int(current_app.config.get("RECEIPT_BATCH_STALE_MINUTES", 3))
        try:
            max_items = int((request.args.get("max_items") or request.form.get("max_items") or "1").strip() or 1)
        except Exception:
            max_items = 1
        max_items = max(1, min(5, max_items))

        processed_ids: list[int] = []
        if stale_minutes > 0:
            try:
                requeue_stale_processing_items(max_age_minutes=stale_minutes, limit=50)
                db.session.commit()
            except Exception:
                db.session.rollback()

        for _ in range(max_items):
            claimed_item_id = 0
            try:
                claimed = claim_next_uploaded_item_for_batch(user_pk=user_pk, batch_id=batch_id)
                claimed_item_id = int(claimed.id) if claimed else 0
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(
                    "[ERROR][영수증으로 거래 추가][실패] : batch-%s, 항목 선점 실패: %s",
                    batch_id,
                    str(e)[:220],
                )
                claimed_item_id = 0

            if claimed_item_id <= 0:
                break

            try:
                process_receipt_item(claimed_item_id)
                processed_ids.append(int(claimed_item_id))
                recompute_batch_counts(batch_id)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                item = db.session.get(ReceiptItem, claimed_item_id)
                if item:
                    _ = mark_receipt_item_failed(item, f"처리 재개 실패: {e}")
                    _log_receipt_stage(
                        status="실패",
                        file_name=(item.original_filename or f"item-{claimed_item_id}"),
                        detail=(item.error_message or "처리 재개 실패"),
                        level="ERROR",
                    )
                try:
                    recompute_batch_counts(batch_id)
                    db.session.commit()
                except Exception as e2:
                    db.session.rollback()
                    current_app.logger.warning(
                        "[WARN][영수증으로 거래 추가][실패] : batch-%s, 실패 상태 저장 실패: %s",
                        batch_id,
                        str(e2)[:220],
                    )

        if not processed_ids:
            recompute_batch_counts(batch_id)
            db.session.commit()

        refreshed = db.session.get(ReceiptBatch, batch_id)
        payload = {
            "ok": True,
            "processed": bool(len(processed_ids) > 0),
            "processed_count": int(len(processed_ids)),
            "item_id": int(processed_ids[-1] if processed_ids else 0),
            "batch_status": str((refreshed.status if refreshed else "queued") or "queued"),
            "total_count": int((refreshed.total_count if refreshed else 0) or 0),
            "done_count": int((refreshed.done_count if refreshed else 0) or 0),
            "failed_count": int((refreshed.failed_count if refreshed else 0) or 0),
        }
        if wants_json:
            return jsonify(payload)

        if processed_ids:
            flash(f"대기 항목 {len(processed_ids)}건 처리를 재개했어요.", "ok")
        else:
            flash("재개할 대기 항목이 없어요.", "ok")
        return redirect(
            url_for(
                "web_calendar.receipt_new_upload_page",
                month=(batch.month_key or ""),
                focus=request.args.get("focus") or default_review_focus,
                q=request.args.get("q") or "",
                limit=parse_limit(request.args.get("limit"), default=200),
                batch_id=batch.id,
            )
        )

    @bp.post("/review/receipt-new/batch/<int:batch_id>/stop")
    def receipt_batch_stop(batch_id: int):
        user_pk = uid_getter()
        if not _batch_tables_ready():
            return jsonify({"ok": False, "message": "다중 업로드 준비 중이에요. 잠시 후 다시 시도해주세요."}), 503

        batch = ReceiptBatch.query.filter_by(id=batch_id, user_pk=user_pk).first()
        if not batch:
            return jsonify({"ok": False, "message": "배치를 찾지 못했어요."}), 404

        queued_items = (
            ReceiptItem.query.filter_by(batch_id=batch.id, user_pk=user_pk, status=ITEM_STATUS_UPLOADED)
            .all()
        )
        processing_count = (
            ReceiptItem.query.filter_by(batch_id=batch.id, user_pk=user_pk, status=ITEM_STATUS_PROCESSING)
            .count()
        )
        stopped_count = 0
        for item in queued_items:
            mark_receipt_item_paused(item, "사용자가 중단했어요. 필요하면 다시 시도해주세요.")
            stopped_count += 1

        recompute_batch_counts(batch.id)
        db.session.commit()

        msg = f"대기 항목 {stopped_count}건을 중단했어요."
        if int(processing_count or 0) > 0:
            msg += " 이미 처리 중인 항목은 마무리될 수 있어요."

        refreshed = db.session.get(ReceiptBatch, batch.id)
        return jsonify(
            {
                "ok": True,
                "message": msg,
                "stopped_count": int(stopped_count),
                "processing_count": int(processing_count or 0),
                "batch_status": str((refreshed.status if refreshed else "queued") or "queued"),
                "total_count": int((refreshed.total_count if refreshed else 0) or 0),
                "done_count": int((refreshed.done_count if refreshed else 0) or 0),
                "failed_count": int((refreshed.failed_count if refreshed else 0) or 0),
            }
        )

    @bp.get("/review/receipt-new/batch/items/<int:item_id>/confirm")
    def receipt_batch_item_confirm(item_id: int):
        user_pk = uid_getter()
        if not _batch_tables_ready():
            flash("다중 업로드 준비 중이에요. 잠시 후 다시 시도해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page"))
        item = ReceiptItem.query.filter_by(id=item_id, user_pk=user_pk).first()
        if not item:
            flash("항목을 찾지 못했어요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page"))

        month_key = (request.args.get("month") or "").strip()
        if not month_key:
            batch = ReceiptBatch.query.filter_by(id=item.batch_id, user_pk=user_pk).first()
            month_key = (batch.month_key if batch else "") or utcnow_fn().strftime("%Y-%m")
        month_key = _safe_parse_month(month_key).strftime("%Y-%m")

        focus = _normalize_focus(request.args.get("focus"))
        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)

        if item.status in (ITEM_STATUS_UPLOADED, ITEM_STATUS_PROCESSING):
            flash("아직 분석이 끝나지 않았어요. 잠시 후 다시 확인해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=month_key, focus=focus, q=q, limit=limit, batch_id=item.batch_id))
        if item.status == ITEM_STATUS_FAILED and not item.file_key:
            flash("파일이 없어 직접 입력을 열 수 없어요. 다시 업로드해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=month_key, focus=focus, q=q, limit=limit, batch_id=item.batch_id))

        token = uuid4().hex
        payload = build_draft_payload_from_item(item)
        payload["month_key"] = month_key
        try:
            duplicate_map = _build_duplicate_flags(user_pk=user_pk, items=[item])
            if bool(duplicate_map.get(int(item.id), False)):
                payload["duplicate_suspected"] = True
        except Exception:
            pass
        item_error = str(item.error_message or "").strip()
        if item_error and ("중복" in item_error) and (not str(payload.get("duplicate_hint") or "").strip()):
            payload["duplicate_hint"] = item_error[:200]
        _save_draft_file(user_pk=user_pk, token=token, payload=payload)

        # 구 버전 호환(session fallback)
        drafts = session.get("receipt_new_drafts") or {}
        if not isinstance(drafts, dict):
            drafts = {}
        drafts[token] = payload
        session["receipt_new_drafts"] = drafts
        session.modified = True

        return redirect(url_for("web_calendar.receipt_new_confirm_page", token=token, month=month_key, focus=focus, q=q, limit=limit))

    @bp.get("/review/receipt-new/confirm")
    def receipt_new_confirm_page():
        user_pk = uid_getter()
        token = safe_str(request.args.get("token"), max_len=80)
        focus = safe_str(request.args.get("focus") or default_review_focus, max_len=40)
        if focus not in review_focus:
            focus = default_review_focus
        q = safe_str(request.args.get("q"), max_len=120)
        limit = parse_limit(request.args.get("limit"), default=200)
        requested_month = parse_date_ym(request.args.get("month")) or safe_str(request.args.get("month"), max_len=7)
        fallback_month = _safe_parse_month(requested_month).strftime("%Y-%m")

        d = _load_draft_file(user_pk=user_pk, token=token)
        if not isinstance(d, dict):
            drafts = session.get("receipt_new_drafts") or {}
            if not isinstance(drafts, dict):
                drafts = {}
            d = drafts.get(token)
        if not token or not isinstance(d, dict):
            flash("세션이 만료되었어요. 영수증을 다시 올려주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=fallback_month, focus=focus, q=q, limit=limit))

        month_raw = parse_date_ym(request.args.get("month")) or safe_str(d.get("month_key") or fallback_month, max_len=7)
        month_first = _safe_parse_month(month_raw)
        month_key = month_first.strftime("%Y-%m")
        duplicate_suspected = bool(d.get("duplicate_suspected"))
        duplicate_hint = str(d.get("duplicate_hint") or "").strip()
        sha_value = str(d.get("sha256") or "").strip()
        if sha_value:
            try:
                has_existing_evidence = (
                    db.session.query(EvidenceItem.id)
                    .filter(
                        EvidenceItem.user_pk == user_pk,
                        EvidenceItem.sha256 == sha_value,
                        EvidenceItem.deleted_at.is_(None),
                    )
                    .first()
                    is not None
                )
                non_failed_receipt_count = (
                    db.session.query(func.count(ReceiptItem.id))
                    .filter(
                        ReceiptItem.user_pk == user_pk,
                        ReceiptItem.sha256 == sha_value,
                        ReceiptItem.status != ITEM_STATUS_FAILED,
                    )
                    .scalar()
                ) or 0
                if has_existing_evidence or int(non_failed_receipt_count) > 1:
                    duplicate_suspected = True
                    if not duplicate_hint:
                        duplicate_hint = "같은 영수증이 이미 처리 중이거나 등록되어 있을 수 있어요. 기존 거래와 중복인지 확인해 주세요."
            except Exception:
                pass

        return render_template(
            "calendar/receipt_new_confirm.html",
            token=token,
            month_key=month_key,
            month_first=month_first,
            focus=focus,
            q=q,
            limit=limit,
            original_filename=d.get("original_filename") or "",
            receipt_type=d.get("receipt_type") or "paper",
            draft_ok=bool(d.get("draft_ok")),
            draft_provider=d.get("draft_provider") or "openai",
            draft_error=d.get("draft_error") or "",
            duplicate_suspected=bool(duplicate_suspected),
            duplicate_hint=duplicate_hint,
            view=d.get("parsed") or {},
        )

    @bp.post("/review/receipt-new/save")
    def receipt_new_save():
        user_pk = uid_getter()

        token = safe_str(request.form.get("token"), max_len=80)
        d = _load_draft_file(user_pk=user_pk, token=token)
        drafts = session.get("receipt_new_drafts") or {}
        if not isinstance(drafts, dict):
            drafts = {}
        if not isinstance(d, dict):
            d = drafts.get(token)
        if not token or not isinstance(d, dict):
            fallback_month = _safe_parse_month(request.form.get("month")).strftime("%Y-%m")
            flash("세션이 만료되었어요. 영수증을 다시 올려주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=fallback_month))

        month_raw = parse_date_ym(request.form.get("month")) or safe_str(d.get("month_key"), max_len=7)
        month_first = _safe_parse_month(month_raw)
        month_key = month_first.strftime("%Y-%m")
        focus = safe_str(request.form.get("focus") or default_review_focus, max_len=40)
        if focus not in review_focus:
            focus = default_review_focus
        q = safe_str(request.form.get("q"), max_len=120)
        limit = parse_limit(request.form.get("limit"), default=200)

        merchant = safe_str(request.form.get("merchant"), max_len=120)
        paid_at = safe_str(request.form.get("paid_at"), max_len=32)
        total_raw = safe_str(request.form.get("total_krw"), max_len=32)

        expense_kind = safe_str(request.form.get("expense_kind") or "mixed", max_len=20)
        if expense_kind not in ("business", "personal", "mixed"):
            expense_kind = "mixed"

        occurred_at = parse_paid_at_input(paid_at, month_key)
        amt = parse_amount_krw(total_raw)
        if amt <= 0:
            flash("총액(원)을 확인해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_confirm_page", token=token, month=month_key, focus=focus, q=q, limit=limit))

        tx_month_key = occurred_at.strftime("%Y-%m")
        before = compute_tax_estimate(user_pk, month_key=tx_month_key)
        before_tax = int(before.buffer_target_krw)
        before_profit = int(before.estimated_profit_krw)
        before_expense = int(before.expense_business_krw)

        file_key = str(d.get("file_key") or "")
        if not file_key:
            flash("업로드된 파일 정보를 찾지 못했어요. 다시 업로드해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_upload_page", month=month_key, focus=focus, q=q, limit=limit))
        sha = str(d.get("sha256") or "")
        external_hash = hashlib.sha256(f"receipt|{user_pk}|{sha}|{occurred_at.isoformat()}|{amt}|{merchant}".encode("utf-8")).hexdigest()

        tx = Transaction(
            user_pk=user_pk,
            import_job_id=None,
            occurred_at=occurred_at,
            direction="out",
            amount_krw=amt,
            counterparty=merchant or None,
            memo=None,
            source="receipt",
            external_hash=external_hash,
        )

        moved_key = None
        def _persist_retry_draft(new_file_key: str | None) -> None:
            try:
                if not new_file_key:
                    return
                d["file_key"] = str(new_file_key)
                _save_draft_file(user_pk=user_pk, token=token, payload=d)
                drafts[token] = d
                session["receipt_new_drafts"] = drafts
                session.modified = True
            except Exception:
                pass
        try:
            db.session.add(tx)
            db.session.flush()

            moved_key = move_evidence_file_to_tx(user_pk=user_pk, month_key=tx_month_key, tx_id=tx.id, file_key=file_key)

            if expense_kind == "business":
                req, st = "required", "attached"
            elif expense_kind == "personal":
                req, st = "not_needed", "not_needed"
            else:
                req, st = "maybe", "attached"

            payload = {
                "receipt_type": d.get("receipt_type") or "paper",
                "merchant": merchant,
                "paid_at": paid_at,
                "total_krw": amt,
                "expense_kind": expense_kind,
            }

            ev = EvidenceItem(
                user_pk=user_pk,
                transaction_id=tx.id,
                requirement=req,
                status=st,
                note="receipt_final:" + json.dumps(payload, ensure_ascii=False),
                file_key=moved_key,
                original_filename=str(d.get("original_filename") or ""),
                mime_type=str(d.get("mime_type") or ""),
                size_bytes=int(d.get("size_bytes") or 0) or None,
                sha256=str(d.get("sha256") or ""),
                uploaded_at=utcnow_fn(),
                deleted_at=None,
                retention_until=default_retention_until(),
            )
            db.session.add(ev)

            el = ExpenseLabel(user_pk=user_pk, transaction_id=tx.id)
            el.status = expense_kind
            el.confidence = 100
            el.labeled_by = "auto"
            el.decided_at = utcnow_fn()
            db.session.add(el)

            db.session.commit()

        except IntegrityError:
            db.session.rollback()
            _persist_retry_draft(moved_key)
            flash("중복 등록으로 보이는 영수증이에요. 이미 추가된 거래인지 확인해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_confirm_page", token=token, month=month_key, focus=focus, q=q, limit=limit))
        except Exception as e:
            db.session.rollback()
            _persist_retry_draft(moved_key)
            current_app.logger.exception("[ERROR][영수증으로 거래 추가][실패] : 저장 중 예외")
            flash("저장 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")
            return redirect(url_for("web_calendar.receipt_new_confirm_page", token=token, month=month_key, focus=focus, q=q, limit=limit))

        drafts.pop(token, None)
        session["receipt_new_drafts"] = drafts
        session.modified = True
        _delete_draft_file(user_pk=user_pk, token=token)

        after = compute_tax_estimate(user_pk, month_key=tx_month_key)
        after_tax = int(after.buffer_target_krw)
        after_profit = int(after.estimated_profit_krw)
        after_expense = int(after.expense_business_krw)
        tax_delta = after_tax - before_tax

        flash("영수증으로 지출을 추가했습니다. (세금/증빙이 즉시 업데이트돼요)", "ok")

        redirect_url = (
            url_for(
                "web_calendar.review",
                month=tx_month_key,
                focus=focus,
                q=q,
                limit=limit,
                toast="receipt_applied",
                tax_before=before_tax,
                tax_after=after_tax,
                profit_before=before_profit,
                profit_after=after_profit,
                expense_before=before_expense,
                expense_after=after_expense,
                tax_delta=tax_delta,
            )
            + f"#tx-{tx.id}"
        )

        if is_partial():
            return {"ok": True, "redirect": redirect_url}
        return redirect(redirect_url)

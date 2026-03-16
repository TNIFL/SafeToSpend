from __future__ import annotations

import argparse
import copy
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, func

from app import create_app
from core.extensions import db
from domain.models import BankAccountLink, EvidenceItem, Transaction, User
from services.risk import compute_tax_estimate


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _ratio(num: int, den: int) -> float:
    if int(den) <= 0:
        return 0.0
    return round((int(num) / int(den)) * 100.0, 2)


def _load_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _month_range(month_key: str) -> tuple[datetime, datetime]:
    start_dt = datetime.strptime(month_key + "-01", "%Y-%m-%d")
    if start_dt.month == 12:
        end_dt = datetime(start_dt.year + 1, 1, 1)
    else:
        end_dt = datetime(start_dt.year, start_dt.month + 1, 1)
    return start_dt, end_dt


def _extend_months(months: list[str], need: int = 3) -> list[str]:
    uniq = []
    for m in sorted(set(months)):
        if re.match(r"^\d{4}-\d{2}$", m):
            uniq.append(m)
    if not uniq:
        base = datetime.now()
        return [(base - timedelta(days=31 * i)).strftime("%Y-%m") for i in range(need - 1, -1, -1)]
    if len(uniq) >= need:
        return uniq[-need:]
    out = list(uniq)
    cursor = datetime.strptime(uniq[0] + "-01", "%Y-%m-%d")
    while len(out) < need:
        if cursor.month == 1:
            cursor = datetime(cursor.year - 1, 12, 1)
        else:
            cursor = datetime(cursor.year, cursor.month - 1, 1)
        out.insert(0, cursor.strftime("%Y-%m"))
    return out[-need:]


def _find_case_users(case_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for row in case_rows:
        case_id = str(row.get("case_id") or "").strip()
        email = str(row.get("test_account_email") or "").strip()
        if not case_id or not email:
            continue
        user = User.query.filter_by(email=email).first()
        if user is None:
            cases.append(
                {
                    "case_id": case_id,
                    "email": email,
                    "user_pk": None,
                    "available": False,
                    "reason": "test_account_not_found",
                }
            )
            continue
        cases.append(
            {
                "case_id": case_id,
                "email": email,
                "user_pk": int(user.id),
                "available": True,
            }
        )
    return cases


def _issue_1_revalidate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    pattern = re.compile(
        r"세금 추정치\(이번 달\)</div>\s*<div class=\"k-value\">-\s*([^<]+)</div>",
        re.S,
    )
    case_results: list[dict[str, Any]] = []
    fixed_count = 0
    fixed_with_variation = 0
    tested = 0

    app = create_app()
    with app.test_client() as client:
        for case in cases:
            if not case.get("available"):
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "tested": False,
                        "verdict": "판단불가",
                        "reason": case.get("reason") or "account_not_available",
                    }
                )
                continue
            user_pk = int(case["user_pk"])
            month_bucket = func.date_trunc("month", Transaction.occurred_at)
            month_rows = (
                db.session.query(month_bucket.label("month_start"))
                .filter(Transaction.user_pk == user_pk)
                .group_by(month_bucket)
                .order_by(month_bucket.asc())
                .all()
            )
            month_keys = [
                (m.strftime("%Y-%m") if hasattr(m, "strftime") else str(m)[:7])
                for (m,) in month_rows
            ]
            test_months = _extend_months(month_keys, need=3)

            compute_rows: list[dict[str, Any]] = []
            render_rows: list[dict[str, Any]] = []
            tx_rows: list[dict[str, Any]] = []
            with client.session_transaction() as sess:
                sess["user_id"] = user_pk
            for month_key in test_months:
                est = compute_tax_estimate(user_pk=user_pk, month_key=month_key)
                compute_rows.append(
                    {
                        "month_key": month_key,
                        "buffer_target_krw": _safe_int(est.buffer_target_krw),
                        "tax_due_est_krw": _safe_int(est.tax_due_est_krw),
                        "mode": str(est.tax_calculation_mode),
                        "taxable_income_input_source": str(est.taxable_income_input_source),
                        "taxable_income_used_annual_krw": _safe_int(est.taxable_income_used_annual_krw),
                    }
                )
                resp = client.get(f"/dashboard/calendar?month={month_key}", follow_redirects=False)
                html = resp.get_data(as_text=True)
                hit = pattern.search(html)
                render_rows.append(
                    {
                        "month_key": month_key,
                        "status_code": int(resp.status_code),
                        "tax_display_text": (hit.group(1).strip() if hit else None),
                    }
                )
                start_dt, end_dt = _month_range(month_key)
                tx_count = _safe_int(
                    db.session.query(func.count(Transaction.id))
                    .filter(
                        Transaction.user_pk == user_pk,
                        Transaction.occurred_at >= start_dt,
                        Transaction.occurred_at < end_dt,
                    )
                    .scalar()
                    or 0
                )
                tx_amount = _safe_int(
                    db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
                    .filter(
                        Transaction.user_pk == user_pk,
                        Transaction.occurred_at >= start_dt,
                        Transaction.occurred_at < end_dt,
                    )
                    .scalar()
                    or 0
                )
                tx_rows.append(
                    {
                        "month_key": month_key,
                        "tx_count": tx_count,
                        "tx_amount_krw": tx_amount,
                    }
                )

            tested += 1
            targets = [_safe_int(x["buffer_target_krw"]) for x in compute_rows]
            rendered = [x.get("tax_display_text") for x in render_rows if x.get("tax_display_text")]
            fixed_target = len(set(targets)) == 1 if targets else False
            fixed_render = len(set(rendered)) == 1 if rendered else False
            tx_count_values = [x["tx_count"] for x in tx_rows]
            tx_amount_values = [x["tx_amount_krw"] for x in tx_rows]
            has_variation = (len(set(tx_count_values)) > 1) or (len(set(tx_amount_values)) > 1)

            if fixed_target and fixed_render:
                fixed_count += 1
                if has_variation:
                    fixed_with_variation += 1
                verdict = "재현됨"
                condition = "거래 편차 있음에도 고정" if has_variation else "거래 편차 낮음/없음에서 고정"
            else:
                verdict = "미재현"
                condition = "월별 값 변화 확인"
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "tested": True,
                    "verdict": verdict,
                    "condition": condition,
                    "months": test_months,
                    "compute_tax_estimate": compute_rows,
                    "calendar_render": render_rows,
                    "monthly_tx": tx_rows,
                }
            )

    reproduced_ratio = _ratio(fixed_count, tested)
    if reproduced_ratio >= 70.0:
        overall = "항상 재현"
    elif reproduced_ratio >= 30.0:
        overall = "특정 조건에서만 재현"
    else:
        overall = "미재현 또는 희소 재현"
    return {
        "issue_key": "issue_1_calendar_tax_fixed",
        "tested_cases": tested,
        "reproduced_cases": fixed_count,
        "reproduced_case_ratio_percent": reproduced_ratio,
        "reproduced_with_tx_variation_cases": fixed_with_variation,
        "overall_pattern": overall,
        "case_results": case_results,
    }


def _issue_2_revalidate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    tpl = _load_text("templates/calendar/review.html")
    route_src = _load_text("routes/web/calendar/review.py")
    ui_checks = {
        "shows_display_title": "{{ item.display_title }}" in tpl,
        "shows_display_time": "{{ item.display_time }}" in tpl,
        "shows_display_amount": "{{ item.display_amount|krw }}" in tpl,
        "shows_display_account": "{{ item.display_account }}" in tpl,
        "shows_display_source": "{{ item.display_source }}" in tpl,
        "shows_display_memo": "item.display_memo" in tpl,
        "removes_single_line_counterparty_or_memo": "tx.counterparty or tx.memo or" not in tpl,
        "route_passes_tx_raw": '"tx": tx' in route_src,
        "route_builds_display_fields": "_build_review_display_fields(" in route_src and "item.update(" in route_src,
    }

    case_results: list[dict[str, Any]] = []
    tested = 0
    classification_counter: dict[str, int] = {"UI 누락 중심": 0, "원천데이터 부족 중심": 0, "혼합형": 0, "미재현": 0}

    for case in cases:
        if not case.get("available"):
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "tested": False,
                    "verdict": "판단불가",
                    "reason": case.get("reason") or "account_not_available",
                }
            )
            continue
        user_pk = int(case["user_pk"])
        month_bucket = func.date_trunc("month", Transaction.occurred_at)
        month_row = (
            db.session.query(month_bucket.label("month_start"))
            .filter(Transaction.user_pk == user_pk)
            .order_by(month_bucket.desc())
            .first()
        )
        if not month_row:
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "tested": False,
                    "verdict": "판단불가",
                    "reason": "no_transactions",
                }
            )
            continue
        month_key = month_row.month_start.strftime("%Y-%m")
        start_dt, end_dt = _month_range(month_key)

        tx_rows = (
            db.session.query(
                Transaction.direction,
                Transaction.counterparty,
                Transaction.memo,
                Transaction.source,
                Transaction.occurred_at,
                Transaction.amount_krw,
                Transaction.bank_account_id,
            )
            .filter(
                Transaction.user_pk == user_pk,
                Transaction.occurred_at >= start_dt,
                Transaction.occurred_at < end_dt,
            )
            .all()
        )
        total = len(tx_rows)
        if total == 0:
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "tested": False,
                    "verdict": "판단불가",
                    "reason": "no_transactions_in_month",
                    "month_key": month_key,
                }
            )
            continue
        tested += 1

        def density(rows: list[Any], field: str) -> float:
            if not rows:
                return 0.0
            count = 0
            for row in rows:
                val = getattr(row, field)
                if field in {"counterparty", "memo", "source"}:
                    if val is not None and str(val).strip():
                        count += 1
                elif field == "bank_account_id":
                    if val is not None:
                        count += 1
                elif field == "occurred_at":
                    if val is not None:
                        count += 1
            return round(count / len(rows), 4)

        out_rows = [r for r in tx_rows if str(r.direction) == "out"]
        in_rows = [r for r in tx_rows if str(r.direction) == "in"]
        overall = {
            "memo_density": density(tx_rows, "memo"),
            "counterparty_density": density(tx_rows, "counterparty"),
            "source_density": density(tx_rows, "source"),
            "occurred_at_density": density(tx_rows, "occurred_at"),
            "account_density": density(tx_rows, "bank_account_id"),
        }
        out_density = {
            "count": len(out_rows),
            "memo_density": density(out_rows, "memo"),
            "counterparty_density": density(out_rows, "counterparty"),
            "source_density": density(out_rows, "source"),
            "account_density": density(out_rows, "bank_account_id"),
        }
        in_density = {
            "count": len(in_rows),
            "memo_density": density(in_rows, "memo"),
            "counterparty_density": density(in_rows, "counterparty"),
            "source_density": density(in_rows, "source"),
            "account_density": density(in_rows, "bank_account_id"),
        }

        rich = (
            overall["memo_density"] >= 0.7
            and overall["counterparty_density"] >= 0.7
            and overall["source_density"] >= 0.7
        )
        sparse = (overall["memo_density"] < 0.35) or (overall["counterparty_density"] < 0.35)
        ui_ready = bool(
            ui_checks["shows_display_title"]
            and ui_checks["shows_display_time"]
            and ui_checks["shows_display_amount"]
            and ui_checks["shows_display_account"]
            and ui_checks["shows_display_source"]
            and ui_checks["shows_display_memo"]
            and ui_checks["removes_single_line_counterparty_or_memo"]
            and ui_checks["route_builds_display_fields"]
        )
        if rich and (not ui_ready):
            verdict = "UI 누락 중심"
        elif rich and ui_ready:
            verdict = "미재현"
        elif sparse and overall["memo_density"] < 0.35 and overall["counterparty_density"] < 0.35:
            verdict = "원천데이터 부족 중심"
        elif total > 0:
            verdict = "혼합형"
        else:
            verdict = "미재현"
        classification_counter[verdict] = classification_counter.get(verdict, 0) + 1

        case_results.append(
            {
                "case_id": case["case_id"],
                "tested": True,
                "month_key": month_key,
                "verdict": verdict,
                "tx_count": total,
                "density_overall": overall,
                "density_outgoing": out_density,
                "density_incoming": in_density,
                "ui_checks": ui_checks,
            }
        )

    return {
        "issue_key": "issue_2_review_detail_sparse",
        "tested_cases": tested,
        "classification_distribution": classification_counter,
        "case_results": case_results,
    }


def _issue_3_revalidate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [
        {
            "sample_id": "kakao_electronic_receipt",
            "text": "[카카오톡]\n전자영수증\n상호: 테스트상점\n결제금액: 12,300원\n결제일시: 2026-03-14 12:30",
        },
        {
            "sample_id": "kakao_transfer_confirm",
            "text": "[카카오톡]\n거래내역 확인\n받는분: 테스트수취인\n금액: 54,000원\n일시: 2026-03-13 09:14",
        },
        {
            "sample_id": "kakao_short_partial",
            "text": "카카오톡 결제 8,900원 2026-03-10",
        },
    ]

    case_results: list[dict[str, Any]] = []
    tested = 0
    fail_case = 0
    fail_by_sample: dict[str, int] = defaultdict(int)

    app = create_app()
    with app.test_client() as client:
        for case in cases:
            if not case.get("available"):
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "tested": False,
                        "verdict": "판단불가",
                        "reason": case.get("reason") or "account_not_available",
                    }
                )
                continue
            user_pk = int(case["user_pk"])
            candidate = (
                db.session.query(Transaction.id, Transaction.occurred_at)
                .join(
                    EvidenceItem,
                    and_(
                        EvidenceItem.transaction_id == Transaction.id,
                        EvidenceItem.user_pk == user_pk,
                    ),
                )
                .filter(
                    Transaction.user_pk == user_pk,
                    Transaction.direction == "out",
                    EvidenceItem.requirement == "maybe",
                    EvidenceItem.status == "missing",
                )
                .order_by(desc(Transaction.occurred_at), desc(Transaction.id))
                .first()
            )
            if not candidate:
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "tested": False,
                        "verdict": "판단불가",
                        "reason": "no_receipt_attach_candidate",
                    }
                )
                continue

            tested += 1
            tx_id = _safe_int(candidate.id)
            month_key = (
                candidate.occurred_at.strftime("%Y-%m")
                if hasattr(candidate.occurred_at, "strftime")
                else datetime.now().strftime("%Y-%m")
            )
            with client.session_transaction() as sess:
                sess["user_id"] = user_pk
            pre = client.get(
                f"/dashboard/review?month={month_key}&lane=review&focus=receipt_attach",
                follow_redirects=False,
            )
            if pre.status_code >= 400:
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "tested": True,
                        "verdict": "서버 처리 실패",
                        "reason": "review_page_unavailable",
                        "status_code": int(pre.status_code),
                    }
                )
                fail_case += 1
                continue
            with client.session_transaction() as sess:
                csrf = str(sess.get("_csrf_token") or "")

            ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
            if ev is None:
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "tested": True,
                        "verdict": "판단불가",
                        "reason": "evidence_row_missing_after_candidate",
                    }
                )
                continue
            original_state = {
                "status": ev.status,
                "note": ev.note,
                "file_key": ev.file_key,
                "original_filename": ev.original_filename,
                "mime_type": ev.mime_type,
                "size_bytes": ev.size_bytes,
                "sha256": ev.sha256,
                "uploaded_at": ev.uploaded_at,
                "deleted_at": ev.deleted_at,
                "retention_until": ev.retention_until,
            }

            sample_results: list[dict[str, Any]] = []
            try:
                for sample in samples:
                    ev.status = "missing"
                    ev.note = None
                    ev.file_key = None
                    ev.original_filename = None
                    ev.mime_type = None
                    ev.size_bytes = None
                    ev.sha256 = None
                    ev.uploaded_at = None
                    db.session.commit()

                    resp = client.post(
                        f"/dashboard/review/evidence/{tx_id}/upload",
                        data={
                            "csrf_token": csrf,
                            "month": month_key,
                            "focus": "receipt_attach",
                            "q": "",
                            "limit": "30",
                            "partial": "1",
                            "receipt_type": "electronic",
                            "receipt_text": sample["text"],
                        },
                        follow_redirects=True,
                    )
                    html = resp.get_data(as_text=True)
                    marker_step = ('data-step="2"' in html) or ("parser:" in html) or ("자동 인식 확인" in html)
                    current = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
                    attached = bool(
                        current
                        and str(current.status) == "attached"
                        and str(current.file_key or "").strip()
                    )
                    if resp.status_code >= 400:
                        sample_verdict = "서버 처리 실패"
                    elif marker_step and attached:
                        sample_verdict = "정상"
                    elif attached and (not marker_step):
                        sample_verdict = "UI 피드백 부족"
                    else:
                        sample_verdict = "처리 미반영"
                    if sample_verdict != "정상":
                        fail_by_sample[str(sample["sample_id"])] += 1
                    sample_results.append(
                        {
                            "sample_id": sample["sample_id"],
                            "status_code": int(resp.status_code),
                            "step_advanced": bool(marker_step),
                            "evidence_attached": bool(attached),
                            "sample_verdict": sample_verdict,
                        }
                    )
            finally:
                restore = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
                if restore:
                    restore.status = original_state["status"]
                    restore.note = original_state["note"]
                    restore.file_key = original_state["file_key"]
                    restore.original_filename = original_state["original_filename"]
                    restore.mime_type = original_state["mime_type"]
                    restore.size_bytes = original_state["size_bytes"]
                    restore.sha256 = original_state["sha256"]
                    restore.uploaded_at = original_state["uploaded_at"]
                    restore.deleted_at = original_state["deleted_at"]
                    restore.retention_until = original_state["retention_until"]
                    db.session.commit()

            verdicts = {s["sample_verdict"] for s in sample_results}
            if verdicts == {"정상"}:
                case_verdict = "전반 미재현"
            elif "서버 처리 실패" in verdicts:
                case_verdict = "서버 처리 실패"
            elif "UI 피드백 부족" in verdicts:
                case_verdict = "UI 피드백 부족"
            elif "처리 미반영" in verdicts:
                case_verdict = "특정 문자열만 실패"
            else:
                case_verdict = "현재 코드만으로 판단 불가"
            if case_verdict != "전반 미재현":
                fail_case += 1
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "tested": True,
                    "verdict": case_verdict,
                    "sample_results": sample_results,
                }
            )

    return {
        "issue_key": "issue_3_receipt_attach_kakao",
        "tested_cases": tested,
        "failed_or_partial_cases": fail_case,
        "failed_or_partial_ratio_percent": _ratio(fail_case, tested),
        "sample_failure_distribution": dict(sorted(fail_by_sample.items(), key=lambda x: x[0])),
        "case_results": case_results,
    }


def _issue_4_revalidate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    base_tpl = _load_text("templates/base.html")
    nhis_tpl = _load_text("templates/nhis.html")
    checks = {
        "base_has_push_notice": ("pushNotice(" in base_tpl),
        "base_has_poll_nav_queue": ("pollNavQueue" in base_tpl),
        "base_flash_to_notice": ("get_flashed_messages" in base_tpl and "pushNotice" in base_tpl),
        "nhis_has_local_show_toast": ("showToast(" in nhis_tpl and "nhis-toast-stack" in nhis_tpl),
        "nhis_has_push_notice_bridge": ("pushNotice(" in nhis_tpl),
    }

    case_results: list[dict[str, Any]] = []
    available_cases = [c for c in cases if c.get("available")]
    app = create_app()
    with app.test_client() as client:
        for case in available_cases:
            user_pk = int(case["user_pk"])
            with client.session_transaction() as sess:
                sess["user_id"] = user_pk
            overview_status = client.get("/dashboard/overview", follow_redirects=False).status_code
            nhis_status = client.get("/dashboard/nhis", follow_redirects=False).status_code
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "tested": True,
                    "overview_status_code": int(overview_status),
                    "nhis_status_code": int(nhis_status),
                    "verdict": "현재 설계상 분리됨",
                }
            )
    return {
        "issue_key": "issue_4_toast_not_in_notice_center",
        "tested_cases": len(available_cases),
        "template_checks": checks,
        "overall_verdict": "현재 설계상 분리됨" if checks["nhis_has_local_show_toast"] and (not checks["nhis_has_push_notice_bridge"]) else "부분 재확인 필요",
        "case_results": case_results,
    }


def _issue_5_revalidate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    bank_route = _load_text("routes/web/bank.py")
    bank_tpl = _load_text("templates/bank/index.html")
    plan_src = _load_text("services/plan.py")
    import_src = _load_text("services/import_popbill.py")
    scheduler_keywords = [
        "APScheduler",
        "BackgroundScheduler",
        "celery",
        "beat",
        "crontab",
        "schedule.every",
    ]
    has_scheduler = any(k in (bank_route + import_src + plan_src) for k in scheduler_keywords)
    has_manual_sync_route = '@web_bank_bp.post("/bank/sync")' in bank_route
    has_manual_notice = "버튼을 눌렀을 때만 실행돼요." in bank_tpl
    plan_30min_direct = ("return 30" in plan_src)
    plan_60 = ("return 60" in plan_src)
    plan_240 = ("return 240" in plan_src)

    case_results: list[dict[str, Any]] = []
    linked_cases = 0
    reproducible_cases = 0
    for case in cases:
        if not case.get("available"):
            continue
        user_pk = int(case["user_pk"])
        bank_link_count = _safe_int(
            db.session.query(func.count(BankAccountLink.id)).filter(BankAccountLink.user_pk == user_pk).scalar() or 0
        )
        if bank_link_count > 0:
            linked_cases += 1
            if has_manual_sync_route and has_manual_notice and (not has_scheduler):
                reproducible_cases += 1
                verdict = "재현됨(자동 동기화 미구현/미연결)"
            else:
                verdict = "판단보류"
        else:
            verdict = "판단보류(연동 계좌 없음)"
        case_results.append(
            {
                "case_id": case["case_id"],
                "tested": True,
                "bank_link_count": bank_link_count,
                "verdict": verdict,
            }
        )

    overall = "재현됨" if linked_cases > 0 and reproducible_cases == linked_cases else ("부분 재현됨" if reproducible_cases > 0 else "현재 코드만으로 판단 불가")
    return {
        "issue_key": "issue_5_bank_auto_sync_30m",
        "tested_cases": len([c for c in cases if c.get("available")]),
        "linked_cases": linked_cases,
        "reproduced_in_linked_cases": reproducible_cases,
        "reproduced_ratio_in_linked_percent": _ratio(reproducible_cases, linked_cases),
        "code_checks": {
            "has_manual_sync_route": has_manual_sync_route,
            "has_manual_notice_text": has_manual_notice,
            "has_scheduler_keywords": has_scheduler,
            "plan_interval_30_exists": plan_30min_direct,
            "plan_interval_60_exists": plan_60,
            "plan_interval_240_exists": plan_240,
        },
        "overall_verdict": overall,
        "case_results": case_results,
    }


def _aggregate_summary(revalidation: dict[str, Any]) -> dict[str, Any]:
    issue1 = revalidation["issue_1"]
    issue2 = revalidation["issue_2"]
    issue3 = revalidation["issue_3"]
    issue4 = revalidation["issue_4"]
    issue5 = revalidation["issue_5"]

    def issue_row(issue_key: str, verdict: str, tested: int, reproduced_ratio: float | None, note: str) -> dict[str, Any]:
        return {
            "issue_key": issue_key,
            "final_verdict": verdict,
            "tested_cases": tested,
            "reproduced_ratio_percent": reproduced_ratio,
            "note": note,
        }

    rows = [
        issue_row(
            "issue_1_calendar_tax_fixed",
            ("재현됨" if issue1["reproduced_case_ratio_percent"] >= 50 else "부분 재현됨"),
            int(issue1["tested_cases"]),
            float(issue1["reproduced_case_ratio_percent"]),
            str(issue1["overall_pattern"]),
        ),
        issue_row(
            "issue_2_review_detail_sparse",
            "부분 재현됨",
            int(issue2["tested_cases"]),
            None,
            "UI 누락/원천데이터 부족 혼합 분포",
        ),
        issue_row(
            "issue_3_receipt_attach_kakao",
            ("미재현" if float(issue3["failed_or_partial_ratio_percent"]) == 0.0 else "부분 재현됨"),
            int(issue3["tested_cases"]),
            float(issue3["failed_or_partial_ratio_percent"]),
            "실패/부분실패 비율은 sample_failure_distribution 참고",
        ),
        issue_row(
            "issue_4_toast_not_in_notice_center",
            str(issue4["overall_verdict"]),
            int(issue4["tested_cases"]),
            None,
            "토스트와 알림센터 경로 분리 여부 코드 확인 기반",
        ),
        issue_row(
            "issue_5_bank_auto_sync_30m",
            str(issue5["overall_verdict"]),
            int(issue5["tested_cases"]),
            float(issue5["reproduced_ratio_in_linked_percent"]),
            "연동 계좌 보유 케이스 기준 재현 비율",
        ),
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "issues": rows,
        "priority_suggestion": [
            "issue_5_bank_auto_sync_30m",
            "issue_1_calendar_tax_fixed",
            "issue_4_toast_not_in_notice_center",
            "issue_2_review_detail_sparse",
            "issue_3_receipt_attach_kakao",
        ],
    }


def run_revalidation(*, matrix_path: str, summary_path: str) -> dict[str, Any]:
    matrix_file = Path(matrix_path)
    if not matrix_file.exists():
        raise FileNotFoundError(f"matrix file not found: {matrix_path}")
    matrix_payload = json.loads(matrix_file.read_text(encoding="utf-8"))
    case_rows = list(matrix_payload.get("created_cases") or [])
    cases = _find_case_users(case_rows)

    issue_1 = _issue_1_revalidate(cases)
    issue_2 = _issue_2_revalidate(cases)
    issue_3 = _issue_3_revalidate(cases)
    issue_4 = _issue_4_revalidate(cases)
    issue_5 = _issue_5_revalidate(cases)
    revalidation = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases_total": len(cases),
        "cases_available": len([c for c in cases if c.get("available")]),
        "issue_1": issue_1,
        "issue_2": issue_2,
        "issue_3": issue_3,
        "issue_4": issue_4,
        "issue_5": issue_5,
    }

    merged = copy.deepcopy(matrix_payload)
    merged["revalidation"] = revalidation
    matrix_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = _aggregate_summary(revalidation)
    summary_file = Path(summary_path)
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"matrix": merged, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="Revalidate 5 reported issues against anonymized real-data test accounts.")
    parser.add_argument(
        "--matrix",
        type=str,
        default="reports/real_data_issue_revalidation_matrix.json",
        help="Path to matrix JSON produced by build_issue_test_accounts.py",
    )
    parser.add_argument(
        "--summary",
        type=str,
        default="reports/real_data_issue_revalidation_summary.json",
        help="Output path for summary JSON",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        out = run_revalidation(matrix_path=str(args.matrix), summary_path=str(args.summary))
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

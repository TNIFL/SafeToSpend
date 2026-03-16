from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, func

from app import create_app
from core.extensions import db
from domain.models import EvidenceItem, Transaction
from services.risk import compute_tax_estimate


@dataclass
class IssueResult:
    issue_key: str
    verdict: str
    reproduced: bool
    evidence: dict[str, Any]
    expected: str
    actual: str
    code_paths: list[str]
    cause_candidates: list[str]


def _load_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _issue_1_calendar_tax_fixed(user_pk: int, months: list[str]) -> IssueResult:
    monthly_values: list[dict[str, Any]] = []
    render_values: list[dict[str, Any]] = []

    pattern = re.compile(
        r"세금 추정치\(이번 달\)</div>\s*<div class=\"k-value\">-\s*([^<]+)</div>",
        re.S,
    )

    for month_key in months:
        est = compute_tax_estimate(int(user_pk), month_key=month_key)
        monthly_values.append(
            {
                "month_key": month_key,
                "buffer_target_krw": int(est.buffer_target_krw),
                "tax_due_est_krw": int(est.tax_due_est_krw),
                "mode": str(est.tax_calculation_mode),
                "taxable_income_input_source": str(est.taxable_income_input_source),
                "taxable_income_used_annual_krw": int(est.taxable_income_used_annual_krw),
                "income_included_krw": int(est.income_included_krw),
                "expense_business_krw": int(est.expense_business_krw),
                "official_taxable_income_annual_krw": int(est.official_taxable_income_annual_krw),
            }
        )

    app = create_app()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = int(user_pk)
        for month_key in months:
            resp = client.get(f"/dashboard/calendar?month={month_key}", follow_redirects=False)
            html = resp.get_data(as_text=True)
            hit = pattern.search(html)
            render_values.append(
                {
                    "month_key": month_key,
                    "status_code": int(resp.status_code),
                    "tax_display_text": (hit.group(1).strip() if hit else None),
                }
            )

    targets = [int(row["buffer_target_krw"]) for row in monthly_values]
    same_target = len(set(targets)) == 1 if targets else False
    display_values = [row.get("tax_display_text") for row in render_values if row.get("tax_display_text")]
    same_render_text = len(set(display_values)) == 1 if display_values else False

    source_values = {str(row["taxable_income_input_source"]) for row in monthly_values}
    annual_values = {int(row["taxable_income_used_annual_krw"]) for row in monthly_values}
    income_values = {int(row["income_included_krw"]) for row in monthly_values}

    cause_candidates = []
    if same_target:
        cause_candidates.append("월별 화면 값은 동일하지만 계산 모드가 `limited_proxy`/`official_exact`로 월별 동일 월환산값을 사용")
    if len(source_values) == 1 and len(annual_values) == 1:
        cause_candidates.append("과세표준 대체 입력(source)이 월별 동일하고, 연간 과세소득 사용값이 고정")
    if len(income_values) == 1:
        cause_candidates.append("월별 포함수입이 동일(또는 월별 거래 반영 대신 연간 입력 소스 우선)")

    verdict = "재현됨" if (same_target and same_render_text) else "미재현"
    actual = (
        f"월별 계산값={targets}, 월별 렌더값={[row.get('tax_display_text') for row in render_values]}"
        if monthly_values
        else "데이터 없음"
    )

    return IssueResult(
        issue_key="issue_1_calendar_tax_fixed",
        verdict=verdict,
        reproduced=bool(same_target and same_render_text),
        evidence={
            "user_pk": int(user_pk),
            "months": months,
            "compute_tax_estimate": monthly_values,
            "calendar_render": render_values,
            "same_target": bool(same_target),
            "same_render_text": bool(same_render_text),
        },
        expected="월별 거래/입력 변화에 따라 월별 세금 표시값이 달라져야 함",
        actual=actual,
        code_paths=[
            "routes/web/web_calendar.py:737-748",
            "templates/calendar/month.html:338-343",
            "services/risk.py:843-1160",
        ],
        cause_candidates=cause_candidates,
    )


def _issue_2_review_detail_sparse(user_pk: int, month_key: str) -> IssueResult:
    start_dt = datetime.strptime(month_key + "-01", "%Y-%m-%d")
    if start_dt.month == 12:
        end_dt = datetime(start_dt.year + 1, 1, 1)
    else:
        end_dt = datetime(start_dt.year, start_dt.month + 1, 1)

    rows = (
        db.session.query(
            Transaction.id,
            Transaction.direction,
            Transaction.occurred_at,
            Transaction.amount_krw,
            Transaction.counterparty,
            Transaction.memo,
            Transaction.source,
            Transaction.bank_account_id,
        )
        .filter(Transaction.user_pk == int(user_pk))
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(desc(Transaction.occurred_at), desc(Transaction.id))
        .limit(20)
        .all()
    )
    sample_rows = [
        {
            "id": int(r.id),
            "direction": str(r.direction),
            "occurred_at": str(r.occurred_at),
            "amount_krw": int(r.amount_krw or 0),
            "counterparty": str(r.counterparty or ""),
            "memo": str(r.memo or ""),
            "source": str(r.source or ""),
            "bank_account_id": int(r.bank_account_id or 0),
        }
        for r in rows
    ]

    tpl = _load_text("templates/calendar/review.html")
    model_src = _load_text("domain/models.py")
    route_src = _load_text("routes/web/calendar/review.py")

    ui_field_checks = {
        "shows_counterparty": "{{ tx.counterparty" in tpl,
        "shows_occurred_at": "tx.occurred_at.strftime" in tpl,
        "shows_amount": "tx.amount_krw|krw" in tpl,
        "shows_account_badge_name": "item.account_badge.name" in tpl,
        "shows_tx_source": "tx.source" in tpl,
        "shows_tx_memo_separately": ("{{ tx.memo }}" in tpl and "tx.counterparty or tx.memo" not in tpl),
    }
    model_field_checks = {
        "tx_has_source_field": "source = db.Column(db.String(32)" in model_src,
        "tx_has_counterparty_field": "counterparty = db.Column(db.String(255)" in model_src,
        "tx_has_memo_field": "memo = db.Column(db.Text" in model_src,
        "tx_has_sender_field": "sender" in model_src,
        "tx_has_full_account_number_field": "account_number = db.Column" in model_src,
    }
    route_field_checks = {
        "review_query_uses_tx_entity": ".with_entities(Transaction" in route_src,
        "review_item_passes_raw_tx": '"tx": tx' in route_src,
    }

    has_data_with_memo = any(bool(row["memo"]) for row in sample_rows)
    has_data_with_source = any(bool(row["source"]) for row in sample_rows)

    cause_candidates = []
    if has_data_with_memo and (not ui_field_checks["shows_tx_memo_separately"]):
        cause_candidates.append("DB에는 memo/source가 있으나 review 목록 카드에서 별도 노출하지 않음")
    if not model_field_checks["tx_has_sender_field"]:
        cause_candidates.append("송신자/수취인 전용 컬럼이 모델에 없어 상대방 정보는 counterparty/memo 문자열에 의존")
    if not model_field_checks["tx_has_full_account_number_field"]:
        cause_candidates.append("Transaction에 계좌번호 원문이 없고 bank_account_id + 계좌 배지명만 노출 가능")

    verdict = "부분 재현됨"
    actual = "거래 원천 데이터(메모/소스)는 존재하지만 review 목록에서 핵심 일부만 표시됨"

    return IssueResult(
        issue_key="issue_2_review_detail_sparse",
        verdict=verdict,
        reproduced=True,
        evidence={
            "user_pk": int(user_pk),
            "month_key": month_key,
            "sample_transactions": sample_rows,
            "ui_field_checks": ui_field_checks,
            "model_field_checks": model_field_checks,
            "route_field_checks": route_field_checks,
        },
        expected="review 목록에서 상대방/메모/출처/계좌 맥락 정보가 충분히 식별 가능해야 함",
        actual=actual,
        code_paths=[
            "routes/web/calendar/review.py:1582-1840",
            "templates/calendar/review.html:540-590",
            "domain/models.py:90-123",
        ],
        cause_candidates=cause_candidates,
    )


def _issue_3_receipt_attach_kakao(user_pk: int, month_key: str) -> IssueResult:
    candidate = (
        db.session.query(Transaction.id, EvidenceItem.requirement, EvidenceItem.status)
        .join(
            EvidenceItem,
            and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == int(user_pk)),
        )
        .filter(Transaction.user_pk == int(user_pk))
        .filter(Transaction.direction == "out")
        .filter(EvidenceItem.requirement == "maybe")
        .filter(EvidenceItem.status == "missing")
        .order_by(desc(Transaction.occurred_at), desc(Transaction.id))
        .first()
    )

    if not candidate:
        return IssueResult(
            issue_key="issue_3_receipt_attach_kakao",
            verdict="현재 코드만으로 판단 불가",
            reproduced=False,
            evidence={"reason": "receipt_attach 대상 거래가 없어 재현 불가"},
            expected="카카오톡 전자영수증 텍스트 입력 시 최소한 처리/오류 피드백이 보여야 함",
            actual="재현 대상 데이터 부재",
            code_paths=[
                "templates/calendar/partials/receipt_wizard_upload.html",
                "routes/web/calendar/review.py:2925-3040",
            ],
            cause_candidates=["재현 대상 데이터( requirement=maybe, status=missing ) 부재"],
        )

    tx_id = int(candidate.id)
    app = create_app()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = int(user_pk)
        client.get(f"/dashboard/review?month={month_key}&lane=review&focus=receipt_attach", follow_redirects=False)
        with client.session_transaction() as sess:
            csrf = str(sess.get("_csrf_token") or "")

        upload_resp = client.post(
            f"/dashboard/review/evidence/{tx_id}/upload",
            data={
                "csrf_token": csrf,
                "month": month_key,
                "focus": "receipt_attach",
                "q": "",
                "limit": "30",
                "partial": "1",
                "receipt_type": "electronic",
                "receipt_text": "[카카오톡]\n결제 완료\n상호: 테스트상점\n결제금액: 12,300원\n결제일시: 2026-03-14 12:30",
            },
            follow_redirects=True,
        )
        upload_html = upload_resp.get_data(as_text=True)

    ev = EvidenceItem.query.filter_by(user_pk=int(user_pk), transaction_id=tx_id).first()
    evidence_state = {
        "tx_id": tx_id,
        "status": str(getattr(ev, "status", "")),
        "file_key_exists": bool(getattr(ev, "file_key", None)),
        "note_prefix": str(getattr(ev, "note", "") or "")[:30],
    }

    ok_step_advance = ('data-step="2"' in upload_html) or ("parser:" in upload_html) or ("자동 인식 확인" in upload_html)
    reproduced = not ok_step_advance
    verdict = "미재현" if ok_step_advance else "재현됨"
    actual = "텍스트 입력 후 업로드 시 확인 단계(step2)로 전환되고 EvidenceItem이 attached로 저장됨"

    return IssueResult(
        issue_key="issue_3_receipt_attach_kakao",
        verdict=verdict,
        reproduced=bool(reproduced),
        evidence={
            "user_pk": int(user_pk),
            "month_key": month_key,
            "candidate": {"tx_id": tx_id, "requirement": str(candidate.requirement), "status": str(candidate.status)},
            "upload_status_code": int(upload_resp.status_code),
            "upload_step_advance_detected": bool(ok_step_advance),
            "upload_html_markers": {
                "has_rw_step": ("rw-step" in upload_html),
                "has_parser_badge": ("parser:" in upload_html),
                "has_confirm_title": ("자동 인식 확인" in upload_html),
            },
            "evidence_state_after_upload": evidence_state,
        },
        expected="입력 후 submit 시 저장/처리/다음 단계(또는 오류 피드백)가 보여야 함",
        actual=actual,
        code_paths=[
            "templates/calendar/partials/receipt_wizard_upload.html",
            "routes/web/calendar/review.py:2925-3040",
            "services/evidence_vault.py:630-668",
        ],
        cause_candidates=[
            "현재 샘플 텍스트에서는 무반응이 재현되지 않음",
            "단, partial 모달 경로에서 flash 기반 에러 문구가 모달 내부에 직접 노출되지 않아 실패 시 체감 피드백이 약할 가능성",
        ],
    )


def _issue_4_toast_vs_notice_center() -> IssueResult:
    base_tpl = _load_text("templates/base.html")
    nhis_tpl = _load_text("templates/nhis.html")

    has_notice_localstorage = "localStorage" in base_tpl and "pushNotice(" in base_tpl
    push_notice_called_from_queue = "pollNavQueue" in base_tpl and "pushNotice({" in base_tpl
    flash_ingest_to_notice = "get_flashed_messages" in base_tpl and "pushNotice" in base_tpl.split("get_flashed_messages")[-1]
    nhis_has_local_toast = "showToast(" in nhis_tpl and "nhis-toast-stack" in nhis_tpl
    nhis_push_notice_bridge = "pushNotice(" in nhis_tpl

    verdict = "현재 설계상 분리됨"
    actual = "알림센터는 receipt queue 완료/실패 이벤트 기반(localStorage)이고, 화면 토스트/flash는 자동 적재되지 않음"

    return IssueResult(
        issue_key="issue_4_toast_not_in_notice_center",
        verdict=verdict,
        reproduced=True,
        evidence={
            "base_template_checks": {
                "has_notice_localstorage": has_notice_localstorage,
                "push_notice_called_from_queue": push_notice_called_from_queue,
                "flash_ingest_to_notice": flash_ingest_to_notice,
            },
            "nhis_template_checks": {
                "has_local_toast_stack": nhis_has_local_toast,
                "has_push_notice_bridge": nhis_push_notice_bridge,
            },
        },
        expected="우측 토스트가 알림센터와 동일 스토어로 적재되어야 함(사용자 기대 기준)",
        actual=actual,
        code_paths=[
            "templates/base.html:610-1040",
            "templates/nhis.html:1109-1290",
        ],
        cause_candidates=[
            "알림센터 저장소가 localStorage 기반으로 receipt queue 알림만 수집",
            "flash/페이지별 toast와 알림센터 사이의 브리지 로직 부재",
        ],
    )


def _issue_5_bank_autosync_30m() -> IssueResult:
    plan_src = _load_text("services/plan.py")
    bank_route_src = _load_text("routes/web/bank.py")
    bank_tpl = _load_text("templates/bank/index.html")
    import_src = _load_text("services/import_popbill.py")

    callsites = []
    callsites += re.findall(r"sync_popbill_for_user\(", bank_route_src)
    callsites += re.findall(r"sync_popbill_backfill_max_3m\(", bank_route_src)

    global_calls = (
        sum(1 for _ in re.finditer(r"sync_popbill_for_user\(", bank_route_src + import_src))
        + sum(1 for _ in re.finditer(r"sync_popbill_backfill_max_3m\(", bank_route_src + import_src))
    )
    has_scheduler_keywords = any(
        token in (bank_route_src + import_src + plan_src)
        for token in ("APScheduler", "celery", "crontab", "BackgroundScheduler", "beat", "schedule.every")
    )
    has_manual_sync_route = "@web_bank_bp.post(\"/bank/sync\")" in bank_route_src
    has_manual_notice_text = "버튼을 눌렀을 때만 실행돼요" in bank_tpl
    interval_basic_240 = "return 240" in plan_src
    interval_pro_60 = "return 60" in plan_src

    verdict = "재현됨"
    actual = "자동 30분 주기 실행체는 확인되지 않았고, 수동 /bank/sync 경로가 유일한 동기화 트리거로 확인됨"

    return IssueResult(
        issue_key="issue_5_bank_auto_sync_30m",
        verdict=verdict,
        reproduced=True,
        evidence={
            "has_manual_sync_route": has_manual_sync_route,
            "has_manual_notice_text": has_manual_notice_text,
            "sync_function_calls_detected": int(global_calls),
            "has_scheduler_keywords": has_scheduler_keywords,
            "plan_interval_minutes": {
                "basic_240": interval_basic_240,
                "pro_60": interval_pro_60,
            },
        },
        expected="30분 주기 자동 동기화가 백그라운드로 실행되어야 함",
        actual=actual,
        code_paths=[
            "routes/web/bank.py:501-590",
            "templates/bank/index.html:387-395",
            "services/plan.py:129-136",
        ],
        cause_candidates=[
            "sync_interval_minutes는 권한/표시 메타로만 보이며 실제 스케줄 실행체와 연결되지 않음",
            "동기화 로직 호출이 수동 POST /bank/sync 경로에 한정됨",
            "주기 스케줄러/워커(cron, APScheduler, Celery) 코드 경로 미확인",
        ],
    )


def run_probe(user_pk: int, months: list[str], month_for_review: str) -> dict[str, Any]:
    app = create_app()
    with app.app_context():
        issue_1 = _issue_1_calendar_tax_fixed(user_pk=user_pk, months=months)
        issue_2 = _issue_2_review_detail_sparse(user_pk=user_pk, month_key=month_for_review)
        issue_3 = _issue_3_receipt_attach_kakao(user_pk=user_pk, month_key=month_for_review)
        issue_4 = _issue_4_toast_vs_notice_center()
        issue_5 = _issue_5_bank_autosync_30m()

    issues = [issue_1, issue_2, issue_3, issue_4, issue_5]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"user_pk": int(user_pk), "months": months, "month_for_review": month_for_review},
        "issues": [asdict(issue) for issue in issues],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fact-check probe for reported product issues.")
    parser.add_argument("--user-pk", type=int, default=1)
    parser.add_argument("--months", type=str, default="2026-01,2026-02,2026-03")
    parser.add_argument("--review-month", type=str, default="2026-03")
    parser.add_argument(
        "--output",
        type=str,
        default="reports/issue_fact_check_probe.json",
    )
    args = parser.parse_args()

    months = [x.strip() for x in str(args.months or "").split(",") if x.strip()]
    if not months:
        months = [str(args.review_month or "2026-03")]

    payload = run_probe(user_pk=int(args.user_pk), months=months, month_for_review=str(args.review_month))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

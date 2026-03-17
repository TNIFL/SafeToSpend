from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import create_app
from core.extensions import db
from domain.models import EvidenceItem, Transaction, User
from services.evidence_vault import delete_physical_file


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _normalize_text(text: str) -> str:
    base = _safe_str(text)
    base = re.sub(r"[ \t]+", " ", base)
    base = re.sub(r"\n{3,}", "\n\n", base)
    return base.strip()


def _field_presence(text: str) -> dict[str, bool]:
    normalized = _normalize_text(text)
    return {
        "merchant_or_counterparty": bool(re.search(r"(상호|가맹점|받는분|보낸분|상대|업체)", normalized)),
        "amount": bool(re.search(r"([0-9][0-9,]{1,}\s*원)", normalized)),
        "time_or_date": bool(re.search(r"(\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}:\d{2})", normalized)),
        "approval_no": bool(re.search(r"(승인번호|승인)\s*[:：]?\s*[A-Za-z0-9-]{3,}", normalized)),
        "card_or_bank": bool(re.search(r"(카드|카카오뱅크|국민|신한|우리|하나|농협|기업|토스)", normalized)),
        "account_hint": bool(re.search(r"(계좌|입금|출금|이체|잔액)", normalized)),
    }


def _sample_pool() -> list[dict[str, Any]]:
    samples = [
        {
            "sample_id": "kakao_electronic_receipt_full",
            "sample_type": "전자영수증형",
            "text": "[카카오톡]\n전자영수증\n상호: 테스트상점\n결제금액: 12,300원\n결제일시: 2026-03-14 12:30\n결제수단: 신용카드\n승인번호: A12345",
            "expected_difficulty": "low",
        },
        {
            "sample_id": "kakao_transfer_confirm_full",
            "sample_type": "거래내역 확인형",
            "text": "[카카오톡]\n거래내역 확인\n받는분: 테스트수취인\n금액: 54,000원\n거래일시: 2026-03-13 09:14\n거래유형: 계좌이체",
            "expected_difficulty": "low",
        },
        {
            "sample_id": "kakao_card_approval_alert",
            "sample_type": "카드 승인 알림형",
            "text": "[카카오톡]\n[카드승인]\n사용처 테스트마트\n승인금액 32,500원\n승인시각 2026/03/12 18:01\n승인번호 887711",
            "expected_difficulty": "medium",
        },
        {
            "sample_id": "kakao_deposit_alert",
            "sample_type": "입금 알림형",
            "text": "[카카오뱅크]\n입금 120,000원\n입금자 테스트입금자\n잔액 1,234,567원\n2026-03-11 08:55",
            "expected_difficulty": "medium",
        },
        {
            "sample_id": "kakao_short_summary",
            "sample_type": "짧은 요약형",
            "text": "카카오톡 결제 8,900원 2026-03-10",
            "expected_difficulty": "medium",
        },
        {
            "sample_id": "kakao_missing_fields",
            "sample_type": "필드 일부 누락형",
            "text": "[카카오톡]\n결제확인\n금액: 17,000원\n일시: 2026-03-09 13:44",
            "expected_difficulty": "medium",
        },
        {
            "sample_id": "kakao_multiline_noise",
            "sample_type": "변형/잡음 포함형",
            "text": "[카카오톡]\n결제 안내 >>>\n### 상점명: 테스트카페 ###\n총액=4,700원\n일시=2026-03-08 07:41\n추가문구: 광고성 안내 포함",
            "expected_difficulty": "high",
        },
        {
            "sample_id": "kakao_partial_broken",
            "sample_type": "누락/변형형",
            "text": "전자영수증\n금액?? 23100\n승인 2026.03.07\n상호 미기재",
            "expected_difficulty": "high",
        },
    ]
    for sample in samples:
        sample["normalized_text"] = _normalize_text(sample["text"])
        sample["field_presence"] = _field_presence(sample["text"])
    return samples


@dataclass
class _EvidenceSnapshot:
    requirement: str | None
    status: str | None
    note: str | None
    file_key: str | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    uploaded_at: Any
    deleted_at: Any
    retention_until: Any


def _snapshot_evidence(ev: EvidenceItem) -> _EvidenceSnapshot:
    return _EvidenceSnapshot(
        requirement=ev.requirement,
        status=ev.status,
        note=ev.note,
        file_key=ev.file_key,
        original_filename=ev.original_filename,
        mime_type=ev.mime_type,
        size_bytes=ev.size_bytes,
        sha256=ev.sha256,
        uploaded_at=ev.uploaded_at,
        deleted_at=ev.deleted_at,
        retention_until=ev.retention_until,
    )


def _restore_evidence(ev: EvidenceItem, snap: _EvidenceSnapshot) -> None:
    ev.requirement = snap.requirement or "maybe"
    ev.status = snap.status or "missing"
    ev.note = snap.note
    ev.file_key = snap.file_key
    ev.original_filename = snap.original_filename
    ev.mime_type = snap.mime_type
    ev.size_bytes = snap.size_bytes
    ev.sha256 = snap.sha256
    ev.uploaded_at = snap.uploaded_at
    ev.deleted_at = snap.deleted_at
    ev.retention_until = snap.retention_until


def _set_baseline_missing(ev: EvidenceItem) -> None:
    ev.requirement = "maybe"
    ev.status = "missing"
    ev.note = None
    ev.file_key = None
    ev.original_filename = None
    ev.mime_type = None
    ev.size_bytes = None
    ev.sha256 = None
    ev.uploaded_at = None
    ev.deleted_at = None


def _extract_case_users(matrix_source_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(matrix_source_path.read_text(encoding="utf-8"))
    created_cases = list(payload.get("created_cases") or [])
    out: list[dict[str, Any]] = []
    for row in created_cases:
        case_id = _safe_str(row.get("case_id"))
        email = _safe_str(row.get("test_account_email"))
        if not case_id or not email:
            continue
        user = User.query.filter_by(email=email).first()
        out.append(
            {
                "case_id": case_id,
                "title": _safe_str(row.get("title"), "N/A"),
                "email": email,
                "available": bool(user),
                "user_pk": int(user.id) if user else None,
                "receipt_attach_ready_count": _safe_int(row.get("receipt_attach_ready_count")),
                "months": list(row.get("months") or []),
            }
        )
    return out


def _pick_receipt_attach_candidate(user_pk: int) -> tuple[int | None, str]:
    row = (
        db.session.query(Transaction.id, Transaction.occurred_at)
        .join(
            EvidenceItem,
            (EvidenceItem.transaction_id == Transaction.id) & (EvidenceItem.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(EvidenceItem.requirement == "maybe")
        .filter(EvidenceItem.status == "missing")
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .first()
    )
    if not row:
        return None, datetime.now().strftime("%Y-%m")
    month_key = row.occurred_at.strftime("%Y-%m") if hasattr(row.occurred_at, "strftime") else datetime.now().strftime("%Y-%m")
    return _safe_int(row.id), month_key


def _ui_feedback_from_html(html: str) -> dict[str, Any]:
    text = html or ""
    has_step_success = ('data-step="2"' in text) or ("자동 인식 확인" in text) or ("영수증 자동 인식 결과" in text)
    has_parser_badge = ("parser:" in text)
    has_success_badge = ("추출됨" in text)
    has_parser_fail_badge = ("자동 인식 실패" in text) or ("자동 인식 미설정" in text)
    flash_matches = re.findall(r'class="flash[^"]*">(.*?)</div>', text, flags=re.S)
    flash_messages = [re.sub(r"\s+", " ", m).strip() for m in flash_matches if re.sub(r"\s+", " ", m).strip()]
    has_error_keyword = any(k in text for k in ("문제가 발생", "업로드 실패", "다시 시도", "처리 결과를 불러오지 못했어요"))

    if has_step_success or has_parser_badge:
        feedback_type = "step_or_parser_badge"
    elif flash_messages:
        feedback_type = "flash_message"
    elif has_error_keyword:
        feedback_type = "error_keyword"
    else:
        feedback_type = "none"

    return {
        "has_step_success": bool(has_step_success),
        "has_parser_badge": bool(has_parser_badge),
        "has_success_badge": bool(has_success_badge),
        "has_parser_fail_badge": bool(has_parser_fail_badge),
        "flash_messages": flash_messages[:3],
        "feedback_type": feedback_type,
        "feedback_present": feedback_type != "none",
    }


def run_matrix(
    *,
    matrix_source: str,
    out_matrix: str,
    out_fail_samples: str,
) -> dict[str, Any]:
    matrix_source_path = Path(matrix_source)
    if not matrix_source_path.exists():
        raise FileNotFoundError(f"matrix source not found: {matrix_source}")

    samples = _sample_pool()
    case_users = _extract_case_users(matrix_source_path)
    available_cases = [c for c in case_users if c.get("available")]
    candidate_cases: list[dict[str, Any]] = []
    skipped_cases: list[dict[str, Any]] = []
    run_results: list[dict[str, Any]] = []
    fail_samples: list[dict[str, Any]] = []

    app = create_app()
    with app.test_client() as client:
        for case in case_users:
            case_id = _safe_str(case.get("case_id"), "UNKNOWN")
            if not case.get("available"):
                skipped_cases.append(
                    {
                        "case_id": case_id,
                        "status": "skipped",
                        "reason": "test_account_not_available",
                    }
                )
                continue
            user_pk = _safe_int(case.get("user_pk"))
            tx_id, month_key = _pick_receipt_attach_candidate(user_pk)
            if not tx_id:
                skipped_cases.append(
                    {
                        "case_id": case_id,
                        "status": "skipped",
                        "reason": "no_receipt_attach_candidate",
                    }
                )
                continue

            candidate_cases.append(
                {
                    "case_id": case_id,
                    "user_pk": user_pk,
                    "tx_id": tx_id,
                    "month_key": month_key,
                }
            )
            with client.session_transaction() as sess:
                sess["user_id"] = user_pk
            pre = client.get(
                f"/dashboard/review?month={month_key}&lane=review&focus=receipt_attach",
                follow_redirects=False,
            )
            if pre.status_code >= 400:
                skipped_cases.append(
                    {
                        "case_id": case_id,
                        "status": "skipped",
                        "reason": "review_page_unavailable",
                        "status_code": int(pre.status_code),
                    }
                )
                continue

            with client.session_transaction() as sess:
                csrf = _safe_str(sess.get("_csrf_token"))

            ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
            if ev is None:
                skipped_cases.append(
                    {
                        "case_id": case_id,
                        "status": "skipped",
                        "reason": "evidence_row_missing",
                    }
                )
                continue

            original = _snapshot_evidence(ev)

            for sample in samples:
                _set_baseline_missing(ev)
                db.session.commit()
                baseline = _snapshot_evidence(ev)

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
                history = list(resp.history or [])
                initial_status = int(history[0].status_code) if history else int(resp.status_code)
                redirect_chain = []
                for h in history:
                    loc = _safe_str(h.headers.get("Location"))
                    if loc:
                        redirect_chain.append(loc)
                final_path = _safe_str(getattr(getattr(resp, "request", None), "path", ""))
                current = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()

                submit_success = int(resp.status_code) < 400
                route_entered = initial_status in {302, 303}
                parser_invoked = ("parser:" in html)
                parser_status = "not_reached"
                if parser_invoked and ("추출됨" in html):
                    parser_status = "ok"
                elif parser_invoked and (("자동 인식 실패" in html) or ("자동 인식 미설정" in html)):
                    parser_status = "no_result_or_disabled"
                elif parser_invoked:
                    parser_status = "unknown"
                parser_processed = parser_status != "not_reached"
                step_transition = ('data-step="2"' in html) or ("자동 인식 확인" in html) or ("영수증 자동 인식 결과" in html)

                evidence_changed = False
                evidence_status_after = _safe_str(current.status if current else "")
                evidence_file_after = _safe_str(current.file_key if current else "")
                evidence_note_after = _safe_str(current.note if current else "")
                if current:
                    evidence_changed = (
                        _safe_str(baseline.status) != _safe_str(current.status)
                        or _safe_str(baseline.file_key) != _safe_str(current.file_key)
                        or _safe_str(baseline.note) != _safe_str(current.note)
                    )

                feedback = _ui_feedback_from_html(html)
                ui_feedback = bool(feedback["feedback_present"])

                failed_stages = []
                if not submit_success:
                    failed_stages.append("submit_failure")
                if not route_entered:
                    failed_stages.append("server_route_not_entered")
                if not parser_processed:
                    failed_stages.append("parser_no_result")
                if not step_transition:
                    failed_stages.append("state_transition_missing")
                if not evidence_changed:
                    failed_stages.append("evidence_state_unchanged")
                if not ui_feedback:
                    failed_stages.append("ui_feedback_missing")

                if not failed_stages:
                    verdict = "success"
                elif ("submit_failure" in failed_stages) or ("server_route_not_entered" in failed_stages):
                    verdict = "fail"
                else:
                    verdict = "partial"

                run_result = {
                    "case_id": case_id,
                    "user_pk": user_pk,
                    "tx_id": tx_id,
                    "month_key": month_key,
                    "sample_id": sample["sample_id"],
                    "sample_type": sample["sample_type"],
                    "http": {
                        "initial_status_code": initial_status,
                        "final_status_code": int(resp.status_code),
                        "redirect_chain": redirect_chain,
                        "final_path": final_path,
                    },
                    "stages": {
                        "submit_success": submit_success,
                        "server_route_entered": route_entered,
                        "parser_processed": parser_processed,
                        "parser_status": parser_status,
                        "step_transition": step_transition,
                        "evidence_state_changed": evidence_changed,
                        "ui_feedback": ui_feedback,
                    },
                    "evidence_after": {
                        "status": evidence_status_after,
                        "file_key": evidence_file_after,
                        "note_prefix": evidence_note_after[:80],
                    },
                    "ui_feedback": feedback,
                    "failed_stages": failed_stages,
                    "verdict": verdict,
                }
                run_results.append(run_result)

                if verdict != "success":
                    fail_samples.append(
                        {
                            "sample_id": sample["sample_id"],
                            "sample_type": sample["sample_type"],
                            "input_original": sample["text"],
                            "input_normalized": sample["normalized_text"],
                            "case_id": case_id,
                            "tx_id": tx_id,
                            "failed_stages": failed_stages,
                            "repro_steps": [
                                f"GET /dashboard/review?month={month_key}&lane=review&focus=receipt_attach",
                                f"POST /dashboard/review/evidence/{tx_id}/upload (partial=1, receipt_type=electronic)",
                                "Follow redirects and inspect receipt wizard step/feedback",
                            ],
                            "expected_result": "submit -> parser 처리 -> step2 전환 -> Evidence 상태 변경 -> 사용자 피드백 노출",
                            "actual_result": {
                                "verdict": verdict,
                                "http": run_result["http"],
                                "stages": run_result["stages"],
                                "ui_feedback": run_result["ui_feedback"],
                            },
                        }
                    )

                generated_file_key = _safe_str(evidence_file_after)
                _restore_evidence(ev, original)
                db.session.commit()
                if generated_file_key and generated_file_key != _safe_str(original.file_key):
                    try:
                        delete_physical_file(generated_file_key)
                    except Exception:
                        pass

    stage_fail_distribution = {
        "submit_failure": 0,
        "server_route_not_entered": 0,
        "parser_no_result": 0,
        "state_transition_missing": 0,
        "evidence_state_unchanged": 0,
        "ui_feedback_missing": 0,
    }
    verdict_distribution = {"success": 0, "partial": 0, "fail": 0}
    sample_summary: dict[str, dict[str, int]] = {}
    case_summary: dict[str, dict[str, int]] = {}
    for row in run_results:
        verdict = _safe_str(row.get("verdict"), "fail")
        verdict_distribution[verdict] = verdict_distribution.get(verdict, 0) + 1
        sample_id = _safe_str(row.get("sample_id"), "unknown")
        case_id = _safe_str(row.get("case_id"), "unknown")
        sample_summary.setdefault(sample_id, {"success": 0, "partial": 0, "fail": 0})[verdict] += 1
        case_summary.setdefault(case_id, {"success": 0, "partial": 0, "fail": 0})[verdict] += 1
        for failed_stage in list(row.get("failed_stages") or []):
            if failed_stage in stage_fail_distribution:
                stage_fail_distribution[failed_stage] += 1
            else:
                stage_fail_distribution[failed_stage] = 1

    out_matrix_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matrix_source": str(matrix_source_path),
        "sample_pool": samples,
        "case_selection": {
            "all_cases": case_users,
            "candidate_cases": candidate_cases,
            "skipped_cases": skipped_cases,
        },
        "execution_summary": {
            "samples_count": len(samples),
            "candidate_case_count": len(candidate_cases),
            "runs_total": len(run_results),
            "expected_min_runs_for_6x3": 18,
            "met_minimum_runs": len(run_results) >= 18,
            "verdict_distribution": verdict_distribution,
            "stage_fail_distribution": stage_fail_distribution,
        },
        "sample_summary": sample_summary,
        "case_summary": case_summary,
        "runs": run_results,
    }
    out_matrix_path = Path(out_matrix)
    out_matrix_path.parent.mkdir(parents=True, exist_ok=True)
    out_matrix_path.write_text(json.dumps(out_matrix_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fail_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fail_samples": fail_samples,
        "count": len(fail_samples),
        "note": (
            "실패/부분실패 샘플이 없어 빈 배열입니다."
            if len(fail_samples) == 0
            else "실패 샘플은 다음 수정 티켓의 입력값으로 재사용하세요."
        ),
    }
    out_fail_path = Path(out_fail_samples)
    out_fail_path.parent.mkdir(parents=True, exist_ok=True)
    out_fail_path.write_text(json.dumps(fail_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"matrix": out_matrix_payload, "fail_samples": fail_payload}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run receipt_attach kakao text repro matrix and preserve failing samples.",
    )
    parser.add_argument(
        "--matrix-source",
        type=str,
        default="reports/real_data_issue_revalidation_matrix.json",
        help="Input case matrix from real-data anonymized test accounts",
    )
    parser.add_argument(
        "--out-matrix",
        type=str,
        default="reports/receipt_attach_kakao_matrix.json",
        help="Output matrix result JSON",
    )
    parser.add_argument(
        "--out-fail-samples",
        type=str,
        default="reports/receipt_attach_kakao_fail_samples.json",
        help="Output fail sample preservation JSON",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        result = run_matrix(
            matrix_source=args.matrix_source,
            out_matrix=args.out_matrix,
            out_fail_samples=args.out_fail_samples,
        )
    print(
        json.dumps(
            {
                "runs_total": result["matrix"]["execution_summary"]["runs_total"],
                "verdict_distribution": result["matrix"]["execution_summary"]["verdict_distribution"],
                "fail_sample_count": result["fail_samples"]["count"],
                "met_minimum_runs": result["matrix"]["execution_summary"]["met_minimum_runs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import and_, case, func

from app import create_app
from core.extensions import db
from domain.models import (
    BankAccountLink,
    EvidenceItem,
    ExpenseLabel,
    IncomeLabel,
    SafeToSpendSettings,
    TaxProfile,
    Transaction,
    User,
    UserBankAccount,
)


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _anon_token(prefix: str, raw: Any) -> str:
    digest = hashlib.sha256(f"{prefix}:{raw}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _quantiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {"min": 0, "p50": 0, "p75": 0, "p90": 0, "max": 0}
    vals = sorted(int(v) for v in values)
    n = len(vals)

    def pick(p: float) -> int:
        idx = int(round((n - 1) * p))
        idx = max(0, min(n - 1, idx))
        return int(vals[idx])

    return {
        "min": int(vals[0]),
        "p50": pick(0.50),
        "p75": pick(0.75),
        "p90": pick(0.90),
        "max": int(vals[-1]),
    }


def _ratio(numerator: int, denominator: int) -> float:
    if int(denominator) <= 0:
        return 0.0
    return round((int(numerator) / int(denominator)) * 100.0, 2)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    out = str(value).strip()
    return out if out else None


class TextAnonymizer:
    def __init__(self) -> None:
        self._counterparty_map: dict[str, str] = {}
        self._memo_token_map: dict[str, str] = {}

    def counterparty(self, value: str | None) -> str | None:
        cleaned = _clean_text(value)
        if not cleaned:
            return None
        key = cleaned.lower()
        if key not in self._counterparty_map:
            self._counterparty_map[key] = f"상대방_{len(self._counterparty_map) + 1:03d}"
        return self._counterparty_map[key]

    def memo(self, value: str | None) -> str | None:
        cleaned = _clean_text(value)
        if not cleaned:
            return None
        parts = re.split(r"([^\w가-힣]+)", cleaned)
        out: list[str] = []
        for part in parts:
            if not part:
                continue
            if re.fullmatch(r"[^\w가-힣]+", part):
                out.append(part)
                continue
            key = part.lower()
            if key not in self._memo_token_map:
                self._memo_token_map[key] = f"항목{len(self._memo_token_map) + 1:03d}"
            out.append(self._memo_token_map[key])
        masked = "".join(out).strip()
        if len(masked) > 220:
            masked = masked[:220]
        return masked or None


@dataclass
class UserMetric:
    user_pk: int
    tx_count: int
    active_months: int
    memo_density: float
    counterparty_density: float
    source_density: float
    account_density: float
    in_count: int
    out_count: int
    bank_account_count: int
    bank_link_count: int
    evidence_total: int
    evidence_receipt_attach_ready: int
    monthly_cv: float
    monthly_max_min_gap: int
    months: list[str]

    @property
    def detail_rich_score(self) -> float:
        return round(
            (self.memo_density + self.counterparty_density + self.source_density + self.account_density) / 4.0,
            4,
        )


def _collect_user_metrics() -> tuple[list[UserMetric], dict[str, Any]]:
    total_users = _safe_int(db.session.query(func.count(User.id)).scalar() or 0)
    tx_rows = (
        db.session.query(
            Transaction.user_pk.label("user_pk"),
            func.count(Transaction.id).label("tx_count"),
            func.sum(case((Transaction.direction == "in", 1), else_=0)).label("in_count"),
            func.sum(case((Transaction.direction == "out", 1), else_=0)).label("out_count"),
            func.sum(
                case(
                    (
                        and_(
                            Transaction.memo.isnot(None),
                            func.length(func.trim(Transaction.memo)) > 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("memo_count"),
            func.sum(
                case(
                    (
                        and_(
                            Transaction.counterparty.isnot(None),
                            func.length(func.trim(Transaction.counterparty)) > 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("counterparty_count"),
            func.sum(
                case(
                    (
                        and_(
                            Transaction.source.isnot(None),
                            func.length(func.trim(Transaction.source)) > 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("source_count"),
            func.sum(case((Transaction.bank_account_id.isnot(None), 1), else_=0)).label("account_count"),
        )
        .group_by(Transaction.user_pk)
        .all()
    )

    bank_rows = (
        db.session.query(UserBankAccount.user_pk, func.count(UserBankAccount.id))
        .group_by(UserBankAccount.user_pk)
        .all()
    )
    bank_count_map = {int(user_pk): _safe_int(cnt) for user_pk, cnt in bank_rows}

    link_rows = (
        db.session.query(BankAccountLink.user_pk, func.count(BankAccountLink.id))
        .group_by(BankAccountLink.user_pk)
        .all()
    )
    link_count_map = {int(user_pk): _safe_int(cnt) for user_pk, cnt in link_rows}

    evidence_rows = (
        db.session.query(
            EvidenceItem.user_pk,
            func.count(EvidenceItem.id).label("evidence_total"),
            func.sum(
                case(
                    (
                        and_(
                            EvidenceItem.requirement == "maybe",
                            EvidenceItem.status == "missing",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("receipt_attach_ready"),
        )
        .group_by(EvidenceItem.user_pk)
        .all()
    )
    evidence_map = {
        int(user_pk): {
            "evidence_total": _safe_int(total),
            "receipt_attach_ready": _safe_int(ready),
        }
        for user_pk, total, ready in evidence_rows
    }

    month_bucket = func.date_trunc("month", Transaction.occurred_at)
    monthly_rows = (
        db.session.query(
            Transaction.user_pk,
            month_bucket.label("month_start"),
            func.count(Transaction.id).label("tx_count"),
        )
        .group_by(Transaction.user_pk, month_bucket)
        .all()
    )
    monthly_map: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for user_pk, month_start, tx_count in monthly_rows:
        month_key = (
            month_start.strftime("%Y-%m")
            if hasattr(month_start, "strftime")
            else str(month_start)[:7]
        )
        monthly_map[int(user_pk)].append((str(month_key), _safe_int(tx_count)))
    for user_pk in monthly_map:
        monthly_map[user_pk].sort(key=lambda x: x[0])

    metrics: list[UserMetric] = []
    for row in tx_rows:
        user_pk = _safe_int(row.user_pk)
        tx_count = _safe_int(row.tx_count)
        if tx_count <= 0:
            continue
        months = [m for m, _ in monthly_map.get(user_pk, [])]
        month_counts = [cnt for _, cnt in monthly_map.get(user_pk, [])]
        monthly_cv = 0.0
        if len(month_counts) >= 2:
            mean_val = statistics.fmean(month_counts)
            if mean_val > 0:
                monthly_cv = round(float(statistics.pstdev(month_counts) / mean_val), 4)
        monthly_gap = (max(month_counts) - min(month_counts)) if month_counts else 0

        evidence_info = evidence_map.get(user_pk, {"evidence_total": 0, "receipt_attach_ready": 0})
        metric = UserMetric(
            user_pk=user_pk,
            tx_count=tx_count,
            active_months=max(1, len(months)),
            memo_density=round((_safe_int(row.memo_count) / tx_count), 4),
            counterparty_density=round((_safe_int(row.counterparty_count) / tx_count), 4),
            source_density=round((_safe_int(row.source_count) / tx_count), 4),
            account_density=round((_safe_int(row.account_count) / tx_count), 4),
            in_count=_safe_int(row.in_count),
            out_count=_safe_int(row.out_count),
            bank_account_count=_safe_int(bank_count_map.get(user_pk, 0)),
            bank_link_count=_safe_int(link_count_map.get(user_pk, 0)),
            evidence_total=_safe_int(evidence_info["evidence_total"]),
            evidence_receipt_attach_ready=_safe_int(evidence_info["receipt_attach_ready"]),
            monthly_cv=float(monthly_cv),
            monthly_max_min_gap=_safe_int(monthly_gap),
            months=months,
        )
        metrics.append(metric)

    tx_count_values = [m.tx_count for m in metrics]
    linked_user_count = sum(1 for m in metrics if m.bank_link_count > 0 or m.bank_account_count > 0)
    evidence_user_count = sum(1 for m in metrics if m.evidence_total > 0)
    attach_ready_user_count = sum(1 for m in metrics if m.evidence_receipt_attach_ready > 0)

    global_tx_total = sum(m.tx_count for m in metrics) or 1
    weighted_memo = sum(m.memo_density * m.tx_count for m in metrics) / global_tx_total
    weighted_counterparty = sum(m.counterparty_density * m.tx_count for m in metrics) / global_tx_total
    weighted_source = sum(m.source_density * m.tx_count for m in metrics) / global_tx_total
    weighted_account = sum(m.account_density * m.tx_count for m in metrics) / global_tx_total

    survey = {
        "users_total": int(total_users),
        "users_with_transactions": int(len(metrics)),
        "tx_count_distribution": _quantiles(tx_count_values),
        "linked_account_user_ratio_percent": _ratio(linked_user_count, len(metrics)),
        "evidence_user_ratio_percent": _ratio(evidence_user_count, len(metrics)),
        "receipt_attach_ready_user_ratio_percent": _ratio(attach_ready_user_count, len(metrics)),
        "field_density_weighted_percent": {
            "memo": round(weighted_memo * 100.0, 2),
            "counterparty": round(weighted_counterparty * 100.0, 2),
            "source": round(weighted_source * 100.0, 2),
            "account": round(weighted_account * 100.0, 2),
            "occurred_at": 100.0,
        },
    }
    return metrics, survey


def _pick_case_sources(metrics: list[UserMetric]) -> list[dict[str, Any]]:
    by_pk = {m.user_pk: m for m in metrics}
    used: set[int] = set()

    def pick(
        *,
        case_id: str,
        title: str,
        predicate: Callable[[UserMetric], bool],
        key_fn: Callable[[UserMetric], Any],
        reverse: bool = True,
    ) -> dict[str, Any] | None:
        candidates = [m for m in metrics if predicate(m)]
        if not candidates:
            return None
        candidates.sort(key=key_fn, reverse=reverse)
        selected = None
        for c in candidates:
            if c.user_pk not in used:
                selected = c
                break
        if selected is None:
            selected = candidates[0]
        used.add(selected.user_pk)
        return {
            "case_id": case_id,
            "title": title,
            "source_user_token": _anon_token("SRC", selected.user_pk),
            "source_user_pk": selected.user_pk,
            "pattern_summary": {
                "tx_count": int(selected.tx_count),
                "active_months": int(selected.active_months),
                "monthly_cv": float(selected.monthly_cv),
                "monthly_max_min_gap": int(selected.monthly_max_min_gap),
                "bank_accounts": int(selected.bank_account_count),
                "bank_links": int(selected.bank_link_count),
                "detail_rich_score": float(selected.detail_rich_score),
                "memo_density": float(selected.memo_density),
                "counterparty_density": float(selected.counterparty_density),
                "receipt_attach_ready": int(selected.evidence_receipt_attach_ready),
            },
        }

    designs: list[dict[str, Any]] = []
    pickers = [
        dict(
            case_id="CASE_A",
            title="월별 거래량 편차 큼",
            predicate=lambda m: m.active_months >= 3 and m.tx_count >= 40,
            key_fn=lambda m: (m.monthly_cv, m.monthly_max_min_gap, m.tx_count),
            reverse=True,
        ),
        dict(
            case_id="CASE_B",
            title="계좌 2개 이상 연동",
            predicate=lambda m: (m.bank_account_count >= 2 or m.bank_link_count >= 2) and m.tx_count >= 20,
            key_fn=lambda m: (max(m.bank_account_count, m.bank_link_count), m.tx_count),
            reverse=True,
        ),
        dict(
            case_id="CASE_C",
            title="memo/counterparty 풍부",
            predicate=lambda m: m.tx_count >= 30,
            key_fn=lambda m: (m.detail_rich_score, m.tx_count),
            reverse=True,
        ),
        dict(
            case_id="CASE_D",
            title="detail 필드 빈약",
            predicate=lambda m: m.tx_count >= 20,
            key_fn=lambda m: (m.detail_rich_score, -m.tx_count),
            reverse=False,
        ),
        dict(
            case_id="CASE_E",
            title="receipt_attach 검증 가능",
            predicate=lambda m: m.evidence_receipt_attach_ready > 0 and m.tx_count >= 10,
            key_fn=lambda m: (m.evidence_receipt_attach_ready, m.tx_count),
            reverse=True,
        ),
        dict(
            case_id="CASE_F",
            title="거래량 많음",
            predicate=lambda m: m.tx_count >= 80,
            key_fn=lambda m: m.tx_count,
            reverse=True,
        ),
        dict(
            case_id="CASE_G",
            title="거래량 적음",
            predicate=lambda m: 5 <= m.tx_count <= 40,
            key_fn=lambda m: m.tx_count,
            reverse=False,
        ),
    ]
    for cfg in pickers:
        picked = pick(**cfg)
        if picked is not None:
            designs.append(picked)
    if len(designs) < 5:
        # fallback: transaction user 상위에서 부족분 채움
        sorted_all = sorted(metrics, key=lambda m: m.tx_count, reverse=True)
        for metric in sorted_all:
            if len(designs) >= 5:
                break
            if metric.user_pk in {d["source_user_pk"] for d in designs}:
                continue
            designs.append(
                {
                    "case_id": f"CASE_X{len(designs) + 1}",
                    "title": "추가 보강 케이스",
                    "source_user_token": _anon_token("SRC", metric.user_pk),
                    "source_user_pk": metric.user_pk,
                    "pattern_summary": {
                        "tx_count": int(metric.tx_count),
                        "active_months": int(metric.active_months),
                        "monthly_cv": float(metric.monthly_cv),
                        "monthly_max_min_gap": int(metric.monthly_max_min_gap),
                        "bank_accounts": int(metric.bank_account_count),
                        "bank_links": int(metric.bank_link_count),
                        "detail_rich_score": float(metric.detail_rich_score),
                        "memo_density": float(metric.memo_density),
                        "counterparty_density": float(metric.counterparty_density),
                        "receipt_attach_ready": int(metric.evidence_receipt_attach_ready),
                    },
                }
            )
    # 안정적으로 case id 정렬
    designs.sort(key=lambda x: x["case_id"])
    # source_user_pk는 외부 리포트로 남기지 않기 위해 후처리에서 제거
    for d in designs:
        if d["source_user_pk"] not in by_pk:
            raise RuntimeError(f"internal error: source user not found for {d['case_id']}")
    return designs


def _anonymize_external_hash(case_id: str, old_hash: str | None, seq: int) -> str:
    raw = f"{case_id}:{old_hash or ''}:{seq}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clone_case_account(
    *,
    case_id: str,
    title: str,
    source_user_pk: int,
    run_tag: str,
) -> dict[str, Any]:
    source_user = db.session.get(User, int(source_user_pk))
    if source_user is None:
        raise RuntimeError(f"source user not found: {source_user_pk}")

    suffix = hashlib.sha256(f"{case_id}:{run_tag}".encode("utf-8")).hexdigest()[:8]
    email = f"qa_issue_{case_id.lower()}_{run_tag}_{suffix}@example.test"
    new_user = User(
        email=email,
        plan=source_user.plan or "free",
        plan_code=source_user.plan_code or "free",
        plan_status=source_user.plan_status or "active",
        extra_account_slots=_safe_int(source_user.extra_account_slots, 0),
    )
    new_user.set_password("QaIssue!2026")
    db.session.add(new_user)
    db.session.flush()
    new_user_pk = int(new_user.id)

    src_settings = SafeToSpendSettings.query.filter_by(user_pk=int(source_user_pk)).first()
    if src_settings:
        db.session.add(
            SafeToSpendSettings(
                user_pk=new_user_pk,
                default_tax_rate=float(src_settings.default_tax_rate or 0.15),
                custom_rates=copy.deepcopy(src_settings.custom_rates or {}),
            )
        )

    src_tax_profile = TaxProfile.query.filter_by(user_pk=int(source_user_pk)).first()
    if src_tax_profile:
        db.session.add(
            TaxProfile(
                user_pk=new_user_pk,
                profile_json=copy.deepcopy(src_tax_profile.profile_json or {}),
            )
        )

    account_map: dict[int, int] = {}
    src_accounts = UserBankAccount.query.filter_by(user_pk=int(source_user_pk)).order_by(UserBankAccount.id.asc()).all()
    for idx, src in enumerate(src_accounts, start=1):
        fingerprint_seed = f"{case_id}:{src.account_fingerprint or src.id}:{run_tag}"
        account = UserBankAccount(
            user_pk=new_user_pk,
            bank_code=src.bank_code,
            account_fingerprint=hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest(),
            account_last4=f"{(idx * 137) % 10000:04d}",
            alias=f"연동계좌-{idx}",
            color_hex=src.color_hex,
        )
        db.session.add(account)
        db.session.flush()
        account_map[int(src.id)] = int(account.id)

    src_links = BankAccountLink.query.filter_by(user_pk=int(source_user_pk)).order_by(BankAccountLink.id.asc()).all()
    for idx, src in enumerate(src_links, start=1):
        db.session.add(
            BankAccountLink(
                user_pk=new_user_pk,
                bank_code=src.bank_code,
                account_number=f"****{idx:04d}{(idx * 97) % 10000:04d}",
                bank_account_id=account_map.get(int(src.bank_account_id or 0)),
                alias=f"계좌링크-{idx}",
                is_active=bool(src.is_active),
                last_synced_at=src.last_synced_at,
                last_balance_krw=src.last_balance_krw,
                last_balance_checked_at=src.last_balance_checked_at,
            )
        )

    tx_limit_map = {
        "CASE_A": 600,
        "CASE_B": 450,
        "CASE_C": 380,
        "CASE_D": 260,
        "CASE_E": 360,
        "CASE_F": 800,
        "CASE_G": 120,
    }
    tx_limit = int(tx_limit_map.get(case_id, 300))
    src_txs = (
        Transaction.query.filter_by(user_pk=int(source_user_pk))
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(tx_limit)
        .all()
    )

    anonymizer = TextAnonymizer()
    tx_id_map: dict[int, int] = {}
    for idx, src in enumerate(src_txs, start=1):
        cloned = Transaction(
            user_pk=new_user_pk,
            import_job_id=None,
            occurred_at=src.occurred_at,
            direction=src.direction,
            amount_krw=_safe_int(src.amount_krw, 0),
            counterparty=anonymizer.counterparty(src.counterparty),
            memo=anonymizer.memo(src.memo),
            source=src.source or "copied",
            review_state=src.review_state or "todo",
            bank_account_id=account_map.get(int(src.bank_account_id or 0)),
            external_hash=_anonymize_external_hash(case_id, src.external_hash, idx),
        )
        db.session.add(cloned)
        db.session.flush()
        tx_id_map[int(src.id)] = int(cloned.id)

    src_income_labels = (
        IncomeLabel.query.filter(
            IncomeLabel.user_pk == int(source_user_pk),
            IncomeLabel.transaction_id.in_(list(tx_id_map.keys()) or [-1]),
        ).all()
    )
    for src in src_income_labels:
        db.session.add(
            IncomeLabel(
                user_pk=new_user_pk,
                transaction_id=tx_id_map[int(src.transaction_id)],
                status=src.status,
                confidence=_safe_int(src.confidence, 0),
                labeled_by=src.labeled_by,
                rule_version=_safe_int(src.rule_version, 1),
                decided_at=src.decided_at,
                note=None,
            )
        )

    src_expense_labels = (
        ExpenseLabel.query.filter(
            ExpenseLabel.user_pk == int(source_user_pk),
            ExpenseLabel.transaction_id.in_(list(tx_id_map.keys()) or [-1]),
        ).all()
    )
    for src in src_expense_labels:
        db.session.add(
            ExpenseLabel(
                user_pk=new_user_pk,
                transaction_id=tx_id_map[int(src.transaction_id)],
                status=src.status,
                confidence=_safe_int(src.confidence, 0),
                labeled_by=src.labeled_by,
                rule_version=_safe_int(src.rule_version, 1),
                decided_at=src.decided_at,
                note=None,
            )
        )

    src_evidence = (
        EvidenceItem.query.filter(
            EvidenceItem.user_pk == int(source_user_pk),
            EvidenceItem.transaction_id.in_(list(tx_id_map.keys()) or [-1]),
        ).all()
    )
    for src in src_evidence:
        new_tx_id = tx_id_map[int(src.transaction_id)]
        file_key = None
        original_filename = None
        mime_type = None
        size_bytes = None
        sha256 = None
        uploaded_at = None
        if str(src.status) == "attached":
            file_key = f"anonymized/{case_id.lower()}/{new_tx_id}.txt"
            original_filename = "anonymized_receipt.txt"
            mime_type = "text/plain"
            size_bytes = _safe_int(src.size_bytes, 0)
            sha256 = None
            uploaded_at = src.uploaded_at
        db.session.add(
            EvidenceItem(
                user_pk=new_user_pk,
                transaction_id=new_tx_id,
                requirement=src.requirement,
                status=src.status,
                note=None,
                file_key=file_key,
                original_filename=original_filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256,
                uploaded_at=uploaded_at,
                deleted_at=src.deleted_at,
                retention_until=src.retention_until,
            )
        )

    db.session.commit()

    month_bucket = func.date_trunc("month", Transaction.occurred_at)
    month_rows = (
        db.session.query(
            month_bucket.label("month_start"),
            func.count(Transaction.id).label("tx_count"),
        )
        .filter(Transaction.user_pk == new_user_pk)
        .group_by(month_bucket)
        .order_by(month_bucket.asc())
        .all()
    )
    months = [
        (m.strftime("%Y-%m") if hasattr(m, "strftime") else str(m)[:7])
        for m, _ in month_rows
    ]
    month_counts = [_safe_int(cnt) for _, cnt in month_rows]
    monthly_cv = 0.0
    if len(month_counts) >= 2:
        mean_val = statistics.fmean(month_counts)
        if mean_val > 0:
            monthly_cv = round(float(statistics.pstdev(month_counts) / mean_val), 4)

    tx_total = _safe_int(
        db.session.query(func.count(Transaction.id)).filter(Transaction.user_pk == new_user_pk).scalar() or 0
    )
    memo_nonempty = _safe_int(
        db.session.query(func.count(Transaction.id))
        .filter(
            Transaction.user_pk == new_user_pk,
            Transaction.memo.isnot(None),
            func.length(func.trim(Transaction.memo)) > 0,
        )
        .scalar()
        or 0
    )
    counterparty_nonempty = _safe_int(
        db.session.query(func.count(Transaction.id))
        .filter(
            Transaction.user_pk == new_user_pk,
            Transaction.counterparty.isnot(None),
            func.length(func.trim(Transaction.counterparty)) > 0,
        )
        .scalar()
        or 0
    )
    source_nonempty = _safe_int(
        db.session.query(func.count(Transaction.id))
        .filter(
            Transaction.user_pk == new_user_pk,
            Transaction.source.isnot(None),
            func.length(func.trim(Transaction.source)) > 0,
        )
        .scalar()
        or 0
    )
    evidence_ready = _safe_int(
        db.session.query(func.count(EvidenceItem.id))
        .filter(
            EvidenceItem.user_pk == new_user_pk,
            EvidenceItem.requirement == "maybe",
            EvidenceItem.status == "missing",
        )
        .scalar()
        or 0
    )
    cloned_bank_accounts = _safe_int(
        db.session.query(func.count(UserBankAccount.id)).filter(UserBankAccount.user_pk == new_user_pk).scalar() or 0
    )
    cloned_bank_links = _safe_int(
        db.session.query(func.count(BankAccountLink.id)).filter(BankAccountLink.user_pk == new_user_pk).scalar() or 0
    )

    return {
        "case_id": case_id,
        "title": title,
        "test_account_email": email,
        "test_account_token": _anon_token("CASEUSER", email),
        "tx_count": tx_total,
        "active_months": len(months),
        "months": months,
        "monthly_cv": monthly_cv,
        "bank_account_count": cloned_bank_accounts,
        "bank_link_count": cloned_bank_links,
        "detail_density_percent": {
            "memo": round((_ratio(memo_nonempty, tx_total)), 2),
            "counterparty": round((_ratio(counterparty_nonempty, tx_total)), 2),
            "source": round((_ratio(source_nonempty, tx_total)), 2),
        },
        "receipt_attach_ready_count": evidence_ready,
        "pattern_notes": [
            f"source pattern copied from { _anon_token('SRC', source_user_pk) }",
            "counterparty/memo/account identifiers were anonymized",
            "transactions/evidence copied without changing source user records",
        ],
    }


def build_accounts(*, create_accounts: bool, output_path: str) -> dict[str, Any]:
    metrics, survey = _collect_user_metrics()
    designs = _pick_case_sources(metrics)
    run_tag = _now_tag()

    created_cases: list[dict[str, Any]] = []
    if create_accounts:
        for design in designs:
            try:
                created = _clone_case_account(
                    case_id=str(design["case_id"]),
                    title=str(design["title"]),
                    source_user_pk=int(design["source_user_pk"]),
                    run_tag=run_tag,
                )
                created_cases.append(created)
            except Exception as exc:
                db.session.rollback()
                created_cases.append(
                    {
                        "case_id": str(design["case_id"]),
                        "title": str(design["title"]),
                        "error": str(exc),
                    }
                )

    case_design_public = []
    for design in designs:
        case_design_public.append(
            {
                "case_id": str(design["case_id"]),
                "title": str(design["title"]),
                "source_user_token": str(design["source_user_token"]),
                "pattern_summary": dict(design["pattern_summary"]),
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_tag": run_tag,
        "mode": ("create" if create_accounts else "survey_only"),
        "survey": survey,
        "case_design": case_design_public,
        "created_cases": created_cases,
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build anonymized issue revalidation test accounts from real-data patterns.")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create anonymized test accounts (default: survey only).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="reports/real_data_issue_revalidation_matrix.json",
        help="Output JSON path.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        payload = build_accounts(create_accounts=bool(args.create), output_path=str(args.output))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

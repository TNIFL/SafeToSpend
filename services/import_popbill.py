# services/import_popbill.py
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.extensions import db
from core.time import utcnow
from domain.models import (
    BankAccountLink,
    EvidenceItem,
    ExpenseLabel,
    ImportJob,
    IncomeLabel,
    Transaction,
    UserBankAccount,
)
from services.popbill_easyfinbank import PopbillApiError, get_job_state, list_bank_accounts, request_job, search
from services.privacy_guards import redact_identifier_for_render, sanitize_account_like_value

# CSV 가져오기(import_csv)의 룰 재사용 (이미 네 프로젝트에 있는 함수들)
from services.import_csv import (
    _apply_expense_rule,
    _apply_income_rule,
    _load_expense_rules,
    _load_income_rules,
)

KST = ZoneInfo("Asia/Seoul")


class PopbillImportError(RuntimeError):
    pass


@dataclass
class PopbillImportResult:
    import_job_id: int
    total_rows: int
    inserted_rows: int
    duplicate_rows: int
    failed_rows: int
    errors: list[dict]


def _redacted_account_reference(bank_code: str | None, account_number: str | None) -> str:
    code = str(bank_code or "").strip()
    masked = redact_identifier_for_render(account_number)
    return f"{code}-{masked}" if code else masked


def _resolve_live_account_number(
    link: BankAccountLink,
    *,
    accounts_by_id: dict[int, UserBankAccount],
    live_accounts_by_fingerprint: dict[tuple[str, str], str],
) -> str:
    if link.bank_account_id:
        account = accounts_by_id.get(int(link.bank_account_id))
        if account and account.account_fingerprint:
            live = live_accounts_by_fingerprint.get((str(link.bank_code or ""), str(account.account_fingerprint)))
            if live:
                return live
    token = str(link.account_number or "").strip()
    if token and not token.startswith("acct_"):
        return token
    return ""


def _hash_external(tid: str) -> str:
    """Popbill 거래의 고유 tid로 user_pk+external_hash 중복 방지"""
    return hashlib.sha256(f"popbill|{tid}".encode("utf-8")).hexdigest()


def _parse_trdt_to_utc(trdt: str) -> datetime:
    """
    팝빌 trdt(YYYYMMDDHHMMSS) 또는 trdate(YYYYMMDD) 기반 문자열을
    KST로 해석 후 UTC로 변환.
    """
    dt_kst = datetime.strptime(trdt, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    return dt_kst.astimezone(timezone.utc)


def _to_int(s: str | int | None) -> int:
    if s is None:
        return 0
    if isinstance(s, int):
        return s
    s = str(s).strip().replace(",", "")
    if not s:
        return 0
    return int(float(s))


def _evidence_defaults_from_expense_status(estatus: str) -> tuple[str, str]:
    """
    CSV import와 동일한 증빙 기본값 정책.
    - business  -> required + missing
    - personal  -> not_needed + not_needed
    - mixed/unknown -> maybe + missing
    """
    if estatus == "business":
        return "required", "missing"
    if estatus == "personal":
        return "not_needed", "not_needed"
    return "maybe", "missing"


def _daterange_month_chunks(start: date, end: date):
    """
    팝빌 requestJob은 보통 1개월 단위 범위가 안정적이라,
    start~end를 월 단위로 쪼개서 yield.
    """
    cur = start
    while cur <= end:
        if cur.month == 12:
            next_month = date(cur.year + 1, 1, 1)
        else:
            next_month = date(cur.year, cur.month + 1, 1)

        chunk_end = min(end, next_month - timedelta(days=1))
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _poll_until_ready(job_id: str, max_wait_sec: int = 12, interval_sec: float = 1.0):
    """
    팝빌 job이 완료(jobState==3)될 때까지 잠깐 폴링.
    (승인/실거래 환경에서는 더 길게 잡을 수도 있음)
    """
    started = time.time()
    last_state = None

    while time.time() - started < max_wait_sec:
        st = get_job_state(job_id)
        last_state = st
        if str(getattr(st, "jobState", "")) == "3":
            return st
        time.sleep(interval_sec)

    return last_state


def sync_popbill_for_user(
    user_pk: int,
    start: date | None = None,
    end: date | None = None,
) -> PopbillImportResult:
    """
    활성화된 BankAccountLink들을 대상으로
    - requestJob(월단위) → jobState 완료 확인 → search(page)로 거래 수집
    - Transaction upsert(중복제거: external_hash) + 기본 Label/Evidence 생성
    """
    links = (
        BankAccountLink.query
        .filter(BankAccountLink.user_pk == user_pk, BankAccountLink.is_active.is_(True))
        .all()
    )
    if not links:
        raise PopbillImportError("연동된 계좌가 없습니다. 먼저 /bank 에서 계좌를 활성화해 주세요.")

    today_kst = datetime.now(timezone.utc).astimezone(KST).date()
    start = start or (today_kst - timedelta(days=30))
    end = end or today_kst
    if start > end:
        start, end = end, start

    # ImportJob 생성 (CSV import와 동일하게 started_at 기록)
    job = ImportJob(
        user_pk=user_pk,
        source="popbill",
        filename=None,
        total_rows=0,
        inserted_rows=0,
        duplicate_rows=0,
        failed_rows=0,
        error_summary={},
        started_at=utcnow(),
    )
    db.session.add(job)
    db.session.commit()  # job.id 확보

    income_rules = _load_income_rules(user_pk)
    expense_rules = _load_expense_rules(user_pk)
    user_accounts = {
        int(row.id): row
        for row in UserBankAccount.query.filter(UserBankAccount.user_pk == user_pk).all()
    }
    live_accounts_by_fingerprint: dict[tuple[str, str], str] = {}
    for account in list_bank_accounts():
        bank_code = str(getattr(account, "bankCode", "") or "").strip()
        raw_account_number = str(getattr(account, "accountNumber", "") or "").strip()
        safe = sanitize_account_like_value(raw_account_number)
        if bank_code and safe.hashed:
            live_accounts_by_fingerprint[(bank_code, safe.hashed)] = raw_account_number

    errors: list[dict] = []
    parsed: list[dict] = []

    for link in links:
        acct_start = start
        acct_end = end

        # 마지막 동기화 이후부터만 가져오기(중복은 어차피 hash로 막힘)
        if getattr(link, "last_synced_at", None):
            last_kst = link.last_synced_at.astimezone(KST).date()
            acct_start = max(acct_start, last_kst)

        for s, e in _daterange_month_chunks(acct_start, acct_end):
            sdate = s.strftime("%Y%m%d")
            edate = e.strftime("%Y%m%d")
            live_account_number = _resolve_live_account_number(
                link,
                accounts_by_id=user_accounts,
                live_accounts_by_fingerprint=live_accounts_by_fingerprint,
            )
            if not live_account_number:
                errors.append({
                    "account": _redacted_account_reference(link.bank_code, link.account_number),
                    "range": f"{sdate}~{edate}",
                    "error": "연결된 계좌 식별자를 다시 확인해 주세요.",
                })
                job.failed_rows += 1
                db.session.commit()
                continue

            try:
                job_id = request_job(link.bank_code, live_account_number, sdate, edate)
            except PopbillApiError as pe:
                errors.append({
                    "account": _redacted_account_reference(link.bank_code, live_account_number),
                    "range": f"{sdate}~{edate}",
                    "error": str(pe),
                    "code": getattr(pe, "code", None),
                })
                job.failed_rows += 1
                db.session.commit()
                continue

            st = _poll_until_ready(job_id)
            if not st:
                errors.append({
                    "account": _redacted_account_reference(link.bank_code, live_account_number),
                    "range": f"{sdate}~{edate}",
                    "error": "수집 상태 확인 실패",
                })
                job.failed_rows += 1
                db.session.commit()
                continue

            # 팝빌 문서 기준: jobState==3(완료) + errorCode==1(정상)
            if str(getattr(st, "jobState", "")) != "3" or int(getattr(st, "errorCode", 0) or 0) != 1:
                errors.append({
                    "account": _redacted_account_reference(link.bank_code, live_account_number),
                    "range": f"{sdate}~{edate}",
                    "error": f"수집 미완료/실패: jobState={getattr(st, 'jobState', None)} errorCode={getattr(st, 'errorCode', None)}",
                    "reason": getattr(st, "errorReason", None),
                })
                job.failed_rows += 1
                db.session.commit()
                continue

            # search 페이지네이션 (per_page=1000)
            page = 1
            while True:
                res = search(job_id, trade_types=[], search_string="", page=page, per_page=1000, order="D")
                rows = getattr(res, "list", None) or []

                for r in rows:
                    try:
                        tid = str(getattr(r, "tid", "") or "").strip()
                        if not tid:
                            continue

                        trdt = str(getattr(r, "trdt", "") or "").strip()
                        if not trdt:
                            trdate = str(getattr(r, "trdate", "") or "").strip()
                            if not trdate:
                                # 날짜가 없으면 이 row는 버림
                                continue
                            trdt = f"{trdate}000000"

                        occurred_at = _parse_trdt_to_utc(trdt)

                        acc_in = _to_int(getattr(r, "accIn", None))
                        acc_out = _to_int(getattr(r, "accOut", None))

                        if acc_in > 0:
                            direction = "in"
                            amount_krw = acc_in
                        else:
                            direction = "out"
                            amount_krw = acc_out

                        # DB 제약: amount_krw > 0
                        if int(amount_krw) <= 0:
                            continue

                        remark1 = str(getattr(r, "remark1", "") or "").strip()
                        remark2 = str(getattr(r, "remark2", "") or "").strip()
                        remark3 = str(getattr(r, "remark3", "") or "").strip()
                        remark4 = str(getattr(r, "remark4", "") or "").strip()
                        memo = str(getattr(r, "memo", "") or "").strip()

                        counterparty = (remark2 or remark1 or remark3).strip() or None
                        full_memo = " | ".join([x for x in [remark1, remark2, remark3, remark4, memo] if x]) or None

                        parsed.append({
                            "occurred_at": occurred_at,
                            "direction": direction,
                            "amount_krw": int(amount_krw),
                            "counterparty": counterparty,
                            "memo": full_memo,
                            "external_hash": _hash_external(tid),
                        })
                    except Exception as ex:
                        job.failed_rows += 1
                        errors.append({
                            "account": _redacted_account_reference(link.bank_code, live_account_number),
                            "range": f"{sdate}~{edate}",
                            "error": f"row parse error: {ex}",
                        })

                page_count = int(getattr(res, "pageCount", 1) or 1)
                if page >= page_count:
                    break
                page += 1

        # 계좌별 마지막 동기화 시각 업데이트
        if hasattr(link, "last_synced_at"):
            link.last_synced_at = utcnow()
        db.session.commit()

    # parsed 확정
    job.total_rows = len(parsed)
    if errors:
        job.error_summary = {"errors": errors[:50]}
    db.session.commit()

    if not parsed:
        job.finished_at = utcnow()
        db.session.commit()
        return PopbillImportResult(job.id, 0, 0, 0, int(job.failed_rows), errors)

    # 중복 제거: 이미 존재하는 external_hash는 스킵
    hashes = [p["external_hash"] for p in parsed]
    existing: set[str] = set()

    for i in range(0, len(hashes), 1000):
        chunk = hashes[i:i + 1000]
        rows = (
            db.session.query(Transaction.external_hash)
            .filter(Transaction.user_pk == user_pk, Transaction.external_hash.in_(chunk))
            .all()
        )
        existing.update([r[0] for r in rows])

    to_insert = [p for p in parsed if p["external_hash"] not in existing]
    job.duplicate_rows = len(parsed) - len(to_insert)
    db.session.commit()

    if not to_insert:
        job.finished_at = utcnow()
        db.session.commit()
        return PopbillImportResult(job.id, len(parsed), 0, int(job.duplicate_rows), int(job.failed_rows), errors)

    # Transaction insert
    tx_objs = [
        Transaction(
            user_pk=user_pk,
            import_job_id=job.id,
            occurred_at=p["occurred_at"],
            direction=p["direction"],
            amount_krw=int(p["amount_krw"]),
            counterparty=p["counterparty"],
            memo=p["memo"],
            source="popbill",
            external_hash=p["external_hash"],
            created_at=utcnow(),
        )
        for p in to_insert
    ]
    db.session.bulk_save_objects(tx_objs)
    db.session.commit()

    # 삽입된 tx 다시 조회해서(IDs 필요) 라벨/증빙 생성
    inserted_hashes = [p["external_hash"] for p in to_insert]
    inserted_txs: list[Transaction] = []

    for i in range(0, len(inserted_hashes), 1000):
        chunk = inserted_hashes[i:i + 1000]
        inserted_txs.extend(
            Transaction.query
            .filter(Transaction.user_pk == user_pk, Transaction.external_hash.in_(chunk))
            .all()
        )

    now = utcnow()
    income_labels: list[IncomeLabel] = []
    expense_labels: list[ExpenseLabel] = []
    evidences: list[EvidenceItem] = []

    for tx in inserted_txs:
        cp = tx.counterparty or ""

        if tx.direction == "in":
            status, conf = _apply_income_rule(cp, income_rules)
            income_labels.append(
                IncomeLabel(
                    user_pk=user_pk,
                    transaction_id=tx.id,
                    status=status,
                    confidence=conf,
                    labeled_by="auto",
                    rule_version=1,
                    decided_at=(now if status != "unknown" else None),
                    note=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            # ✅ 여기서 "estatus"를 제대로 정의해서 Evidence 정책과 연결
            estatus, conf = _apply_expense_rule(cp, expense_rules)

            expense_labels.append(
                ExpenseLabel(
                    user_pk=user_pk,
                    transaction_id=tx.id,
                    status=estatus,
                    confidence=conf,
                    labeled_by="auto",
                    rule_version=1,
                    decided_at=(now if estatus != "unknown" else None),
                    note=None,
                    created_at=now,
                    updated_at=now,
                )
            )

            requirement, ev_status = _evidence_defaults_from_expense_status(estatus)
            evidences.append(
                EvidenceItem(
                    user_pk=user_pk,
                    transaction_id=tx.id,
                    requirement=requirement,
                    status=ev_status,
                    note=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    if income_labels:
        db.session.bulk_save_objects(income_labels)
    if expense_labels:
        db.session.bulk_save_objects(expense_labels)
    if evidences:
        db.session.bulk_save_objects(evidences)
    db.session.commit()

    job.inserted_rows = len(inserted_txs)
    job.finished_at = utcnow()
    db.session.commit()

    return PopbillImportResult(
        import_job_id=job.id,
        total_rows=len(parsed),
        inserted_rows=len(inserted_txs),
        duplicate_rows=int(job.duplicate_rows),
        failed_rows=int(job.failed_rows),
        errors=errors,
    )

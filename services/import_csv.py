# services/import_csv.py
from __future__ import annotations

import csv
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from core.extensions import db
from core.time import utcnow
from domain.models import (
    ImportJob,
    Transaction,
    IncomeLabel,
    ExpenseLabel,
    EvidenceItem,
    CounterpartyRule,
    CounterpartyExpenseRule,
)

# ----------------------------
# Errors / Results
# ----------------------------

class CsvImportError(Exception):
    pass


@dataclass
class CsvImportResult:
    import_job_id: int
    total_rows: int
    inserted_rows: int
    duplicate_rows: int
    failed_rows: int


# ----------------------------
# File upload / preview
# ----------------------------

_TMP_BASE = "/tmp/safetospend_uploads"
_PREVIEW_ROWS = 20
_SNIFF_BYTES = 50_000

_DELIMS = [",", "\t", ";", "|"]

# Common Korean/English patterns for mapping
_PAT_DATE = re.compile(r"(일자|거래일|승인일|날짜|date|datetime|거래일시|승인일시)", re.I)
_PAT_AMOUNT = re.compile(r"(금액|거래금액|승인금액|amount|amt|금액\(원\))", re.I)
_PAT_IN_AMOUNT = re.compile(r"(입금|수입|credit|입금액|수입액)", re.I)
_PAT_OUT_AMOUNT = re.compile(r"(출금|지출|debit|출금액|지출액)", re.I)
_PAT_DIR = re.compile(r"(구분|입출|입\/출|입출금|type|dr\/cr|direction)", re.I)
_PAT_CP = re.compile(r"(거래처|가맹점|상호|merchant|counterparty|가게|업체|사용처)", re.I)
_PAT_MEMO = re.compile(r"(적요|내용|메모|비고|description|memo|내역|상세)", re.I)


def _ensure_user_tmp_dir(user_pk: int) -> str:
    path = os.path.join(_TMP_BASE, str(user_pk))
    os.makedirs(path, exist_ok=True)
    return path


def save_temp_upload(file: FileStorage | None, user_pk: int) -> Tuple[str, str, str]:
    """Save uploaded CSV into /tmp for preview/import.
    Returns (token, filepath, filename).
    """
    if not file or not file.filename:
        raise CsvImportError("CSV 파일이 없습니다.")

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".csv"):
        # allow text/csv without .csv extension, but prefer .csv
        filename = f"{filename}.csv"

    token = secrets.token_hex(16)
    user_dir = _ensure_user_tmp_dir(user_pk)
    filepath = os.path.join(user_dir, f"{token}.csv")

    try:
        file.save(filepath)
    except Exception as e:
        raise CsvImportError(f"파일 저장 실패: {e}")

    return token, filepath, filename


def _read_bytes_sample(filepath: str) -> bytes:
    with open(filepath, "rb") as f:
        return f.read(_SNIFF_BYTES)


def _decode_best_effort(raw: bytes) -> str:
    # Try common encodings for KR CSV exports
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # last resort: replace
    return raw.decode("utf-8", errors="replace")


def _sniff_delimiter(text_sample: str) -> str:
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text_sample, delimiters=_DELIMS)
        return dialect.delimiter
    except Exception:
        # fallback: choose the delimiter with most occurrences in first non-empty line
        lines = [ln for ln in text_sample.splitlines() if ln.strip()]
        if not lines:
            return ","
        line = lines[0]
        best = ","
        best_count = -1
        for d in _DELIMS:
            c = line.count(d)
            if c > best_count:
                best = d
                best_count = c
        return best


def read_csv_preview(filepath: str) -> Tuple[List[str], List[List[str]], str]:
    """Read headers + first N rows for preview and delimiter display."""
    raw = _read_bytes_sample(filepath)
    text = _decode_best_effort(raw)
    delimiter = _sniff_delimiter(text)

    # Now parse properly using the guessed delimiter
    # Use a fresh decode for full file reading (avoid sample truncation issues)
    with open(filepath, "rb") as f:
        full_text = _decode_best_effort(f.read())

    # Normalize newlines for csv.reader robustness
    full_text = full_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = full_text.split("\n")

    reader = csv.reader(lines, delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration:
        raise CsvImportError("CSV 파일이 비어있습니다.")

    headers = [h.strip() for h in headers if h is not None]
    rows: List[List[str]] = []
    for _ in range(_PREVIEW_ROWS):
        try:
            row = next(reader)
        except StopIteration:
            break
        rows.append([c.strip() if isinstance(c, str) else "" for c in row])

    if not headers or all(not h for h in headers):
        raise CsvImportError("CSV 헤더(첫 줄)를 인식할 수 없습니다. (첫 행에 컬럼명이 있어야 합니다)")

    return headers, rows, delimiter


def suggest_mapping(headers: List[str]) -> Dict[str, str]:
    """Heuristic mapping suggestion from headers."""
    def pick(pattern: re.Pattern) -> str:
        for h in headers:
            if pattern.search(h or ""):
                return h
        return ""

    mapping = {
        "date": pick(_PAT_DATE),
        "amount": pick(_PAT_AMOUNT),
        "in_amount": pick(_PAT_IN_AMOUNT),
        "out_amount": pick(_PAT_OUT_AMOUNT),
        "direction": pick(_PAT_DIR),
        "counterparty": pick(_PAT_CP),
        "memo": pick(_PAT_MEMO),
    }

    # If both in/out exist, often amount should be left blank
    if mapping["in_amount"] and mapping["out_amount"]:
        mapping["amount"] = ""

    return mapping


# ----------------------------
# Import core
# ----------------------------

def _normalize_space(s: Optional[str]) -> str:
    if not s:
        return ""
    s = str(s).strip()
    s = " ".join(s.split())
    return s


def _parse_int_amount(v: Optional[str]) -> Optional[int]:
    """Parse KRW integer from strings like '1,234', '₩1,234', '(1,234)', '-1234'."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None

    # parentheses as negative
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    s = s.replace("원", "").replace("₩", "").replace(",", "").replace(" ", "")
    # keep digits, sign, dot
    s = re.sub(r"[^0-9\.\-\+]", "", s)
    if not s or s in ("-", "+", ".", "-.", "+."):
        return None

    try:
        # Some CSVs export amounts as "1234.00"
        val = float(s)
    except ValueError:
        return None

    iv = int(round(val))
    if neg:
        iv = -abs(iv)
    return iv


def _parse_direction(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    s = _normalize_space(v).lower()

    # Korean
    if any(k in s for k in ("입금", "수입", "입", "credit", "cr")):
        if "출" not in s and "지출" not in s and "debit" not in s and "dr" not in s:
            return "in"
    if any(k in s for k in ("출금", "지출", "출", "debit", "dr")):
        return "out"

    # English common words
    if s in ("in", "income", "credit"):
        return "in"
    if s in ("out", "expense", "debit"):
        return "out"

    return None


def _parse_datetime_kst_to_utc(dt_str: str) -> datetime:
    """Parse a date/datetime string assuming KST when tz missing, store UTC."""
    if not dt_str or not str(dt_str).strip():
        raise CsvImportError("거래일시 값이 비어있습니다.")

    from dateutil import parser
    from zoneinfo import ZoneInfo

    kst = ZoneInfo("Asia/Seoul")
    try:
        dt = parser.parse(str(dt_str))
    except Exception as e:
        raise CsvImportError(f"날짜 파싱 실패: '{dt_str}' ({e})")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=kst)

    return dt.astimezone(ZoneInfo("UTC"))


def _compute_external_hash(
    occurred_at_utc: datetime,
    direction: str,
    amount_krw: int,
    counterparty: str,
    memo: str,
) -> str:
    # Keep it stable and tolerant
    base = f"{occurred_at_utc.isoformat()}|{direction}|{amount_krw}|{counterparty.lower()}|{memo.lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _build_header_index(headers: List[str]) -> Dict[str, int]:
    idx: Dict[str, int] = {}
    for i, h in enumerate(headers):
        h2 = str(h).strip()
        if h2 and h2 not in idx:
            idx[h2] = i
    return idx


def _get_cell(row: List[str], header_idx: Dict[str, int], col: str) -> str:
    if not col:
        return ""
    i = header_idx.get(col)
    if i is None:
        return ""
    if i >= len(row):
        return ""
    v = row[i]
    return v if v is not None else ""


def _chunked(iterable: List[str], size: int) -> List[List[str]]:
    return [iterable[i:i + size] for i in range(0, len(iterable), size)]


def _load_income_rules(user_pk: int) -> Dict[str, str]:
    rules = (
        CounterpartyRule.query
        .filter_by(user_pk=user_pk, active=True)
        .all()
    )
    # key: normalized counterparty_key, val: "income"/"non_income"
    return {r.counterparty_key: r.rule for r in rules}


def _load_expense_rules(user_pk: int) -> Dict[str, str]:
    rules = (
        CounterpartyExpenseRule.query
        .filter_by(user_pk=user_pk, active=True)
        .all()
    )
    # key: normalized counterparty_key, val: "business"/"personal"
    return {r.counterparty_key: r.rule for r in rules}


def _norm_counterparty_key(counterparty: str) -> str:
    return _normalize_space(counterparty).lower()


def _apply_income_rule(counterparty: str, income_rules: Dict[str, str]) -> Tuple[str, int]:
    """Return (status, confidence)."""
    key = _norm_counterparty_key(counterparty)
    rule = income_rules.get(key)
    if rule in ("income", "non_income"):
        return rule, 80
    return "unknown", 0


def _apply_expense_rule(counterparty: str, expense_rules: Dict[str, str]) -> Tuple[str, int]:
    key = _norm_counterparty_key(counterparty)
    rule = expense_rules.get(key)
    if rule in ("business", "personal"):
        return rule, 80
    return "unknown", 0


def import_csv_to_db(
    user_pk: int,
    filepath: str,
    filename: str,
    mapping: Dict[str, str],
) -> CsvImportResult:
    """Import CSV file into DB as Transactions + default labels/evidence.
    mapping keys:
      date (required),
      amount OR (in_amount/out_amount) (at least one strategy required),
      direction (optional),
      counterparty (optional),
      memo (optional)
    """
    if not os.path.exists(filepath):
        raise CsvImportError("업로드 파일을 찾을 수 없습니다. 다시 업로드해주세요.")

    map_date = (mapping.get("date") or "").strip()
    map_amount = (mapping.get("amount") or "").strip()
    map_in = (mapping.get("in_amount") or "").strip()
    map_out = (mapping.get("out_amount") or "").strip()
    map_dir = (mapping.get("direction") or "").strip()
    map_cp = (mapping.get("counterparty") or "").strip()
    map_memo = (mapping.get("memo") or "").strip()

    if not map_date:
        raise CsvImportError("거래일시 컬럼은 필수입니다.")
    if not map_amount and not (map_in or map_out):
        raise CsvImportError("금액 컬럼이 필요합니다. (단일 금액 또는 입금/출금 컬럼을 지정하세요)")

    # Read full CSV text
    raw = _read_bytes_sample(filepath)
    sample_text = _decode_best_effort(raw)
    delimiter = _sniff_delimiter(sample_text)

    with open(filepath, "rb") as f:
        full_text = _decode_best_effort(f.read())

    full_text = full_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = full_text.split("\n")
    reader = csv.reader(lines, delimiter=delimiter)

    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        raise CsvImportError("CSV 파일이 비어있습니다.")

    header_idx = _build_header_index(headers)

    # Validate mapping columns exist in headers (when provided)
    for key, col in (
        ("거래일시", map_date),
        ("금액", map_amount),
        ("입금 금액", map_in),
        ("출금 금액", map_out),
        ("입/출 구분", map_dir),
        ("거래처", map_cp),
        ("메모", map_memo),
    ):
        if col and col not in header_idx:
            raise CsvImportError(f"{key}로 지정한 컬럼을 CSV에서 찾지 못했습니다: '{col}'")

    # Create import job
    job = ImportJob(
        user_pk=user_pk,
        source="csv",
        filename=filename,
        total_rows=0,
        inserted_rows=0,
        duplicate_rows=0,
        failed_rows=0,
        error_summary={},
        started_at=utcnow(),
    )
    db.session.add(job)
    db.session.commit()  # get job.id

    income_rules = _load_income_rules(user_pk)
    expense_rules = _load_expense_rules(user_pk)

    # Parse rows
    parsed: List[dict] = []
    errors: List[dict] = []

    row_num = 1  # header is row 1
    for row in reader:
        row_num += 1
        # skip empty lines
        if not row or all(not str(c).strip() for c in row):
            continue

        job.total_rows += 1

        try:
            dt_raw = _get_cell(row, header_idx, map_date)
            occurred_at = _parse_datetime_kst_to_utc(dt_raw)

            cp = _normalize_space(_get_cell(row, header_idx, map_cp)) if map_cp else ""
            memo = _normalize_space(_get_cell(row, header_idx, map_memo)) if map_memo else ""

            # Determine direction + amount
            direction: Optional[str] = None
            amount_krw: Optional[int] = None

            if map_dir:
                direction = _parse_direction(_get_cell(row, header_idx, map_dir))

            if map_in or map_out:
                in_amt = _parse_int_amount(_get_cell(row, header_idx, map_in)) if map_in else None
                out_amt = _parse_int_amount(_get_cell(row, header_idx, map_out)) if map_out else None

                # choose the non-empty one
                if in_amt is not None and in_amt != 0 and (out_amt is None or out_amt == 0):
                    direction = direction or "in"
                    amount_krw = abs(int(in_amt))
                elif out_amt is not None and out_amt != 0 and (in_amt is None or in_amt == 0):
                    direction = direction or "out"
                    amount_krw = abs(int(out_amt))
                elif in_amt is not None and out_amt is not None and (in_amt != 0 or out_amt != 0):
                    # some exports put both; treat net direction by larger abs
                    if abs(in_amt) >= abs(out_amt):
                        direction = direction or "in"
                        amount_krw = abs(int(in_amt))
                    else:
                        direction = direction or "out"
                        amount_krw = abs(int(out_amt))

            if amount_krw is None and map_amount:
                amt = _parse_int_amount(_get_cell(row, header_idx, map_amount))
                if amt is None:
                    raise CsvImportError("금액을 파싱할 수 없습니다.")
                # If direction unknown, infer from sign
                if direction is None:
                    if amt < 0:
                        direction = "out"
                        amount_krw = abs(amt)
                    else:
                        direction = "in"
                        amount_krw = abs(amt)
                else:
                    amount_krw = abs(int(amt))

            if direction not in ("in", "out"):
                # last inference: if amount provided and sign unknown, assume out? no, assume in for non-negative.
                if direction is None:
                    direction = "in"
                else:
                    raise CsvImportError("입/출 구분을 판단할 수 없습니다.")

            if amount_krw is None or amount_krw <= 0:
                raise CsvImportError("금액이 비어있거나 0입니다.")

            external_hash = _compute_external_hash(
                occurred_at_utc=occurred_at,
                direction=direction,
                amount_krw=amount_krw,
                counterparty=cp,
                memo=memo,
            )

            parsed.append(
                {
                    "occurred_at": occurred_at,
                    "direction": direction,
                    "amount_krw": amount_krw,
                    "counterparty": cp,
                    "memo": memo,
                    "external_hash": external_hash,
                }
            )
        except Exception as e:
            job.failed_rows += 1
            errors.append({"row": row_num, "error": str(e)})
            continue

    # Save job parsing stats early
    if errors:
        job.error_summary = {"errors": errors[:50]}  # cap to avoid huge json
    db.session.commit()

    if not parsed:
        job.finished_at = utcnow()
        db.session.commit()
        raise CsvImportError("가져올 거래가 없습니다. (CSV 매핑/내용을 확인해주세요)")

    # Dedup: find existing hashes
    hashes = [p["external_hash"] for p in parsed]
    existing: set[str] = set()
    for chunk in _chunked(hashes, 1000):
        rows = (
            db.session.query(Transaction.external_hash)
            .filter(Transaction.user_pk == user_pk, Transaction.external_hash.in_(chunk))
            .all()
        )
        existing.update([r[0] for r in rows])

    to_insert = [p for p in parsed if p["external_hash"] not in existing]
    job.duplicate_rows = len(parsed) - len(to_insert)

    if not to_insert:
        job.finished_at = utcnow()
        db.session.commit()
        return CsvImportResult(
            import_job_id=job.id,
            total_rows=job.total_rows,
            inserted_rows=0,
            duplicate_rows=job.duplicate_rows,
            failed_rows=job.failed_rows,
        )

    # Insert Transactions
    tx_objs: List[Transaction] = []
    for p in to_insert:
        tx_objs.append(
            Transaction(
                user_pk=user_pk,
                import_job_id=job.id,
                occurred_at=p["occurred_at"],
                direction=p["direction"],
                amount_krw=int(p["amount_krw"]),
                counterparty=p["counterparty"] or None,
                memo=p["memo"] or None,
                source="csv",
                external_hash=p["external_hash"],
                created_at=utcnow(),
            )
        )

    db.session.bulk_save_objects(tx_objs)
    db.session.commit()

    # Fetch inserted transactions to attach labels/evidence (need IDs)
    inserted_hashes = [p["external_hash"] for p in to_insert]
    inserted_txs: List[Transaction] = []
    for chunk in _chunked(inserted_hashes, 1000):
        inserted_txs.extend(
            Transaction.query
            .filter(Transaction.user_pk == user_pk, Transaction.external_hash.in_(chunk))
            .all()
        )

    # Attach IncomeLabel / ExpenseLabel / EvidenceItem
    income_labels: List[IncomeLabel] = []
    expense_labels: List[ExpenseLabel] = []
    evidences: List[EvidenceItem] = []

    now = utcnow()

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

            # Evidence default based on expense label
            if estatus == "business":
                requirement = "required"
                ev_status = "missing"
            elif estatus == "personal":
                requirement = "not_needed"
                ev_status = "not_needed"
            else:
                requirement = "maybe"
                ev_status = "missing"

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

    job.inserted_rows = len(inserted_txs)
    job.finished_at = utcnow()
    db.session.commit()

    return CsvImportResult(
        import_job_id=job.id,
        total_rows=job.total_rows,
        inserted_rows=job.inserted_rows,
        duplicate_rows=job.duplicate_rows,
        failed_rows=job.failed_rows,
    )

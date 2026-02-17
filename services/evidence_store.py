# services/evidence_store.py
"""증빙 파일 보관함(vault) - v1

요구사항
- 보관: 세법상 '확정신고기한 종료일부터 5년' 보관 의무가 일반적이므로,
  기본 보관 만료일(retention_until)을 '거래 발생 연도 + 6년 5월 31일'로 설정.
  (예: 2026년 거래 → 2027-05-31 신고기한 종료 → 2032-05-31까지)

- 사용자는 언제든 즉시 삭제 가능(파일 제거 + 메타 정리).

주의
- v1은 로컬 파일 저장만 지원.
- 배포 시에는 디스크 암호화/접근통제/백업 정책을 별도로 설계해야 함.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from core.extensions import db
from domain.models import EvidenceItem, Transaction


KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class EvidenceFileMeta:
    file_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    retention_until: date


def _project_root() -> Path:
    # services/ 아래에서 2단계 위가 프로젝트 루트(SafeToSpend)
    return Path(__file__).resolve().parents[1]


def evidence_root() -> Path:
    """증빙 저장 루트 디렉터리.

    - 환경변수 EVIDENCE_UPLOAD_DIR 있으면 사용
    - 없으면 <project>/uploads/evidence
    """
    base = _project_root() / "uploads" / "evidence"
    p = Path(os.getenv("EVIDENCE_UPLOAD_DIR") or str(base))
    p.mkdir(parents=True, exist_ok=True)
    return p


def allowed_evidence_ext() -> set[str]:
    return {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def compute_retention_until_from_tx(tx_occurred_at: datetime) -> date:
    """거래시점에서 '최대 보관 만료일' 계산.

    종합소득세 기준으로 많이 쓰는 규칙:
    - 해당 연도 소득세 확정신고 기한: 다음 해 5/31
    - 그 날부터 5년 보관
    → 거래 연도(Y) 기준 retention_until = (Y+6)-05-31
    """
    dt = tx_occurred_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    y = dt.astimezone(KST).year
    return date(y + 6, 5, 31)


def _read_bytes_limited(fs: FileStorage, max_bytes: int) -> bytes:
    b = fs.read()
    if b is None:
        return b""
    if len(b) > max_bytes:
        raise ValueError("파일이 너무 큽니다.")
    return b


def attach_evidence_file(
    *,
    user_pk: int,
    evidence_id: int,
    uploaded: FileStorage,
    max_bytes: int = 20 * 1024 * 1024,
) -> EvidenceFileMeta:
    """증빙 파일 업로드(저장 + DB 메타 업데이트)."""
    if not uploaded or not uploaded.filename:
        raise ValueError("파일이 없습니다.")

    filename = secure_filename(uploaded.filename)
    if not filename:
        raise ValueError("파일명이 올바르지 않습니다.")

    ext = (Path(filename).suffix or "").lower()
    if ext not in allowed_evidence_ext():
        raise ValueError("허용되지 않는 파일 형식입니다. (pdf/jpg/png/webp)")

    ev = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not ev:
        raise ValueError("대상을 찾을 수 없습니다.")

    tx = Transaction.query.filter_by(id=ev.transaction_id, user_pk=user_pk).first()
    if not tx:
        raise ValueError("거래를 찾을 수 없습니다.")

    payload = _read_bytes_limited(uploaded, max_bytes=max_bytes)
    size = len(payload)
    sha = hashlib.sha256(payload).hexdigest()
    now = datetime.now(timezone.utc)
    retention_until = compute_retention_until_from_tx(tx.occurred_at)

    # 기존 파일 제거
    _delete_physical_file_if_exists(ev.file_key)

    # 새 경로
    key = f"u{user_pk}/e{evidence_id}/{uuid4().hex}{ext}"
    abs_path = evidence_root() / key
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(payload)

    ev.file_key = key
    ev.original_filename = filename
    ev.mime_type = uploaded.mimetype or "application/octet-stream"
    ev.size_bytes = size
    ev.sha256 = sha
    ev.uploaded_at = now
    ev.deleted_at = None
    ev.retention_until = retention_until
    ev.status = "attached"  # 업로드하면 자동으로 첨부 처리
    db.session.commit()

    return EvidenceFileMeta(
        file_key=key,
        original_filename=filename,
        mime_type=ev.mime_type,
        size_bytes=size,
        sha256=sha,
        uploaded_at=now,
        retention_until=retention_until,
    )


def evidence_abs_path(file_key: str) -> Path:
    return evidence_root() / file_key


def _delete_physical_file_if_exists(file_key: str | None) -> None:
    if not file_key:
        return
    try:
        p = evidence_abs_path(file_key)
        if p.exists() and p.is_file():
            p.unlink()
    except Exception:
        # best-effort
        return


def delete_evidence_file(*, user_pk: int, evidence_id: int) -> None:
    """즉시 삭제: 파일 제거 + DB 메타 정리."""
    ev = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not ev:
        raise ValueError("대상을 찾을 수 없습니다.")

    _delete_physical_file_if_exists(ev.file_key)

    ev.file_key = None
    ev.original_filename = None
    ev.mime_type = None
    ev.size_bytes = None
    ev.sha256 = None
    ev.uploaded_at = None
    ev.deleted_at = datetime.now(timezone.utc)
    ev.retention_until = None

    # 파일 삭제 후 상태는 requirement에 맞춰 복구
    if ev.requirement == "not_needed":
        ev.status = "not_needed"
    else:
        ev.status = "missing"

    db.session.commit()


def purge_expired_evidence(user_pk: int | None = None) -> int:
    """보관 만료(retention_until)된 파일 자동 삭제.

    - 운영에서는 cron/스케줄러로 돌리면 됨.
    - 여기서는 함수만 제공.
    """
    today = date.today()
    q = EvidenceItem.query.filter(EvidenceItem.file_key.isnot(None))
    q = q.filter(EvidenceItem.retention_until.isnot(None), EvidenceItem.retention_until < today)
    if user_pk is not None:
        q = q.filter(EvidenceItem.user_pk == user_pk)

    n = 0
    for ev in q.all():
        _delete_physical_file_if_exists(ev.file_key)
        ev.file_key = None
        ev.original_filename = None
        ev.mime_type = None
        ev.size_bytes = None
        ev.sha256 = None
        ev.uploaded_at = None
        ev.deleted_at = datetime.now(timezone.utc)
        ev.retention_until = None
        if ev.requirement == "not_needed":
            ev.status = "not_needed"
        else:
            ev.status = "missing"
        n += 1

    if n:
        db.session.commit()
    return n

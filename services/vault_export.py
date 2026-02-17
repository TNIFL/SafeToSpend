# services/vault_export.py
"""증빙 보관함 전체 백업/전체 삭제 (Option C: 서버 보관 + 사용자 로컬 다운로드)

- 전체 백업: 서버에 저장된 모든 증빙 파일을 ZIP으로 묶어 내려받기
- 전체 삭제: 사용자가 원하면 서버 보관본을 즉시 전부 삭제

원칙
- 서버에 파일을 저장하되(보관함), 사용자는 언제든 "내 로컬"로 내려받아 보관 가능
- 사용자가 요청하면 즉시 삭제 가능(법정/권장 보관기간과 무관)

주의
- v1은 로컬 디스크 저장(evidence_store.evidence_root()) 기준
- 운영에서는 접근통제/암호화/백업/로깅을 반드시 설계
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone

from werkzeug.utils import secure_filename

from core.extensions import db
from domain.models import EvidenceItem, Transaction
from services.evidence_store import evidence_abs_path


def build_vault_export_zip(*, user_pk: int) -> tuple[io.BytesIO, str]:
    """해당 유저의 서버 보관 증빙 파일을 전부 ZIP으로 묶어 반환."""

    rows = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(EvidenceItem.file_key.isnot(None))
        .order_by(Transaction.occurred_at.desc())
        .all()
    )

    out = io.BytesIO()
    z = zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED)

    # 00_README
    now = datetime.now(timezone.utc)
    z.writestr(
        "00_README.txt",
        "SafeToSpend(쓸수있어) 증빙 보관함 전체 백업\n"
        f"- exported_at_utc: {now.isoformat()}\n"
        "- 구조: YYYY-MM/증빙ID_원본파일명\n"
        "- index.csv 에서 거래/증빙 메타데이터를 확인하세요.\n",
    )

    # index.csv
    index_rows: list[dict[str, str]] = []

    attached = 0
    skipped = 0

    for ev, tx in rows:
        file_key = (ev.file_key or "").strip()
        if not file_key:
            continue

        p = evidence_abs_path(file_key)
        if not p.exists() or not p.is_file():
            skipped += 1
            continue

        month_key = (tx.occurred_at.strftime("%Y-%m") if tx and tx.occurred_at else "unknown")
        orig = (ev.original_filename or p.name).strip() or p.name
        safe = secure_filename(orig) or p.name
        arcname = f"{month_key}/{ev.id}_{safe}"

        z.write(p, arcname=arcname)
        attached += 1

        index_rows.append(
            {
                "month": month_key,
                "evidence_id": str(ev.id),
                "tx_id": str(tx.id if tx else ""),
                "occurred_at": (tx.occurred_at.isoformat() if tx and tx.occurred_at else ""),
                "direction": (tx.direction or "") if tx else "",
                "amount_krw": str(int(tx.amount_krw or 0)) if tx else "0",
                "counterparty": (tx.counterparty or "") if tx else "",
                "original_filename": (ev.original_filename or ""),
                "mime_type": (ev.mime_type or ""),
                "size_bytes": str(ev.size_bytes or ""),
                "sha256": (ev.sha256 or ""),
                "uploaded_at": (ev.uploaded_at.isoformat() if ev.uploaded_at else ""),
                "retention_until": (ev.retention_until.isoformat() if ev.retention_until else ""),
                "path_in_zip": arcname,
            }
        )

    # write index.csv
    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=[
            "month",
            "evidence_id",
            "tx_id",
            "occurred_at",
            "direction",
            "amount_krw",
            "counterparty",
            "original_filename",
            "mime_type",
            "size_bytes",
            "sha256",
            "uploaded_at",
            "retention_until",
            "path_in_zip",
        ],
    )
    w.writeheader()
    for r in index_rows:
        w.writerow(r)

    z.writestr("index.csv", buf.getvalue())

    # stats
    z.writestr(
        "stats.txt",
        f"attached={attached}\n"
        f"skipped_missing_physical_file={skipped}\n"
        f"rows_total={len(rows)}\n",
    )

    z.close()
    out.seek(0)

    filename = f"SafeToSpend_VaultExport_{now.astimezone(timezone.utc).strftime('%Y%m%d_%H%M%S')}Z.zip"
    return out, filename


def delete_all_evidence_files(*, user_pk: int) -> int:
    """서버 보관 중인 파일을 전부 즉시 삭제하고, DB 메타를 정리한다.

    반환: 실제로 삭제/정리된 evidence 개수
    """

    q = EvidenceItem.query.filter(EvidenceItem.user_pk == user_pk)
    q = q.filter(EvidenceItem.file_key.isnot(None))

    now = datetime.now(timezone.utc)
    n = 0

    for ev in q.all():
        file_key = (ev.file_key or "").strip()
        if file_key:
            try:
                p = evidence_abs_path(file_key)
                if p.exists() and p.is_file():
                    p.unlink()
            except Exception:
                # best-effort
                pass

        ev.file_key = None
        ev.original_filename = None
        ev.mime_type = None
        ev.size_bytes = None
        ev.sha256 = None
        ev.uploaded_at = None
        ev.deleted_at = now
        ev.retention_until = None

        # 파일 삭제 후 상태는 requirement에 맞춰 복구
        if ev.requirement == "not_needed":
            ev.status = "not_needed"
        else:
            ev.status = "missing"

        n += 1

    if n:
        db.session.commit()
    return n

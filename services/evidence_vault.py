# services/evidence_vault.py
from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# 업로드 최대 용량(기본 20MB). 필요하면 app.config["EVIDENCE_MAX_BYTES"]로 조정.
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

ALLOWED_EXTS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".heic",
    ".heif",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def evidence_root() -> Path:
    """
    서버에 증빙 파일을 저장할 루트 디렉터리.
    - 기본: <프로젝트>/uploads/evidence
    - override: app.config["EVIDENCE_UPLOAD_DIR"]
    """
    cfg = current_app.config.get("EVIDENCE_UPLOAD_DIR")
    if cfg:
        root = Path(cfg)
    else:
        root = Path(current_app.root_path) / "uploads" / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _retention_until_default() -> date:
    # 기본 7년(세무 관련 보관 관행/리스크를 고려한 “최대치”로 잡음)
    days = int(current_app.config.get("EVIDENCE_RETENTION_DAYS", 365 * 7))
    return (utcnow().date() + timedelta(days=days))


def _max_bytes() -> int:
    return int(current_app.config.get("EVIDENCE_MAX_BYTES", DEFAULT_MAX_BYTES))


def _guess_mime(filename: str | None, fallback: str = "application/octet-stream") -> str:
    if not filename:
        return fallback
    mt, _ = mimetypes.guess_type(filename)
    return mt or fallback


def _safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _validate_file(file: FileStorage) -> tuple[str, str]:
    """
    반환: (safe_original_filename, mime_type)
    """
    if not file or not file.filename:
        raise ValueError("파일이 없습니다.")
    original = secure_filename(file.filename) or "evidence"
    ext = _safe_ext(original)

    # mimetype은 클라이언트가 주는 값이라 100% 신뢰는 불가하지만, UX상 표시/분류에 씀
    mime = (file.mimetype or "").strip() or _guess_mime(original)

    # 확장자가 비어있으면 mimetype 기반으로 확장자 보강(가능할 때)
    if not ext:
        guessed_ext = mimetypes.guess_extension(mime or "") or ""
        if guessed_ext:
            original = f"{original}{guessed_ext}"
            ext = guessed_ext.lower()

    # 제한은 “강하게” (MVP 단계에서 랜덤 파일 업로드 차단)
    if ext and ext not in ALLOWED_EXTS:
        # 이미지/ pdf면 예외적으로 허용(확장자가 이상한 경우)
        if not (mime.startswith("image/") or mime == "application/pdf"):
            raise ValueError("허용되지 않는 파일 형식입니다. (이미지/PDF만 가능)")
    return original, mime


@dataclass(frozen=True)
class StoredFile:
    file_key: str
    abs_path: Path
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str


def store_evidence_file(
    *,
    user_pk: int,
    tx_id: int,
    month_key: str,
    file: FileStorage,
) -> StoredFile:
    """
    업로드 파일을 서버 디스크에 저장하고, 메타데이터를 반환.
    DB 업데이트는 호출자가 처리.
    """
    original_filename, mime_type = _validate_file(file)

    root = evidence_root()

    # user/month/tx 기준으로 폴더 구성
    rel_dir = Path(f"u{user_pk}") / month_key / f"tx{tx_id}"
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    ext = _safe_ext(original_filename)
    token = uuid4().hex
    save_name = f"{token}{ext or ''}"
    abs_path = abs_dir / save_name

    # streaming 저장 + 해시
    h = hashlib.sha256()
    size = 0
    max_bytes = _max_bytes()

    tmp_path = abs_path.with_suffix(abs_path.suffix + ".part")

    with open(tmp_path, "wb") as f:
        while True:
            chunk = file.stream.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                try:
                    f.close()
                except Exception:
                    pass
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ValueError(f"파일이 너무 큽니다. (최대 {max_bytes // (1024*1024)}MB)")

            h.update(chunk)
            f.write(chunk)

    os.replace(tmp_path, abs_path)

    # file_key는 evidence_root() 기준 상대 경로로 저장
    file_key = str((rel_dir / save_name).as_posix())

    return StoredFile(
        file_key=file_key,
        abs_path=abs_path,
        original_filename=original_filename,
        mime_type=mime_type,
        size_bytes=size,
        sha256=h.hexdigest(),
    )


def resolve_file_path(file_key: str) -> Path:
    """
    DB의 file_key(상대경로)를 실제 파일 절대경로로 변환.
    path traversal 방지 포함.
    """
    if not file_key:
        raise FileNotFoundError("file_key is empty")
    root = evidence_root().resolve()
    target = (root / file_key).resolve()
    if root not in target.parents and root != target:
        raise FileNotFoundError("invalid file path")
    return target


def delete_physical_file(file_key: str) -> None:
    """
    디스크 파일 삭제(없으면 무시).
    """
    try:
        path = resolve_file_path(file_key)
        path.unlink(missing_ok=True)
    except Exception:
        return


def default_retention_until() -> date:
    return _retention_until_default()

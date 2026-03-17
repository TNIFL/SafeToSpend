from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


DEFAULT_MAX_BYTES = 20 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
ALLOWED_EXTS = {".pdf", ".csv", ".xlsx"}


@dataclass(frozen=True)
class StoredOfficialDataFile:
    raw_file_key: str
    abs_path: Path
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str


def _max_bytes() -> int:
    return int(current_app.config.get("OFFICIAL_DATA_MAX_BYTES", current_app.config.get("MAX_CONTENT_LENGTH", DEFAULT_MAX_BYTES)))


def official_data_root() -> Path:
    cfg = current_app.config.get("OFFICIAL_DATA_UPLOAD_DIR")
    if cfg:
        root = Path(cfg)
    else:
        root = Path(current_app.root_path) / "uploads" / "official_data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _guess_mime(filename: str | None, fallback: str = "application/octet-stream") -> str:
    if not filename:
        return fallback
    mt, _ = mimetypes.guess_type(filename)
    return mt or fallback


def _safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _validate_file(file: FileStorage) -> tuple[str, str]:
    if not file or not file.filename:
        raise ValueError("파일이 없습니다.")

    original = secure_filename(file.filename) or "official-data"
    ext = _safe_ext(original)
    mime = (file.mimetype or "").strip() or _guess_mime(original)

    if not ext:
        guessed_ext = mimetypes.guess_extension(mime or "") or ""
        if guessed_ext:
            original = f"{original}{guessed_ext}"
            ext = guessed_ext.lower()

    if ext not in ALLOWED_EXTS:
        raise ValueError("허용되지 않는 파일 형식입니다. (PDF/CSV/XLSX만 가능)")

    return original, mime


def store_official_data_file(*, user_pk: int, file: FileStorage) -> StoredOfficialDataFile:
    original_filename, mime_type = _validate_file(file)

    root = official_data_root()
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    rel_dir = Path(f"u{user_pk}") / month_key
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    ext = _safe_ext(original_filename)
    save_name = f"{uuid4().hex}{ext or ''}"
    abs_path = abs_dir / save_name
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".part")

    max_bytes = _max_bytes()
    size = 0
    hasher = hashlib.sha256()

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
                raise ValueError(f"파일이 너무 큽니다. (최대 {max_bytes // (1024 * 1024)}MB)")
            hasher.update(chunk)
            f.write(chunk)

    os.replace(tmp_path, abs_path)
    raw_file_key = str((rel_dir / save_name).as_posix())

    return StoredOfficialDataFile(
        raw_file_key=raw_file_key,
        abs_path=abs_path,
        original_filename=original_filename,
        mime_type=mime_type,
        size_bytes=size,
        sha256=hasher.hexdigest(),
    )


def resolve_official_data_path(raw_file_key: str) -> Path:
    if not raw_file_key:
        raise FileNotFoundError("raw_file_key is empty")
    root = official_data_root().resolve()
    target = (root / raw_file_key).resolve()
    if root not in target.parents and root != target:
        raise FileNotFoundError("invalid official data path")
    return target


def delete_official_data_file(raw_file_key: str | None) -> None:
    if not raw_file_key:
        return
    try:
        resolve_official_data_path(raw_file_key).unlink(missing_ok=True)
    except Exception:
        return

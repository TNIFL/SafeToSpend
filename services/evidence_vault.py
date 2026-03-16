# services/evidence_vault.py
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# Optional deps for merging long receipts (multi-shot -> single PDF)
try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore

try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    _HAS_HEIF = True
except Exception:  # pragma: no cover
    _HAS_HEIF = False

from core.extensions import db
from core.time import utcnow as _now_kst_naive
from domain.models import EvidenceItem, Transaction

# 업로드 최대 용량(기본 20MB). 필요하면 app.config["EVIDENCE_MAX_BYTES"]로 조정.
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
SIGNATURE_READ_SIZE = 8192
MULTI_IMAGE_MAX_DIM = 2800

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

_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

_HEIF_BRANDS = (
    b"heic",
    b"heix",
    b"hevc",
    b"heim",
    b"heis",
    b"mif1",
    b"msf1",
    b"heif",
)

SENSITIVE_FILENAME_KEYWORDS = (
    "주민등록",
    "주민번호",
    "신분증",
    "운전면허",
    "면허증",
    "여권",
    "가족관계",
    "등본",
    "초본",
    "resident",
    "idcard",
    "passport",
    "familyregister",
    "rrn",
)


def utcnow() -> datetime:
    return _now_kst_naive()


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
    v = current_app.config.get("EVIDENCE_MAX_BYTES")
    if v is None:
        v = current_app.config.get("MAX_UPLOAD_BYTES", DEFAULT_MAX_BYTES)
    return int(v or DEFAULT_MAX_BYTES)


def _safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _contains_sensitive_filename(raw_filename: str | None) -> bool:
    text = str(raw_filename or "").strip().lower()
    if not text:
        return False
    for token in SENSITIVE_FILENAME_KEYWORDS:
        if str(token).strip().lower() in text:
            return True
    return False


def _stream_head_bytes(file: FileStorage, size: int = SIGNATURE_READ_SIZE) -> bytes:
    stream = getattr(file, "stream", None)
    if stream is None:
        return b""
    pos = None
    try:
        if hasattr(stream, "tell"):
            pos = stream.tell()
        # 시그니처 검증은 항상 파일 시작 바이트를 기준으로 본다.
        if hasattr(stream, "seek"):
            stream.seek(0)
        data = stream.read(int(size) if int(size) > 0 else SIGNATURE_READ_SIZE) or b""
    except Exception:
        return b""
    finally:
        try:
            if pos is not None and hasattr(stream, "seek"):
                stream.seek(pos)
        except Exception:
            pass
    return data


def _detect_signature(file: FileStorage) -> tuple[str | None, str | None]:
    """
    반환: (정규화 확장자, 정규화 mime)
    """
    head = _stream_head_bytes(file, SIGNATURE_READ_SIZE)
    if not head:
        return None, None
    if head.startswith(b"%PDF"):
        return ".pdf", "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif", "image/gif"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp", "image/webp"

    # ISO BMFF 계열(HEIC/HEIF): [size][ftyp][major_brand...]
    if len(head) >= 16 and head[4:8] == b"ftyp":
        brands_blob = head[8:40].lower()
        for brand in _HEIF_BRANDS:
            if brand in brands_blob:
                if brand in {b"heic", b"heix", b"hevc", b"heim", b"heis"}:
                    return ".heic", "image/heic"
                return ".heif", "image/heif"
    return None, None


def _mime_from_ext(ext: str) -> str:
    return _EXT_TO_MIME.get(str(ext or "").lower(), "application/octet-stream")


def _finalize_safe_filename(original: str, detected_ext: str) -> str:
    stem = secure_filename(Path(original).stem) or "evidence"
    ext = str(detected_ext or "").lower()
    if ext not in ALLOWED_EXTS:
        ext = ".pdf"
    return f"{stem}{ext}"


def _file_sha256_and_size(abs_path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(abs_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), int(size)


def _image_resample() -> int:
    if Image is None:
        return 0
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None and hasattr(resampling, "LANCZOS"):
        return int(resampling.LANCZOS)
    if hasattr(Image, "LANCZOS"):
        return int(getattr(Image, "LANCZOS"))
    return int(getattr(Image, "BICUBIC", 3))


def _save_multi_images_to_pdf(
    *,
    files: list[FileStorage],
    out_path: Path,
    max_bytes: int,
) -> tuple[int, str]:
    if Image is None:
        raise ValueError("멀티 업로드(긴 영수증 합치기)는 Pillow가 필요합니다. pip install Pillow")
    if not files:
        raise ValueError("파일이 없습니다.")

    with tempfile.TemporaryDirectory(prefix="evidence_multi_") as tmpdir:
        tmp_root = Path(tmpdir)
        normalized_paths: list[Path] = []

        for idx, f in enumerate(files):
            original_filename, mime_type = _validate_file(f)
            ext = _safe_ext(original_filename)
            if ext == ".pdf" or (mime_type or "").lower() == "application/pdf":
                raise ValueError("여러 파일 업로드에서는 PDF를 섞을 수 없어요. 사진 여러 장을 선택해주세요.")
            if ext in (".heic", ".heif") and (not _HAS_HEIF):
                raise ValueError("HEIC/HEIF 멀티 업로드에는 pillow-heif가 필요합니다.")

            try:
                if hasattr(f.stream, "seek"):
                    f.stream.seek(0)
            except Exception:
                pass

            try:
                src = Image.open(f.stream)  # type: ignore[arg-type]
            except Exception:
                raise ValueError("이미지 파일을 읽지 못했어요. 다른 파일로 다시 시도해 주세요.")

            rgb = src.convert("RGB")
            src.close()
            try:
                rgb.thumbnail((MULTI_IMAGE_MAX_DIM, MULTI_IMAGE_MAX_DIM), resample=_image_resample())
            except Exception:
                pass

            img_path = tmp_root / f"img_{idx:03d}.jpg"
            rgb.save(img_path, format="JPEG", quality=88, optimize=True)
            rgb.close()
            normalized_paths.append(img_path)

        if not normalized_paths:
            raise ValueError("유효한 이미지가 없습니다.")

        pil_images: list[Image.Image] = []
        first_img: Image.Image | None = None
        try:
            first_img = Image.open(normalized_paths[0]).convert("RGB")
            for p in normalized_paths[1:]:
                pil_images.append(Image.open(p).convert("RGB"))
            first_img.save(out_path, format="PDF", save_all=True, append_images=pil_images)
        finally:
            if first_img is not None:
                try:
                    first_img.close()
                except Exception:
                    pass
            for img in pil_images:
                try:
                    img.close()
                except Exception:
                    pass

        size = int(out_path.stat().st_size) if out_path.exists() else 0
        if size <= 0:
            raise ValueError("PDF 생성에 실패했어요. 다시 시도해 주세요.")
        if size > max_bytes:
            raise ValueError(f"파일이 너무 큽니다. (최대 {max_bytes // (1024*1024)}MB)")

        sha, _checked = _file_sha256_and_size(out_path)
        return size, sha


def _validate_file(file: FileStorage) -> tuple[str, str]:
    """
    반환: (safe_original_filename, mime_type)
    """
    if not file or not file.filename:
        raise ValueError("파일이 없습니다.")
    if _contains_sensitive_filename(file.filename):
        raise ValueError("신분증/주민등록/가족관계 서류는 이곳에 올릴 수 없어요. 필요한 경우 세무사와 별도 안전 채널로 전달해 주세요.")
    raw_original = secure_filename(file.filename) or "evidence"
    detected_ext, detected_mime = _detect_signature(file)
    if not detected_ext or not detected_mime or detected_ext not in ALLOWED_EXTS:
        raise ValueError("허용되지 않는 파일 형식입니다. 이미지/PDF 파일만 업로드해 주세요.")

    # 시그니처 기준으로 확정: 확장자/클라이언트 mimetype 불일치면 시그니처를 우선한다.
    safe_original = _finalize_safe_filename(raw_original, detected_ext)
    safe_mime = _mime_from_ext(detected_ext)
    return safe_original, safe_mime


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
    max_bytes: int | None = None,
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
    max_limit = int(max_bytes) if max_bytes else _max_bytes()

    tmp_path = abs_path.with_suffix(abs_path.suffix + ".part")

    with open(tmp_path, "wb") as f:
        while True:
            chunk = file.stream.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > max_limit:
                try:
                    f.close()
                except Exception:
                    pass
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ValueError(f"파일이 너무 큽니다. (최대 {max_limit // (1024*1024)}MB)")

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


def evidence_abs_path(file_key: str) -> Path:
    return resolve_file_path(file_key)


def attach_evidence_file(
    *,
    user_pk: int,
    evidence_id: int,
    uploaded: FileStorage,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> StoredFile:
    """inbox 업로드 호환용: 저장 + EvidenceItem 메타 업데이트까지 처리."""
    if not uploaded or not uploaded.filename:
        raise ValueError("파일이 없습니다.")

    ev = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not ev:
        raise ValueError("대상을 찾을 수 없습니다.")

    tx = Transaction.query.filter_by(id=ev.transaction_id, user_pk=user_pk).first()
    if not tx:
        raise ValueError("거래를 찾을 수 없습니다.")

    month_key = tx.occurred_at.strftime("%Y-%m")

    old_file_key = str(ev.file_key or "").strip()

    stored = store_evidence_file(
        user_pk=user_pk,
        tx_id=tx.id,
        month_key=month_key,
        file=uploaded,
        max_bytes=max_bytes,
    )

    ev.file_key = stored.file_key
    ev.original_filename = stored.original_filename
    ev.mime_type = stored.mime_type
    ev.size_bytes = int(stored.size_bytes)
    ev.sha256 = stored.sha256
    ev.uploaded_at = utcnow()
    ev.deleted_at = None
    ev.retention_until = default_retention_until()
    ev.status = "attached"
    db.session.commit()

    if old_file_key and old_file_key != str(stored.file_key or "").strip():
        try:
            delete_physical_file(old_file_key)
        except Exception:
            pass
    return stored


def delete_evidence_file(*, user_pk: int, evidence_id: int) -> None:
    """inbox 삭제 호환용: 물리 파일 + EvidenceItem 메타 정리."""
    ev = EvidenceItem.query.filter_by(id=evidence_id, user_pk=user_pk).first()
    if not ev:
        raise ValueError("대상을 찾을 수 없습니다.")

    if ev.file_key:
        delete_physical_file(ev.file_key)

    ev.file_key = None
    ev.original_filename = None
    ev.mime_type = None
    ev.size_bytes = None
    ev.sha256 = None
    ev.uploaded_at = None
    ev.deleted_at = utcnow()
    ev.retention_until = None
    ev.status = "not_needed" if ev.requirement == "not_needed" else "missing"
    db.session.commit()


def purge_expired_evidence(user_pk: int | None = None) -> int:
    """보관 만료 파일 정리(app CLI purge-evidence)."""
    today = date.today()
    q = EvidenceItem.query.filter(EvidenceItem.file_key.isnot(None))
    q = q.filter(EvidenceItem.retention_until.isnot(None), EvidenceItem.retention_until < today)
    if user_pk is not None:
        q = q.filter(EvidenceItem.user_pk == user_pk)

    n = 0
    for ev in q.all():
        if ev.file_key:
            delete_physical_file(ev.file_key)
        ev.file_key = None
        ev.original_filename = None
        ev.mime_type = None
        ev.size_bytes = None
        ev.sha256 = None
        ev.uploaded_at = None
        ev.deleted_at = utcnow()
        ev.retention_until = None
        ev.status = "not_needed" if ev.requirement == "not_needed" else "missing"
        n += 1

    if n:
        db.session.commit()
    return n

# -----------------------------
# Draft evidence helpers (영수증으로 새 지출 등록 등)
# -----------------------------

def store_evidence_draft_file(
    *,
    user_pk: int,
    month_key: str,
    file: FileStorage,
) -> StoredFile:
    """거래(tx) 생성 전 임시로 증빙 파일을 저장한다.

    저장 위치:
      uploads/evidence/u{user_pk}/{month_key}/_draft/<uuid>.<ext>

    이후 tx가 확정되면 move_evidence_file_to_tx()로 tx 폴더로 이동한다.
    """
    original_filename, mime_type = _validate_file(file)

    root = evidence_root()
    rel_dir = Path(f"u{user_pk}") / month_key / "_draft"
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

    file_key = str((rel_dir / save_name).as_posix())
    return StoredFile(
        file_key=file_key,
        abs_path=abs_path,
        original_filename=original_filename,
        mime_type=mime_type,
        size_bytes=size,
        sha256=h.hexdigest(),
    )


def move_evidence_file_to_tx(
    *,
    user_pk: int,
    month_key: str,
    tx_id: int,
    file_key: str,
) -> str:
    """draft file_key를 tx 폴더로 이동하고 새 file_key를 반환한다."""
    if not file_key:
        raise FileNotFoundError("file_key is empty")

    root = evidence_root()
    old_path = resolve_file_path(file_key)
    if not old_path.exists():
        raise FileNotFoundError("draft file not found")

    rel_dir = Path(f"u{user_pk}") / month_key / f"tx{tx_id}"
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    name = old_path.name
    new_path = abs_dir / name
    if new_path.exists():
        new_path = abs_dir / f"{uuid4().hex}{old_path.suffix}"

    os.replace(old_path, new_path)

    return str((rel_dir / new_path.name).as_posix())


def store_evidence_text_file(
    *,
    user_pk: int,
    tx_id: int,
    month_key: str,
    text: str,
    filename: str = "e-receipt.txt",
) -> StoredFile:
    txt = (text or "").strip()
    if not txt:
        raise ValueError("텍스트가 비어있습니다.")

    root = evidence_root()
    rel_dir = Path(f"u{user_pk}") / month_key / f"tx{tx_id}"
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(filename) or "e-receipt.txt"
    if not safe_name.lower().endswith(".txt"):
        safe_name += ".txt"

    token = uuid4().hex
    save_name = f"{token}_{safe_name}"
    abs_path = abs_dir / save_name

    b = txt.encode("utf-8")
    h = hashlib.sha256(b).hexdigest()
    with open(abs_path, "wb") as f:
        f.write(b)

    file_key = str((rel_dir / save_name).as_posix())
    return StoredFile(
        file_key=file_key,
        abs_path=abs_path,
        original_filename=safe_name,
        mime_type="text/plain",
        size_bytes=len(b),
        sha256=h,
    )


def store_evidence_file_multi(
    *,
    user_pk: int,
    tx_id: int,
    month_key: str,
    files: list[FileStorage],
) -> StoredFile:
    if not files:
        raise ValueError("파일이 없습니다.")
    if len(files) == 1:
        return store_evidence_file(user_pk=user_pk, tx_id=tx_id, month_key=month_key, file=files[0])

    root = evidence_root()
    rel_dir = Path(f"u{user_pk}") / month_key / f"tx{tx_id}"
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    token = uuid4().hex
    save_name = f"{token}_receipt.pdf"
    abs_path = abs_dir / save_name
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".part")

    max_bytes = _max_bytes()
    try:
        size, h = _save_multi_images_to_pdf(files=files, out_path=tmp_path, max_bytes=max_bytes)
        os.replace(tmp_path, abs_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    file_key = str((rel_dir / save_name).as_posix())
    return StoredFile(
        file_key=file_key,
        abs_path=abs_path,
        original_filename="receipt.pdf",
        mime_type="application/pdf",
        size_bytes=int(size),
        sha256=h,
    )
    
# -----------------------------
# Draft helpers: receipt -> new transaction
# -----------------------------

def store_evidence_draft_text(
    *,
    user_pk: int,
    month_key: str,
    text: str,
    filename: str = "e-receipt.txt",
) -> StoredFile:
    txt = (text or "").strip()
    if not txt:
        raise ValueError("텍스트가 비어있습니다.")

    root = evidence_root()
    rel_dir = Path(f"u{user_pk}") / month_key / "_draft"
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(filename) or "e-receipt.txt"
    if not safe_name.lower().endswith(".txt"):
        safe_name += ".txt"

    token = uuid4().hex
    save_name = f"{token}_{safe_name}"
    abs_path = abs_dir / save_name

    b = txt.encode("utf-8")
    max_bytes = _max_bytes()
    if len(b) > max_bytes:
        raise ValueError(f"텍스트가 너무 큽니다. (최대 {max_bytes // (1024*1024)}MB)")

    with open(abs_path, "wb") as f:
        f.write(b)

    h = hashlib.sha256(b).hexdigest()
    file_key = str((rel_dir / save_name).as_posix())
    return StoredFile(
        file_key=file_key,
        abs_path=abs_path,
        original_filename=safe_name,
        mime_type="text/plain",
        size_bytes=len(b),
        sha256=h,
    )


def store_evidence_draft_file_multi(
    *,
    user_pk: int,
    month_key: str,
    files: list[FileStorage],
) -> StoredFile:
    if not files:
        raise ValueError("파일이 없습니다.")

    # 단일 파일이면 기존 검증/저장 로직 최대한 재사용
    if len(files) == 1:
        f = files[0]
        original_filename, mime_type = _validate_file(f)

        root = evidence_root()
        rel_dir = Path(f"u{user_pk}") / month_key / "_draft"
        abs_dir = root / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)

        ext = _safe_ext(original_filename)
        token = uuid4().hex
        save_name = f"{token}{ext or ''}"
        abs_path = abs_dir / save_name

        h = hashlib.sha256()
        size = 0
        max_bytes = _max_bytes()
        tmp_path = abs_path.with_suffix(abs_path.suffix + ".part")

        with open(tmp_path, "wb") as out:
            while True:
                chunk = f.stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    try:
                        out.close()
                    except Exception:
                        pass
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise ValueError(f"파일이 너무 큽니다. (최대 {max_bytes // (1024*1024)}MB)")
                h.update(chunk)
                out.write(chunk)

        os.replace(tmp_path, abs_path)

        file_key = str((rel_dir / save_name).as_posix())
        return StoredFile(
            file_key=file_key,
            abs_path=abs_path,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size,
            sha256=h.hexdigest(),
        )

    max_bytes = _max_bytes()

    root = evidence_root()
    rel_dir = Path(f"u{user_pk}") / month_key / "_draft"
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    token = uuid4().hex
    save_name = f"{token}_receipt.pdf"
    abs_path = abs_dir / save_name
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".part")

    try:
        size, h = _save_multi_images_to_pdf(files=files, out_path=tmp_path, max_bytes=max_bytes)
        os.replace(tmp_path, abs_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    file_key = str((rel_dir / save_name).as_posix())
    return StoredFile(
        file_key=file_key,
        abs_path=abs_path,
        original_filename="receipt.pdf",
        mime_type="application/pdf",
        size_bytes=int(size),
        sha256=h,
    )

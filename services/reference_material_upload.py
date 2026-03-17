from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.extensions import db
from domain.models import ReferenceMaterialItem
from services.reference_material_store import (
    delete_reference_material_file,
    resolve_reference_material_path,
    store_reference_material_file,
)


MATERIAL_KIND_LABELS = {
    "reference": "참고자료",
    "note_attachment": "추가설명",
}

REFERENCE_ONLY_LABEL = "참고용"
HANDLING_LABEL = "자동 반영 안 됨"
PURPOSE_LABEL = "세무사 참고용"
MANAGEMENT_LABEL = "공식자료/증빙과 별도 관리"


@dataclass(frozen=True)
class ReferenceMaterialUploadResult:
    item: ReferenceMaterialItem
    kind_label: str


def _normalize_kind(material_kind: str | None) -> str:
    value = (material_kind or "").strip()
    if value not in MATERIAL_KIND_LABELS:
        raise ValueError("자료종류를 다시 선택해 주세요.")
    return value


def _normalize_title(title: str | None, original_filename: str) -> str:
    value = (title or "").strip()
    if value:
        return value[:200]
    return (Path(original_filename).stem or "참고자료")[:200]


def _normalize_note(note: str | None) -> str:
    value = (note or "").strip()
    return value[:4000]


def create_reference_material(
    *,
    user_pk: int,
    material_kind: str,
    uploaded_file,
    title: str | None = None,
    note: str | None = None,
) -> ReferenceMaterialUploadResult:
    normalized_kind = _normalize_kind(material_kind)
    stored = store_reference_material_file(user_pk=user_pk, file=uploaded_file)

    item = ReferenceMaterialItem(
        user_pk=user_pk,
        material_kind=normalized_kind,
        raw_file_key=stored.raw_file_key,
        original_filename=stored.original_filename,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        title=_normalize_title(title, stored.original_filename),
        note=_normalize_note(note),
    )
    db.session.add(item)
    db.session.commit()
    return ReferenceMaterialUploadResult(item=item, kind_label=MATERIAL_KIND_LABELS[normalized_kind])


def reference_material_to_view_model(item: ReferenceMaterialItem) -> dict[str, str | int]:
    return {
        "id": int(item.id),
        "material_kind": item.material_kind,
        "material_kind_label": MATERIAL_KIND_LABELS.get(item.material_kind, "참고자료"),
        "title": item.title or "참고자료",
        "note": item.note or "",
        "original_filename": item.original_filename,
        "mime_type": item.mime_type,
        "size_bytes": int(item.size_bytes or 0),
        "created_at": item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
        "classification_label": REFERENCE_ONLY_LABEL,
        "handling_label": HANDLING_LABEL,
        "purpose_label": PURPOSE_LABEL,
        "management_label": MANAGEMENT_LABEL,
    }


def list_reference_materials(*, user_pk: int, limit: int = 50) -> list[dict[str, str | int]]:
    rows = (
        ReferenceMaterialItem.query.filter_by(user_pk=user_pk)
        .order_by(ReferenceMaterialItem.created_at.desc(), ReferenceMaterialItem.id.desc())
        .limit(limit)
        .all()
    )
    return [reference_material_to_view_model(row) for row in rows]


def get_reference_material_for_user(*, user_pk: int, item_id: int) -> ReferenceMaterialItem | None:
    return ReferenceMaterialItem.query.filter_by(id=item_id, user_pk=user_pk).first()


def get_reference_material_download_path(*, item: ReferenceMaterialItem) -> Path:
    return resolve_reference_material_path(item.raw_file_key)


def delete_reference_material_item_file(*, item: ReferenceMaterialItem) -> None:
    delete_reference_material_file(item.raw_file_key)

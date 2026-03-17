from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for

from core.auth import login_required
from services.reference_material_upload import (
    create_reference_material,
    get_reference_material_download_path,
    get_reference_material_for_user,
    list_reference_materials,
)


web_reference_material_bp = Blueprint("web_reference_material", __name__, url_prefix="/dashboard")


@web_reference_material_bp.get("/reference-materials")
@login_required
def index():
    user_pk = int(session["user_id"])
    items = list_reference_materials(user_pk=user_pk, limit=50)
    return render_template("reference_material/index.html", items=items)


@web_reference_material_bp.post("/reference-materials/upload")
@login_required
def upload():
    user_pk = int(session["user_id"])
    uploaded_file = request.files.get("file")
    material_kind = request.form.get("material_kind")
    title = request.form.get("title")
    note = request.form.get("note")

    try:
        result = create_reference_material(
            user_pk=user_pk,
            material_kind=material_kind,
            uploaded_file=uploaded_file,
            title=title,
            note=note,
        )
    except ValueError as exc:
        flash(str(exc) or "참고자료 업로드에 실패했습니다.", "error")
        return redirect(url_for("web_reference_material.index"))

    object_particle = "를" if result.kind_label.endswith("자료") else "을"
    flash(
        f"{result.kind_label}{object_particle} 참고용으로 보관했습니다. 자동 반영되지 않고 세무사 참고용으로만 관리됩니다.",
        "success",
    )
    return redirect(url_for("web_reference_material.index"))


@web_reference_material_bp.get("/reference-materials/<int:item_id>/download")
@login_required
def download(item_id: int):
    user_pk = int(session["user_id"])
    item = get_reference_material_for_user(user_pk=user_pk, item_id=item_id)
    if not item:
        abort(404)
    path = get_reference_material_download_path(item=item)
    return send_file(
        path,
        as_attachment=True,
        download_name=item.original_filename,
        mimetype=item.mime_type or "application/octet-stream",
        max_age=0,
    )

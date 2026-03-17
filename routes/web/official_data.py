from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for

from core.auth import login_required
from services.cross_validation import build_cross_validation_context, build_official_document_cross_validation
from services.official_data_upload import (
    get_official_data_document_for_user,
    get_official_data_download_path,
    official_data_document_to_view_model,
    process_official_data_upload,
    query_official_data_documents,
)


web_official_data_bp = Blueprint("web_official_data", __name__, url_prefix="/dashboard")


@web_official_data_bp.get("/official-data")
@login_required
def index():
    user_pk = int(session["user_id"])
    context = build_cross_validation_context(user_pk=user_pk)
    documents = []
    for row in query_official_data_documents(user_pk=user_pk, limit=50):
        view = official_data_document_to_view_model(row)
        view["cross_validation"] = build_official_document_cross_validation(document=row, context=context)
        documents.append(view)
    return render_template("official_data/index.html", documents=documents)


@web_official_data_bp.post("/official-data/upload")
@login_required
def upload():
    user_pk = int(session["user_id"])
    uploaded_file = request.files.get("file")
    try:
        result = process_official_data_upload(user_pk=user_pk, uploaded_file=uploaded_file)
        category = "success" if result.document.parse_status == "parsed" else "error"
        flash(f"공식자료 업로드를 처리했습니다. 현재 상태: {result.status_label}", category)
        return redirect(url_for("web_official_data.detail", document_id=result.document.id))
    except ValueError as exc:
        flash(str(exc) or "공식자료 업로드에 실패했습니다.", "error")
        return redirect(url_for("web_official_data.index"))


@web_official_data_bp.get("/official-data/<int:document_id>")
@login_required
def detail(document_id: int):
    user_pk = int(session["user_id"])
    document = get_official_data_document_for_user(user_pk=user_pk, document_id=document_id)
    if not document:
        abort(404)
    document_view = official_data_document_to_view_model(document)
    document_view["cross_validation"] = build_official_document_cross_validation(
        document=document,
        context=build_cross_validation_context(user_pk=user_pk),
    )
    return render_template("official_data/result.html", document=document_view)


@web_official_data_bp.get("/official-data/<int:document_id>/download")
@login_required
def download(document_id: int):
    user_pk = int(session["user_id"])
    document = get_official_data_document_for_user(user_pk=user_pk, document_id=document_id)
    if not document:
        abort(404)
    path = get_official_data_download_path(document=document)
    return send_file(
        path,
        as_attachment=True,
        download_name=document.original_filename,
        mimetype=document.mime_type or "application/octet-stream",
        max_age=0,
    )

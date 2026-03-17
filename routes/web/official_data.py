from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from core.auth import login_required
from services.official_data_extractors import OfficialDataFileError
from services.official_data_effects import summarize_official_data_effects
from services.official_data_upload import (
    build_official_data_result_context,
    get_official_data_document_for_user,
    list_official_data_upload_document_options,
    process_official_data_upload,
)


web_official_data_bp = Blueprint("web_official_data", __name__)


def _selected_document_option(document_type: str | None) -> dict | None:
    hint = str(document_type or "").strip()
    for item in list_official_data_upload_document_options():
        if item["document_type"] == hint:
            return item
    return None


def _render_upload_page(*, document_type_hint: str | None = None, page_status: str | None = None, page_message: str | None = None, status_tone: str = "warn"):
    selected = _selected_document_option(document_type_hint)
    return render_template(
        "official_data/upload.html",
        page_title="공식 자료 올리기",
        page_summary="지원 형식에 맞는 기관 발급 파일만 받아서 핵심 추출값 중심으로 정리해요.",
        document_options=list_official_data_upload_document_options(),
        selected_document_type=(selected["document_type"] if selected else str(document_type_hint or "").strip()),
        selected_document=selected,
        page_status=page_status,
        page_message=page_message,
        status_tone=status_tone,
        supported_formats=["CSV", "XLSX", "텍스트 추출 가능한 PDF"],
        unsupported_formats=["스크린샷 이미지", "사진 촬영본", "스캔 PDF", "암호 걸린 PDF", "편집한 파일"],
    )


@web_official_data_bp.get("/dashboard/official-data/upload")
@login_required
def upload_page():
    return _render_upload_page(document_type_hint=request.args.get("document_type"))


@web_official_data_bp.post("/dashboard/official-data/upload")
@login_required
def upload_submit():
    document_type_hint = request.form.get("document_type") or request.args.get("document_type")
    uploaded = request.files.get("official_data_file")
    if uploaded is None or not str(getattr(uploaded, "filename", "") or "").strip():
        return _render_upload_page(
            document_type_hint=document_type_hint,
            page_status="파일을 먼저 선택해 주세요",
            page_message="지원 형식에 맞는 기관 발급 파일만 올릴 수 있어요.",
            status_tone="warn",
        )

    try:
        outcome = process_official_data_upload(
            user_pk=int(session["user_id"]),
            uploaded_file=uploaded,
            document_type_hint=document_type_hint,
            raw_file_storage_mode="none",
        )
    except OfficialDataFileError as exc:
        return _render_upload_page(
            document_type_hint=document_type_hint,
            page_status="업로드 형식을 다시 확인해 주세요",
            page_message=str(exc),
            status_tone="warn",
        )
    flash(outcome.status_title, "success" if outcome.status_tone == "success" else "warning")
    return redirect(url_for("web_official_data.result_page", document_id=outcome.document.id))


@web_official_data_bp.get("/dashboard/official-data/result/<int:document_id>")
@login_required
def result_page(document_id: int):
    document = get_official_data_document_for_user(document_id=document_id, user_pk=int(session["user_id"]))
    if document is None:
        abort(404)
    context = build_official_data_result_context(document)
    context["official_data_effect_notice"] = summarize_official_data_effects(document=document)
    return render_template("official_data/result.html", **context)

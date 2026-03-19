from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for

from core.auth import login_required
from domain.models import SafeToSpendSettings
from services.cross_validation import build_cross_validation_context, build_official_document_cross_validation
from services.official_data_upload import (
    get_official_data_document_for_user,
    get_official_data_download_path,
    official_data_document_to_view_model,
    process_official_data_upload,
    query_official_data_documents,
)
from services.plan import build_runtime_plan_state


web_official_data_bp = Blueprint("web_official_data", __name__, url_prefix="/dashboard")


def _normalize_meta_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _meta_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _normalize_meta_text(value) in {"1", "true", "yes", "y", "on", "예", "네", "맞음", "registered"}


def _user_profile_meta(user_pk: int) -> dict[str, Any]:
    settings = SafeToSpendSettings.query.get(user_pk)
    if not settings or not isinstance(settings.custom_rates, dict):
        return {}
    meta = settings.custom_rates.get("_meta")
    return meta if isinstance(meta, dict) else {}


def _doc_item(*, title: str, formats: str, reason: str) -> dict[str, str]:
    return {"title": title, "formats": formats, "reason": reason}


def _guidance_context(user_pk: int) -> dict[str, Any]:
    meta = _user_profile_meta(user_pk)
    plan_state = build_runtime_plan_state(user_pk=user_pk)

    insurance_value = _normalize_meta_text(
        meta.get("insurance_type")
        or meta.get("nhis_type")
        or meta.get("health_insurance_type")
    )
    work_value = _normalize_meta_text(
        meta.get("employment_type")
        or meta.get("work_type")
        or meta.get("worker_type")
        or meta.get("income_type")
        or meta.get("occupation_type")
    )
    business_value = _normalize_meta_text(
        meta.get("business_type")
        or meta.get("tax_profile")
        or meta.get("vat_type")
    )

    is_local_insured = insurance_value in {"local", "지역가입자", "지역"}
    is_employee_insured = insurance_value in {"employee", "직장가입자", "직장"}
    is_freelancer = work_value in {
        "freelancer",
        "프리랜서",
        "selfemployed",
        "self-employed",
        "사업소득",
        "3.3",
    }
    is_vat_business = _meta_truthy(meta.get("vat_registered")) or business_value in {
        "vat",
        "vatregistered",
        "부가세",
        "부가세대상",
        "과세사업자",
    }

    recommendation_title = "당신이 입력한 정보 기준 추천 자료"
    recommendation_notes: list[str] = []
    recommended_documents: list[dict[str, str]] = []

    if is_local_insured:
        recommendation_notes.append("지역가입자 기준으로 건강보험 관련 공식자료를 먼저 보는 편이 좋습니다.")
        recommended_documents.extend(
            [
                _doc_item(
                    title="건강보험 납부확인서",
                    formats="PDF",
                    reason="월별 납부 금액과 기준일을 확인하는 데 가장 직접적으로 쓰입니다.",
                ),
                _doc_item(
                    title="건강보험 자격 관련 문서",
                    formats="PDF",
                    reason="자격 상태와 기준일을 같이 봐야 하는 경우가 많습니다.",
                ),
            ]
        )

    if is_employee_insured:
        recommendation_notes.append("직장가입자 기준으로는 원천징수 관련 문서를 우선 확인하는 편이 좋습니다.")
        recommended_documents.append(
            _doc_item(
                title="홈택스 원천징수 관련 문서",
                formats="CSV / XLSX",
                reason="급여·원천징수 흐름을 공식자료 기준으로 먼저 확인할 수 있습니다.",
            )
        )

    if is_freelancer:
        recommendation_notes.append("프리랜서 기준으로는 원천징수와 납부내역을 함께 올리는 편이 가장 실용적입니다.")
        recommended_documents.extend(
            [
                _doc_item(
                    title="홈택스 원천징수 관련 문서",
                    formats="CSV / XLSX",
                    reason="사업소득·원천징수 흐름을 먼저 정리할 때 유용합니다.",
                ),
                _doc_item(
                    title="홈택스 납부내역",
                    formats="CSV / XLSX",
                    reason="실제 납부 금액과 날짜를 거래 기록과 비교하는 데 쓰입니다.",
                ),
            ]
        )

    if is_vat_business:
        recommendation_notes.append("과세사업자/부가세 대상이면 지원 범위 안의 홈택스 납부 자료부터 먼저 맞추는 편이 안전합니다.")
        recommended_documents.append(
            _doc_item(
                title="홈택스 납부내역",
                formats="CSV / XLSX",
                reason="현재 지원 범위 안에서는 세금 납부 흐름부터 공식자료로 맞추는 편이 안전합니다.",
            )
        )

    deduped_recommended: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for item in recommended_documents:
        if item["title"] in seen_titles:
            continue
        deduped_recommended.append(item)
        seen_titles.add(item["title"])

    additional_documents = [
        _doc_item(
            title="건강보험 납부확인서",
            formats="PDF",
            reason="건보료를 직접 확인해야 하거나 지역가입자 여부가 애매할 때 같이 올리면 좋습니다.",
        ),
        _doc_item(
            title="건강보험 자격 관련 문서",
            formats="PDF",
            reason="가입자 유형과 자격 상태를 보완해서 확인할 때 도움이 됩니다.",
        ),
        _doc_item(
            title="홈택스 원천징수 관련 문서",
            formats="CSV / XLSX",
            reason="급여·원천징수 구조가 있는 달이면 추가 확인용으로 같이 쓰기 좋습니다.",
        ),
        _doc_item(
            title="홈택스 납부내역",
            formats="CSV / XLSX",
            reason="실제 납부일과 납부세액을 거래와 같이 볼 때 유용합니다.",
        ),
    ]

    additional_documents = [item for item in additional_documents if item["title"] not in seen_titles]

    baseline_documents = [
        _doc_item(
            title="홈택스 납부내역",
            formats="CSV / XLSX",
            reason="지출 거래와 금액·날짜를 맞춰 보기 가장 쉬운 공식자료입니다.",
        ),
        _doc_item(
            title="홈택스 원천징수 관련 문서",
            formats="CSV / XLSX",
            reason="소득 구조를 확인해야 하는 달이면 먼저 챙길 만한 자료입니다.",
        ),
        _doc_item(
            title="건강보험 납부확인서",
            formats="PDF",
            reason="건보료 확인이 필요하면 우선순위가 높은 자료입니다.",
        ),
    ]

    recommendation_hint = (
        " ".join(recommendation_notes)
        if recommendation_notes
        else "입력된 정보가 아직 없거나 분류가 애매하면 아래 기본 자료부터 먼저 올려 주세요."
    )

    process_items = (
        "업로드한 원본 파일은 공식자료 전용 채널에 따로 보관하고, 문서 종류를 먼저 판별합니다.",
        "지원 범위 안의 자료는 핵심 추출값을 읽고 반영 가능/검토 필요/미지원/읽기 실패 상태로 나눕니다.",
        "비교 가능한 자료는 거래와 참고자료를 기준으로 교차검증 v1 결과를 함께 보여줍니다.",
    )
    storage_items = (
        "원본 파일과 핵심 추출값을 함께 보관합니다.",
        "공식자료는 일반 증빙, 참고자료와 분리된 채널로 관리됩니다.",
        "세무사 패키지에는 목록과 원본 파일이 포함될 수 있지만, 자동 확정을 뜻하지는 않습니다.",
    )
    deletion_items = (
        "현재 main에서는 공식자료 삭제 기능이 아직 연결되지 않았습니다.",
        "잘못 올린 자료가 있어도 바로 없애기보다는 상태와 사유를 먼저 확인한 뒤 다시 업로드하는 흐름을 기준으로 안내합니다.",
    )
    collection_relation_items = (
        "지금은 공식자료를 직접 업로드해야 합니다.",
        "앞으로 프로에서는 자동 수집 가능한 공식자료를 행정 일정에 맞춰 불러오는 기능을 지원할 예정입니다.",
        "자동 수집본이 생기더라도 직접 업로드한 공식자료는 보조, 대체, 충돌 확인용으로 같이 쓸 수 있습니다.",
    )

    return {
        "guidance_recommendation_title": recommendation_title,
        "guidance_recommendation_hint": recommendation_hint,
        "guidance_recommended_documents": deduped_recommended,
        "guidance_additional_documents": additional_documents,
        "guidance_baseline_documents": baseline_documents,
        "guidance_process_items": process_items,
        "guidance_storage_items": storage_items,
        "guidance_deletion_items": deletion_items,
        "guidance_collection_relation_items": collection_relation_items,
        "guidance_show_pro_notice": plan_state.current_plan_code != "pro",
        "guidance_plan_label": plan_state.current_plan_label,
    }


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
    return render_template(
        "official_data/index.html",
        documents=documents,
        **_guidance_context(user_pk),
    )


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

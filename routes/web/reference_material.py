from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for

from core.auth import login_required
from domain.models import SafeToSpendSettings
from services.reference_material_upload import (
    create_reference_material,
    get_reference_material_download_path,
    get_reference_material_for_user,
    list_reference_materials,
)


web_reference_material_bp = Blueprint("web_reference_material", __name__, url_prefix="/dashboard")


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

    recommendation_notes: list[str] = []
    recommended_documents: list[dict[str, str]] = []

    if is_local_insured:
        recommendation_notes.append("지역가입자 기준으로는 건강보험 납부·자격 상태를 설명하는 보조 자료를 같이 남겨 두는 편이 좋습니다.")
        recommended_documents.extend(
            [
                _doc_item(
                    title="건강보험 납부/자격 상태 설명 메모",
                    formats="PDF / TXT / JPG / PNG",
                    reason="납부확인서만으로는 설명되지 않는 월별 배경이나 자격 변동 메모를 같이 남길 수 있습니다.",
                ),
                _doc_item(
                    title="지역가입 변경 배경 설명 자료",
                    formats="PDF / XLSX / CSV",
                    reason="월별 변동 사유나 세무사에게 같이 전달할 보조 표를 붙일 때 유용합니다.",
                ),
            ]
        )

    if is_employee_insured:
        recommendation_notes.append("직장가입자 기준으로는 급여·공제 구조를 설명하는 메모를 같이 올리는 편이 실용적입니다.")
        recommended_documents.extend(
            [
                _doc_item(
                    title="급여/공제 구조 설명 메모",
                    formats="PDF / TXT",
                    reason="원천징수 문서와 같이 봐야 하는 추가 설명을 정리할 때 적합합니다.",
                ),
                _doc_item(
                    title="회사 처리와 다른 개인 사정 설명 자료",
                    formats="PDF / JPG / PNG",
                    reason="회사 기준 처리와 실제 개인 상황이 다를 때 보조 설명 자료로 남길 수 있습니다.",
                ),
            ]
        )

    if is_freelancer:
        recommendation_notes.append("프리랜서 기준으로는 수입 구조와 경비 배경을 설명하는 자료를 따로 보관하는 편이 좋습니다.")
        recommended_documents.extend(
            [
                _doc_item(
                    title="수입 구조 설명 자료",
                    formats="PDF / XLSX / CSV",
                    reason="여러 지급처나 정산 방식이 있는 경우 세무사에게 맥락을 전달하기 쉽습니다.",
                ),
                _doc_item(
                    title="경비 설명 자료",
                    formats="PDF / JPG / PNG",
                    reason="거래만으로 설명하기 어려운 지출 배경을 참고자료로 남길 수 있습니다.",
                ),
            ]
        )

    if is_vat_business:
        recommendation_notes.append("과세사업자/부가세 대상이면 매출·매입 구조를 설명하는 보조 자료를 함께 올려 두는 편이 안전합니다.")
        recommended_documents.append(
            _doc_item(
                title="매출/매입 구조 설명 메모",
                formats="PDF / XLSX / CSV",
                reason="공식자료와 별도로 거래 묶음의 배경을 정리해서 전달할 때 유용합니다.",
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
            title="거래 설명 메모",
            formats="PDF / TXT",
            reason="거래명만으로 이해되지 않는 배경을 짧게 정리해 둘 수 있습니다.",
        ),
        _doc_item(
            title="경비 설명 자료",
            formats="PDF / JPG / PNG",
            reason="업무 관련성이나 사용 맥락을 추가로 설명할 때 적합합니다.",
        ),
        _doc_item(
            title="수입 구조 설명 자료",
            formats="PDF / XLSX / CSV",
            reason="입금 패턴, 지급처, 정산 주기 같은 배경 설명을 함께 남길 수 있습니다.",
        ),
        _doc_item(
            title="납부/자격 상태 보조 설명 자료",
            formats="PDF / TXT / JPG / PNG",
            reason="공식자료만으로 설명되지 않는 상태 변동을 보조 설명으로 남길 수 있습니다.",
        ),
        _doc_item(
            title="세무사에게 맥락 전달이 필요한 파일",
            formats="PDF / XLSX / CSV / TXT / JPG / PNG",
            reason="자동 반영보다 설명 전달이 중요한 자료는 이 채널에 두는 편이 맞습니다.",
        ),
    ]
    additional_documents = [item for item in additional_documents if item["title"] not in seen_titles]

    baseline_documents = [
        _doc_item(
            title="직접 정리한 메모 PDF",
            formats="PDF",
            reason="공식자료나 거래와 함께 볼 설명을 가장 간단하게 정리할 수 있습니다.",
        ),
        _doc_item(
            title="정리 엑셀/CSV",
            formats="XLSX / CSV",
            reason="거래 묶음, 수입 구조, 경비 메모를 표 형태로 전달할 때 유용합니다.",
        ),
        _doc_item(
            title="거래 배경 설명 텍스트",
            formats="TXT",
            reason="어떤 자료부터 올려야 할지 애매할 때도 가장 가볍게 시작할 수 있습니다.",
        ),
    ]

    recommendation_hint = (
        " ".join(recommendation_notes)
        if recommendation_notes
        else "입력된 정보가 아직 없거나 분류가 애매하면 아래 기본 자료부터 먼저 올려 주세요."
    )

    difference_items = (
        "공식자료와 달리 자동 구조화나 자동 확정의 1차 기준으로 쓰이지 않습니다.",
        "세무사 참고용, 사용자 설명 보조용 채널로 유지됩니다.",
        "교차검증에서 직접 확정 근거가 되기보다 보조 비교나 설명 자료로만 쓰일 수 있습니다.",
    )
    process_items = (
        "업로드한 원본 파일을 참고자료 채널에 그대로 보관합니다.",
        "자동 반영되지 않고, 거래나 공식자료의 결과를 바로 바꾸지 않습니다.",
        "필요하면 세무사 패키지나 공식자료 검토 과정에서 설명 보조 자료로 같이 볼 수 있습니다.",
    )
    storage_items = (
        "공식자료, 증빙 자료와 분리된 참고자료 채널에 보관됩니다.",
        "원본 파일과 제목, 메모를 함께 유지해 이후 설명 맥락을 다시 볼 수 있게 합니다.",
        "현재 범위에서는 설명 보조 자료로만 관리됩니다.",
    )
    deletion_items = (
        "현재 main에서는 참고자료 삭제 기능이 아직 연결되지 않았습니다.",
        "잘못 올린 자료가 있으면 제목과 메모를 분명히 남기고, 이후 정리 기준에 맞춰 다시 업로드하는 흐름을 전제로 안내합니다.",
    )

    return {
        "guidance_recommendation_title": "당신이 입력한 정보 기준 추천 자료",
        "guidance_recommendation_hint": recommendation_hint,
        "guidance_recommended_documents": deduped_recommended,
        "guidance_additional_documents": additional_documents,
        "guidance_baseline_documents": baseline_documents,
        "guidance_difference_items": difference_items,
        "guidance_process_items": process_items,
        "guidance_storage_items": storage_items,
        "guidance_deletion_items": deletion_items,
    }


@web_reference_material_bp.get("/reference-materials")
@login_required
def index():
    user_pk = int(session["user_id"])
    items = list_reference_materials(user_pk=user_pk, limit=50)
    return render_template("reference_material/index.html", items=items, **_guidance_context(user_pk))


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

from __future__ import annotations

import re
from typing import Any

from core.extensions import db
from core.time import utcnow
from domain.models import NhisBillHistory, NhisUserProfile


MEMBER_TYPES = {"regional", "employee", "dependent", "unknown"}


def _month_key_now() -> str:
    return utcnow().strftime("%Y-%m")


def _safe_int(raw: Any) -> int | None:
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if not s:
        return None
    try:
        n = int(float(s))
    except Exception:
        return None
    if n < 0:
        return 0
    return n


def _safe_bool(raw: Any) -> bool | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in {"1", "true", "yes", "on", "y"}:
        return True
    if s in {"0", "false", "no", "off", "n"}:
        return False
    return None


def _sanitize_month_key(raw: Any) -> str:
    s = str(raw or "").strip()
    if len(s) == 7 and s[4] == "-":
        y, m = s.split("-", 1)
        try:
            yy = int(y)
            mm = int(m)
            if 2000 <= yy <= 2100 and 1 <= mm <= 12:
                return f"{yy:04d}-{mm:02d}"
        except Exception:
            pass
    return _month_key_now()


def get_or_create_nhis_profile(user_pk: int) -> NhisUserProfile:
    row = NhisUserProfile.query.filter_by(user_pk=int(user_pk)).first()
    if row:
        return row
    row = NhisUserProfile(
        user_pk=int(user_pk),
        member_type="unknown",
        target_month=_month_key_now(),
    )
    db.session.add(row)
    db.session.commit()
    return row


def nhis_profile_to_dict(row: NhisUserProfile | None) -> dict[str, Any]:
    if not row:
        return {
            "member_type": "unknown",
            "target_month": _month_key_now(),
            "household_has_others": None,
            "annual_income_krw": None,
            "salary_monthly_krw": None,
            "non_salary_annual_income_krw": None,
            "property_tax_base_total_krw": None,
            "rent_deposit_krw": None,
            "rent_monthly_krw": None,
            "has_reduction_or_relief": None,
            "has_housing_loan_deduction": None,
            "last_bill_total_krw": None,
            "last_bill_health_only_krw": None,
            "last_bill_score_points": None,
            "bill_history": [],
            "updated_at": None,
        }
    return {
        "member_type": str(row.member_type or "unknown"),
        "target_month": _sanitize_month_key(row.target_month),
        "household_has_others": row.household_has_others,
        "annual_income_krw": row.annual_income_krw,
        "salary_monthly_krw": row.salary_monthly_krw,
        "non_salary_annual_income_krw": row.non_salary_annual_income_krw,
        "property_tax_base_total_krw": row.property_tax_base_total_krw,
        "rent_deposit_krw": row.rent_deposit_krw,
        "rent_monthly_krw": row.rent_monthly_krw,
        "has_reduction_or_relief": row.has_reduction_or_relief,
        "has_housing_loan_deduction": row.has_housing_loan_deduction,
        "last_bill_total_krw": row.last_bill_total_krw,
        "last_bill_health_only_krw": row.last_bill_health_only_krw,
        "last_bill_score_points": row.last_bill_score_points,
        "bill_history": [],
        "updated_at": row.updated_at,
    }


def _safe_year(raw: Any) -> int | None:
    n = _safe_int(raw)
    if n is None:
        return None
    if 2000 <= n <= 2100:
        return n
    return None


def _safe_month(raw: Any) -> int:
    n = _safe_int(raw)
    if n is None:
        return 0
    if 1 <= n <= 12:
        return n
    return 0


def list_nhis_bill_history(user_pk: int) -> list[dict[str, Any]]:
    rows = (
        NhisBillHistory.query.filter(NhisBillHistory.user_pk == int(user_pk))
        .order_by(NhisBillHistory.bill_year.desc(), NhisBillHistory.bill_month.desc(), NhisBillHistory.id.desc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": int(row.id),
                "bill_year": int(row.bill_year),
                "bill_month": int(row.bill_month or 0),
                "total_krw": row.total_krw,
                "health_only_krw": row.health_only_krw,
                "score_points": row.score_points,
                "updated_at": row.updated_at,
            }
        )
    return out


def _collect_history_rows_from_form(form_data: dict[str, Any]) -> list[dict[str, int | None]]:
    # history_year_1, history_month_1, history_total_krw_1 ...
    rows: list[dict[str, int | None]] = []
    max_rows = _safe_int(form_data.get("history_rows")) or 0
    max_rows = max(0, min(12, int(max_rows)))

    # 프론트 인덱스가 연속적이지 않아도(예: 1,3) 누락 없이 수집한다.
    detected_indices: set[int] = set()
    pattern = re.compile(r"^history_(?:year|month|total_krw|health_only_krw|score_points)_(\d+)$")
    for key in form_data.keys():
        m = pattern.match(str(key))
        if not m:
            continue
        try:
            idx = int(m.group(1))
        except Exception:
            continue
        if idx > 0:
            detected_indices.add(idx)

    if max_rows > 0:
        detected_indices.update(range(1, max_rows + 1))

    indices = sorted(detected_indices)
    if not indices:
        return rows
    if len(indices) > 12:
        indices = indices[:12]

    for idx in indices:
        year = _safe_year(form_data.get(f"history_year_{idx}"))
        if year is None:
            continue
        month = _safe_month(form_data.get(f"history_month_{idx}"))
        total_krw = _safe_int(form_data.get(f"history_total_krw_{idx}"))
        health_only_krw = _safe_int(form_data.get(f"history_health_only_krw_{idx}"))
        score_points = _safe_int(form_data.get(f"history_score_points_{idx}"))
        if total_krw is None and health_only_krw is None and score_points is None:
            continue
        rows.append(
            {
                "bill_year": int(year),
                "bill_month": int(month),
                "total_krw": total_krw,
                "health_only_krw": health_only_krw,
                "score_points": score_points,
            }
        )
    # (연도, 월) 중복 정리: 나중 값 우선
    dedup: dict[tuple[int, int], dict[str, int | None]] = {}
    for row in rows:
        key = (int(row["bill_year"]), int(row["bill_month"] or 0))
        dedup[key] = row
    return [dedup[k] for k in sorted(dedup.keys(), key=lambda x: (x[0], x[1]))]


def save_nhis_bill_history_from_form(user_pk: int, form_data: dict[str, Any]) -> tuple[bool, str]:
    try:
        rows = _collect_history_rows_from_form(form_data)
        if "history_rows" not in form_data:
            return True, "변경 없음"

        if not rows:
            # 화면에서 이력 칸을 모두 비운 상태로 저장하면 기존 이력도 함께 비운다.
            NhisBillHistory.query.filter(NhisBillHistory.user_pk == int(user_pk)).delete()
            db.session.commit()
            return True, "과거 고지 이력을 비웠어요."

        # 사용자가 화면에서 보여준 목록 기준으로 동기화한다.
        NhisBillHistory.query.filter(NhisBillHistory.user_pk == int(user_pk)).delete()
        for data in rows:
            db.session.add(
                NhisBillHistory(
                    user_pk=int(user_pk),
                    bill_year=int(data["bill_year"]),
                    bill_month=int(data["bill_month"] or 0),
                    total_krw=data.get("total_krw"),
                    health_only_krw=data.get("health_only_krw"),
                    score_points=data.get("score_points"),
                    updated_at=utcnow(),
                )
            )
        db.session.commit()
        return True, "과거 고지 이력을 저장했어요."
    except Exception:
        db.session.rollback()
        return False, "과거 고지 이력을 저장하지 못했어요. 잠시 후 다시 시도해 주세요."


def save_nhis_profile_from_form(
    user_pk: int,
    form_data: dict[str, Any],
    *,
    allow_membership_only: bool = False,
) -> tuple[bool, str]:
    try:
        row = get_or_create_nhis_profile(user_pk)

        member_type = str(form_data.get("member_type") or "unknown").strip().lower()
        if member_type not in {"regional", "employee", "dependent"}:
            return False, "가입유형은 지역/직장/피부양자 중에서 선택해 주세요."

        if bool(allow_membership_only):
            row.member_type = member_type
            row.target_month = _sanitize_month_key(form_data.get("target_month"))
            row.updated_at = utcnow()
            db.session.add(row)
            db.session.commit()
            return True, "가입유형을 저장했어요."

        salary_monthly_krw = _safe_int(form_data.get("salary_monthly_krw"))
        annual_income_krw = _safe_int(form_data.get("annual_income_krw"))
        non_salary_annual_income_krw = _safe_int(form_data.get("non_salary_annual_income_krw"))
        property_tax_base_total_krw = _safe_int(form_data.get("property_tax_base_total_krw"))
        if member_type == "employee":
            missing: list[str] = []
            if (salary_monthly_krw or 0) <= 0:
                missing.append("직장 월 보수")
            if non_salary_annual_income_krw is None:
                missing.append("보수 외 소득(연)")
            if missing:
                return False, f"직장가입자 99% 필수 입력이 부족해요: {', '.join(missing)} (없으면 0 입력)"
        elif member_type == "regional":
            missing = []
            if annual_income_krw is None:
                missing.append("연소득 총액")
            if non_salary_annual_income_krw is None:
                missing.append("보수 외 소득(연)")
            if property_tax_base_total_krw is None:
                missing.append("재산세 과세표준 합계")
            if missing:
                return False, f"지역가입자 99% 필수 입력이 부족해요: {', '.join(missing)} (없으면 0 입력)"

        row.member_type = member_type
        row.target_month = _sanitize_month_key(form_data.get("target_month"))

        row.household_has_others = _safe_bool(form_data.get("household_has_others"))
        row.annual_income_krw = annual_income_krw
        row.salary_monthly_krw = salary_monthly_krw
        row.non_salary_annual_income_krw = non_salary_annual_income_krw
        row.property_tax_base_total_krw = property_tax_base_total_krw
        row.rent_deposit_krw = _safe_int(form_data.get("rent_deposit_krw"))
        row.rent_monthly_krw = _safe_int(form_data.get("rent_monthly_krw"))
        row.has_reduction_or_relief = _safe_bool(form_data.get("has_reduction_or_relief"))
        row.has_housing_loan_deduction = _safe_bool(form_data.get("has_housing_loan_deduction"))
        row.last_bill_total_krw = _safe_int(form_data.get("last_bill_total_krw"))
        row.last_bill_health_only_krw = _safe_int(form_data.get("last_bill_health_only_krw"))
        row.last_bill_score_points = _safe_int(form_data.get("last_bill_score_points"))
        row.updated_at = utcnow()

        db.session.add(row)
        db.session.commit()
        # 선택 입력: 연도별 고지 이력(있을 때만 동기화)
        if "history_rows" in form_data:
            ok_hist, msg_hist = save_nhis_bill_history_from_form(user_pk=user_pk, form_data=form_data)
            if not ok_hist:
                return False, msg_hist
        return True, "저장됐어요."
    except Exception:
        db.session.rollback()
        return False, "저장 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."

from __future__ import annotations

import json
import re
from typing import Any

from core.extensions import db
from core.time import utcnow
from domain.models import AssetItem, AssetProfile, NhisUserProfile
from services.input_sanitize import parse_bool_yn, parse_int_krw, safe_str
from services.income_hybrid import (
    INCOME_HYBRID_FIELDS,
    INCOME_HYBRID_INPUT_BASIS_OPTIONS,
    INCOME_HYBRID_SCOPE_OPTIONS,
    get_income_hybrid,
    parse_month_key,
    recommended_income_year,
    set_income_hybrid_enabled,
    save_income_hybrid_entry,
)

ASSET_QUIZ_TOTAL_STEPS = 6
HOUSING_MODES = {"own", "rent", "jeonse", "none", "unknown"}
HOME_RENTAL_MODES = {"unknown", "own_use", "jeonse", "rent", "vacant"}
INCOME_TYPE_OPTIONS = ["salary", "interest", "dividend", "business", "other"]
TRI_STATES = {"yes", "no", "unknown"}
MEMBER_TYPE_OPTIONS = {"regional", "employee", "dependent", "unknown"}


def _safe_int(raw: Any) -> int | None:
    parsed = parse_int_krw(raw)
    if parsed is None:
        return None
    return max(0, int(parsed))


def _safe_float(raw: Any) -> float | None:
    if raw is None:
        return None
    s = safe_str(raw, max_len=64)
    if not s:
        return None
    s = s.replace(",", "")
    try:
        n = float(s)
    except Exception:
        return None
    return max(0.0, n)


def _safe_bool(raw: Any) -> bool | None:
    return parse_bool_yn(raw)


def _safe_tri_state(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in TRI_STATES:
        return s
    b = _safe_bool(raw)
    if b is True:
        return "yes"
    if b is False:
        return "no"
    return "unknown"


def _resolve_member_type_from_hints(*, company_nhis: str, dependent_nhis: str) -> str:
    company = _safe_tri_state(company_nhis)
    dependent = _safe_tri_state(dependent_nhis)
    if dependent == "yes":
        return "dependent"
    if company == "yes":
        return "employee"
    return "regional"


def _normalize_member_type(raw: Any) -> str:
    member_type = str(raw or "").strip().lower()
    if member_type in MEMBER_TYPE_OPTIONS:
        return member_type
    return "unknown"


def _sync_member_type_direct(*, user_pk: int, member_type: str, month_key: str | None = None) -> None:
    safe_member_type = _normalize_member_type(member_type)
    row = NhisUserProfile.query.filter_by(user_pk=int(user_pk)).first()
    if row is None:
        row = NhisUserProfile(
            user_pk=int(user_pk),
            member_type=str(safe_member_type),
            target_month=str(month_key or _default_month_key()),
        )
    elif str(row.member_type or "").strip().lower() != safe_member_type:
        row.member_type = str(safe_member_type)
    if month_key:
        row.target_month = str(month_key)
    row.updated_at = utcnow()
    db.session.add(row)


def _sync_member_type_from_hints(*, user_pk: int, company_nhis: str, dependent_nhis: str, month_key: str | None = None) -> None:
    member_type = _resolve_member_type_from_hints(company_nhis=company_nhis, dependent_nhis=dependent_nhis)
    _sync_member_type_direct(
        user_pk=int(user_pk),
        member_type=member_type,
        month_key=month_key,
    )


def _default_month_key() -> str:
    return utcnow().strftime("%Y-%m")


def get_or_create_asset_profile(user_pk: int) -> AssetProfile:
    row = AssetProfile.query.filter_by(user_pk=int(user_pk)).first()
    if row:
        return row
    row = AssetProfile(user_pk=int(user_pk), quiz_step=1, other_income_types_json=[])
    db.session.add(row)
    db.session.commit()
    return row


def get_asset_item(user_pk: int, kind: str) -> AssetItem | None:
    return (
        AssetItem.query.filter(AssetItem.user_pk == int(user_pk), AssetItem.kind == str(kind))
        .order_by(AssetItem.updated_at.desc(), AssetItem.id.desc())
        .first()
    )


def _normalize_asset_label(label: Any) -> str | None:
    text = safe_str(label, max_len=80)
    return text or None


def _dedupe_asset_items(user_pk: int, *, kind: str | None = None) -> int:
    q = AssetItem.query.filter(AssetItem.user_pk == int(user_pk))
    if kind:
        q = q.filter(AssetItem.kind == str(kind))
    rows = q.order_by(AssetItem.updated_at.desc(), AssetItem.id.desc()).all()

    seen: set[tuple[str, str | None]] = set()
    removed = 0
    touched = False
    for row in rows:
        norm_kind = str(row.kind or "").strip()
        norm_label = _normalize_asset_label(row.label)
        if row.label != norm_label:
            row.label = norm_label
            db.session.add(row)
            touched = True
        key = (norm_kind, norm_label)
        if key in seen:
            db.session.delete(row)
            removed += 1
            touched = True
            continue
        seen.add(key)

    if touched:
        db.session.commit()
    return removed


def list_asset_item_rows(user_pk: int, *, kind: str | None = None) -> list[AssetItem]:
    q = AssetItem.query.filter(AssetItem.user_pk == int(user_pk))
    if kind:
        q = q.filter(AssetItem.kind == str(kind))
    return q.order_by(AssetItem.updated_at.desc(), AssetItem.id.desc()).all()


def get_asset_item_by_id(user_pk: int, item_id: int) -> AssetItem | None:
    return AssetItem.query.filter(
        AssetItem.user_pk == int(user_pk),
        AssetItem.id == int(item_id),
    ).first()


def upsert_asset_item_by_label(
    *,
    user_pk: int,
    kind: str,
    label: str,
    input_json: dict[str, Any] | None = None,
) -> AssetItem:
    norm_label = _normalize_asset_label(label)
    rows = (
        AssetItem.query.filter(
            AssetItem.user_pk == int(user_pk),
            AssetItem.kind == str(kind),
            AssetItem.label == norm_label,
        )
        .order_by(AssetItem.updated_at.desc(), AssetItem.id.desc())
        .all()
    )
    row = rows[0] if rows else None
    if not row:
        row = AssetItem(user_pk=int(user_pk), kind=str(kind), label=norm_label)
        db.session.add(row)
    else:
        for old in rows[1:]:
            db.session.delete(old)
    if input_json is not None:
        row.input_json = dict(input_json)
    row.label = norm_label
    row.updated_at = utcnow()
    db.session.add(row)
    db.session.commit()
    return row


def delete_asset_item(user_pk: int, item_id: int) -> bool:
    row = get_asset_item_by_id(user_pk, item_id)
    if not row:
        return False
    kind = str(row.kind or "").strip()
    label = _normalize_asset_label(row.label)
    q = AssetItem.query.filter(
        AssetItem.user_pk == int(user_pk),
        AssetItem.kind == kind,
    )
    if label is None:
        q = q.filter((AssetItem.label.is_(None)) | (AssetItem.label == ""))
    else:
        q = q.filter(AssetItem.label == label)
    targets = q.order_by(AssetItem.updated_at.desc(), AssetItem.id.desc()).all()
    if not targets:
        targets = [row]
    for target in targets:
        db.session.delete(target)
    db.session.commit()
    return True


def _next_index_label(user_pk: int, *, kind: str, prefix: str) -> str:
    rows = list_asset_item_rows(user_pk, kind=kind)
    max_idx = 0
    for row in rows:
        label = str(row.label or "").strip()
        if not label.startswith(prefix):
            continue
        raw = label.replace(prefix, "", 1).strip()
        try:
            idx = int(raw)
        except Exception:
            continue
        if idx > max_idx:
            max_idx = idx
    return f"{prefix} {max_idx + 1}"


def _label_index(label: str, prefix: str) -> int:
    raw = str(label or "").strip()
    if not raw.startswith(prefix):
        return 10**9
    tail = raw.replace(prefix, "", 1).strip()
    try:
        return int(tail)
    except Exception:
        return 10**9


def _sync_home_representative(user_pk: int) -> None:
    rows = [
        r
        for r in list_asset_item_rows(user_pk, kind="home")
        if str(r.label or "").strip().startswith("보유주택 ")
    ]
    rows.sort(key=lambda r: _label_index(str(r.label or ""), "보유주택"))
    if rows:
        input_json = dict(rows[0].input_json or {})
    else:
        input_json = {
            "address_text": "",
            "home_type": "",
            "area_sqm": None,
            "property_tax_base_manual_krw": None,
            "rental_mode": "unknown",
            "rent_deposit_krw": None,
            "rent_monthly_krw": None,
        }
    upsert_asset_item(user_pk=user_pk, kind="home", label="부동산", input_json=input_json)


def _sync_car_representative(user_pk: int) -> None:
    rows = [
        r
        for r in list_asset_item_rows(user_pk, kind="car")
        if str(r.label or "").strip().startswith("차량 ")
    ]
    rows.sort(key=lambda r: _label_index(str(r.label or ""), "차량"))
    if rows:
        input_json = dict(rows[0].input_json or {})
    else:
        input_json = {"brand": "", "model": "", "year": None}
    upsert_asset_item(user_pk=user_pk, kind="car", label="차량", input_json=input_json)


def _has_labeled_items(user_pk: int, *, kind: str, prefix: str) -> bool:
    rows = list_asset_item_rows(user_pk, kind=kind)
    for row in rows:
        if str(row.label or "").strip().startswith(prefix):
            return True
    return False


def _has_meaningful_home_input(input_json: dict[str, Any]) -> bool:
    if str(input_json.get("address_text") or "").strip():
        return True
    if str(input_json.get("home_type") or "").strip():
        return True
    if _safe_float(input_json.get("area_sqm")) is not None:
        return True
    if _safe_int(input_json.get("property_tax_base_manual_krw")) is not None:
        return True
    if str(input_json.get("rental_mode") or "").strip() not in {"", "unknown"}:
        return True
    if _safe_int(input_json.get("rent_deposit_krw")) is not None:
        return True
    if _safe_int(input_json.get("rent_monthly_krw")) is not None:
        return True
    return False


def _has_meaningful_car_input(input_json: dict[str, Any]) -> bool:
    if str(input_json.get("brand") or "").strip():
        return True
    if str(input_json.get("model") or "").strip():
        return True
    if _safe_int(input_json.get("year")) is not None:
        return True
    return False


def _normalize_home_rental_mode(raw: Any) -> str:
    mode = str(raw or "").strip().lower()
    if mode in HOME_RENTAL_MODES:
        return mode
    return "unknown"


def _normalize_income_types(raw: Any) -> list[str]:
    values: list[Any]
    if isinstance(raw, list):
        values = list(raw)
    elif isinstance(raw, tuple):
        values = list(raw)
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    values = list(parsed)
                else:
                    values = [s]
            except Exception:
                values = [seg.strip() for seg in s.split(",")]
        else:
            values = [seg.strip() for seg in s.split(",")]
    else:
        return []

    out: list[str] = []
    for value in values:
        code = str(value or "").strip().lower()
        if code in INCOME_TYPE_OPTIONS and code not in out:
            out.append(code)
    return out


_NONNEGATIVE_KRW_PATTERN = re.compile(r"^\d+$")
_LARGE_INCOME_CONFIRM_THRESHOLD_KRW = 10_000_000_000


def _parse_non_negative_krw(raw: Any, *, label: str) -> tuple[bool, int | None, str]:
    text = safe_str(raw, max_len=64).replace(",", "")
    if not text:
        return True, None, ""
    if text.startswith("-"):
        return False, None, f"{label}은 0원 이상으로 입력해 주세요."
    if not _NONNEGATIVE_KRW_PATTERN.fullmatch(text):
        return False, None, f"{label}은 숫자만 입력해 주세요."
    value = parse_int_krw(text)
    if value is None:
        return False, None, f"{label}은 숫자만 입력해 주세요."
    if int(value) < 0:
        return False, None, f"{label}은 0원 이상으로 입력해 주세요."
    if int(value) > 1_000_000_000_000:
        return False, None, f"{label} 값이 너무 커요. 숫자를 다시 확인해 주세요."
    return True, int(value), ""


def _save_income_hybrid_from_form(
    *,
    user_pk: int,
    form: Any,
    month_key: str | None = None,
) -> tuple[bool, str]:
    if "income_hybrid_present" not in form:
        return True, ""

    normalized_month = parse_month_key(month_key)
    enabled = _safe_bool(form.get("income_hybrid_enabled")) is True
    fin_income_presence = safe_str(form.get("fin_income_presence"), max_len=16).lower()
    if fin_income_presence in {"yes", "no"}:
        # 빠른 입력(금융소득 있음/없음)은 JS 비활성 환경에서도 의도대로 우선 반영한다.
        enabled = True
    if not enabled:
        # 체크박스가 꺼져 있어도 금액이 들어오면 의도된 입력으로 간주한다.
        for field in INCOME_HYBRID_FIELDS:
            if safe_str(form.get(field), max_len=64).strip():
                enabled = True
                break
    if not enabled:
        set_income_hybrid_enabled(
            user_pk=int(user_pk),
            enabled=False,
            month_key=normalized_month,
        )
        return True, ""

    year_default = recommended_income_year(normalized_month)

    year_raw = safe_str(form.get("income_hybrid_year"), max_len=8)
    if year_raw:
        if not year_raw.isdigit():
            return False, "입력 연도는 숫자 4자리로 입력해 주세요."
        year = int(year_raw)
        if year < 2000 or year > 2100:
            return False, "입력 연도는 2000~2100 범위에서 입력해 주세요."
    else:
        year = int(year_default)

    scope = safe_str(form.get("income_hybrid_scope") or "both", max_len=8).lower()
    if scope not in INCOME_HYBRID_SCOPE_OPTIONS:
        scope = "both"

    input_basis = safe_str(form.get("income_hybrid_input_basis") or "income_amount_pre_tax", max_len=40).lower()
    if input_basis not in INCOME_HYBRID_INPUT_BASIS_OPTIONS:
        input_basis = "income_amount_pre_tax"

    is_pre_tax = _safe_bool(form.get("income_hybrid_is_pre_tax"))
    if is_pre_tax is None:
        is_pre_tax = True

    field_labels = {
        "business_income_amount_krw": "사업소득",
        "fin_income_amount_krw": "금융소득",
        "salary_income_amount_krw": "근로소득",
        "pension_income_amount_krw": "연금소득",
        "other_income_amount_krw": "기타소득",
    }
    parsed_fields: dict[str, int | None] = {}
    for field in INCOME_HYBRID_FIELDS:
        ok, value, err = _parse_non_negative_krw(form.get(field), label=str(field_labels.get(field) or "소득"))
        if not ok:
            return False, err
        parsed_fields[field] = value

    # 빠른 입력 질문(있음/없음/모름)을 우선 적용해 금융소득 값의 모호성을 줄인다.
    if fin_income_presence == "no":
        parsed_fields["fin_income_amount_krw"] = 0
    elif fin_income_presence == "yes":
        fin_income_val = int(parsed_fields.get("fin_income_amount_krw") or 0)
        if fin_income_val <= 0:
            return False, "금융소득이 있으면 연간 금액을 1원 이상 입력해 주세요."
    elif fin_income_presence == "unknown":
        if parsed_fields.get("fin_income_amount_krw") in (None, 0):
            parsed_fields["fin_income_amount_krw"] = None

    has_large_income_value = any(
        (parsed_fields.get(field) or 0) >= _LARGE_INCOME_CONFIRM_THRESHOLD_KRW
        for field in INCOME_HYBRID_FIELDS
    )
    large_income_confirmed = _safe_bool(form.get("income_hybrid_large_confirmed")) is True
    if has_large_income_value and (not large_income_confirmed):
        return False, "입력값이 매우 커요. 값을 다시 확인하고 저장해 주세요."

    save_income_hybrid_entry(
        user_pk=int(user_pk),
        enabled=bool(enabled),
        year=int(year),
        scope=str(scope),
        input_basis=str(input_basis),
        is_pre_tax=bool(is_pre_tax),
        note=safe_str(form.get("income_hybrid_note"), max_len=200, allow_newline=True),
        fields=parsed_fields,
        month_key=normalized_month,
    )
    return True, ""


def _build_income_hybrid_context(
    *,
    user_pk: int,
    month_key: str | None = None,
) -> dict[str, Any]:
    normalized_month = parse_month_key(month_key)
    hybrid = get_income_hybrid(user_pk=int(user_pk), month_key=normalized_month)
    entries = list(hybrid.get("entries") or [])
    recommended = int(hybrid.get("recommended_year") or recommended_income_year(normalized_month))

    selected: dict[str, Any] | None = None
    same_year = [entry for entry in entries if int(entry.get("year") or 0) == recommended]
    if same_year:
        for wanted_scope in ("both", "nhis", "tax"):
            row = next((entry for entry in same_year if str(entry.get("scope") or "") == wanted_scope), None)
            if row is not None:
                selected = row
                break
    if selected is None and entries:
        selected = dict(entries[0])

    active_year = int((selected or {}).get("year") or recommended)
    active_scope = str((selected or {}).get("scope") or "both")
    if active_scope not in INCOME_HYBRID_SCOPE_OPTIONS:
        active_scope = "both"

    input_basis = str((selected or {}).get("input_basis") or "income_amount_pre_tax")
    if input_basis not in INCOME_HYBRID_INPUT_BASIS_OPTIONS:
        input_basis = "income_amount_pre_tax"

    year_candidates = {
        recommended,
        recommended - 1,
        recommended + 1,
        recommended - 2,
        recommended + 2,
        active_year,
    }
    for entry in entries:
        y = int(entry.get("year") or 0)
        if 2000 <= y <= 2100:
            year_candidates.add(y)
    year_options = sorted([y for y in year_candidates if 2000 <= y <= 2100], reverse=True)

    fields: dict[str, int | None] = {}
    for key in INCOME_HYBRID_FIELDS:
        raw_val = (selected or {}).get(key)
        fields[key] = _safe_int(raw_val)

    return {
        "enabled": bool(hybrid.get("enabled") is True),
        "recommended_year": int(recommended),
        "active_year": int(active_year),
        "active_scope": active_scope,
        "input_basis": input_basis,
        "is_pre_tax": bool((selected or {}).get("is_pre_tax") is not False),
        "note": str((selected or {}).get("note") or ""),
        "fields": fields,
        "year_options": year_options,
        "entry_count": len(entries),
        "updated_at": str(hybrid.get("updated_at") or ""),
    }


def upsert_asset_item(
    *,
    user_pk: int,
    kind: str,
    label: str | None = None,
    input_json: dict[str, Any] | None = None,
    estimated_json: dict[str, Any] | None = None,
    basis_json: dict[str, Any] | None = None,
    user_override_json: dict[str, Any] | None = None,
) -> AssetItem:
    row: AssetItem | None = None
    if label is not None:
        norm_label = _normalize_asset_label(label)
        rows = (
            AssetItem.query.filter(
                AssetItem.user_pk == int(user_pk),
                AssetItem.kind == str(kind),
                AssetItem.label == norm_label,
            )
            .order_by(AssetItem.updated_at.desc(), AssetItem.id.desc())
            .all()
        )
        row = rows[0] if rows else None
        if row is not None and len(rows) > 1:
            for old in rows[1:]:
                db.session.delete(old)
    else:
        norm_label = None
    if row is None and label is None:
        row = get_asset_item(user_pk, kind)
    if not row:
        row = AssetItem(user_pk=int(user_pk), kind=str(kind), label=norm_label)
        db.session.add(row)

    if label is not None:
        row.label = norm_label
    if input_json is not None:
        row.input_json = dict(input_json)
    if estimated_json is not None:
        row.estimated_json = dict(estimated_json)
    if basis_json is not None:
        row.basis_json = dict(basis_json)
    if user_override_json is not None:
        row.user_override_json = dict(user_override_json)
    row.updated_at = utcnow()
    db.session.add(row)
    db.session.commit()
    return row


def list_asset_items(user_pk: int) -> dict[str, AssetItem]:
    rows = list_asset_item_rows(user_pk)
    grouped: dict[str, list[AssetItem]] = {}
    for row in rows:
        grouped.setdefault(str(row.kind or ""), []).append(row)

    # 대표 입력값은 "사용자 체감 기준"으로 고정:
    # 다중 항목이 있으면 1번 항목 우선, 없으면 대표 라벨, 마지막으로 최신값.
    preferred_labels: dict[str, tuple[str, ...]] = {
        "home": ("보유주택 1", "부동산"),
        "car": ("차량 1", "차량"),
        "rent": ("전월세",),
    }

    out: dict[str, AssetItem] = {}
    for kind, kind_rows in grouped.items():
        picked: AssetItem | None = None
        for wanted in preferred_labels.get(kind, ()):
            for row in kind_rows:
                if str(row.label or "").strip() == wanted:
                    picked = row
                    break
            if picked is not None:
                break
        out[kind] = picked or kind_rows[0]
    return out


def asset_profile_to_dict(row: AssetProfile | None) -> dict[str, Any]:
    if not row:
        return {
            "completed_at": None,
            "household_has_others": None,
            "dependents_count": None,
            "other_income_types": [],
            "other_income_annual_krw": None,
            "quiz_step": 1,
            "housing_mode": "unknown",
            "has_car": None,
        }
    return {
        "completed_at": row.completed_at,
        "household_has_others": row.household_has_others,
        "dependents_count": row.dependents_count,
        "other_income_types": _normalize_income_types(row.other_income_types_json),
        "other_income_annual_krw": row.other_income_annual_krw,
        "quiz_step": int(row.quiz_step or 1),
        "housing_mode": str(row.housing_mode or "unknown"),
        "has_car": row.has_car,
    }


def asset_item_to_dict(row: AssetItem | None) -> dict[str, Any]:
    if not row:
        return {
            "id": None,
            "kind": "",
            "label": "",
            "input": {},
            "estimated": {},
            "basis": {},
            "override": {},
            "updated_at": None,
        }
    return {
        "id": int(row.id) if row.id is not None else None,
        "kind": str(row.kind or ""),
        "label": str(row.label or ""),
        "input": (row.input_json if isinstance(row.input_json, dict) else {}),
        "estimated": (row.estimated_json if isinstance(row.estimated_json, dict) else {}),
        "basis": (row.basis_json if isinstance(row.basis_json, dict) else {}),
        "override": (row.user_override_json if isinstance(row.user_override_json, dict) else {}),
        "updated_at": row.updated_at,
    }


def _completion_ratio(profile: AssetProfile, items: dict[str, AssetItem]) -> tuple[int, list[str]]:
    checks: list[tuple[bool, str]] = []
    checks.append((profile.household_has_others is not None, "세대 합산 정보"))
    checks.append((profile.dependents_count is not None, "부양가족 정보"))

    housing_mode = str(profile.housing_mode or "unknown")
    checks.append((housing_mode in HOUSING_MODES and housing_mode != "unknown", "거주 형태"))

    home = asset_item_to_dict(items.get("home"))
    rent = asset_item_to_dict(items.get("rent"))
    if housing_mode == "own":
        checks.append((bool(home["input"].get("address_text")), "자가 주소"))
        checks.append((bool(home["input"].get("home_type")), "주택 유형"))
    elif housing_mode in {"rent", "jeonse"}:
        has_rent_info = (
            _safe_int(rent["input"].get("rent_deposit_krw")) is not None
            or _safe_int(rent["input"].get("rent_monthly_krw")) is not None
        )
        checks.append((has_rent_info, "전월세 정보"))

    has_car = profile.has_car
    checks.append((has_car is not None, "차량 보유 여부"))
    car = asset_item_to_dict(items.get("car"))
    if has_car is True:
        checks.append((bool(car["input"].get("brand")), "차량 브랜드"))
        checks.append((bool(car["input"].get("model")), "차량 차종"))
        checks.append((_safe_int(car["input"].get("year")) is not None, "차량 연식"))

    checks.append((len(list(profile.other_income_types_json or [])) > 0, "기타 소득 정보"))

    total = len(checks)
    done = len([1 for ok, _ in checks if ok])
    ratio = int(round((done / total) * 100)) if total > 0 else 0
    missing = [label for ok, label in checks if not ok]
    return max(0, min(100, ratio)), missing


def mark_assets_completed_if_ready(user_pk: int) -> tuple[int, list[str], bool]:
    row = get_or_create_asset_profile(user_pk)
    items = list_asset_items(user_pk)
    ratio, missing = _completion_ratio(row, items)
    completed = ratio >= 80
    if completed and row.completed_at is None:
        row.completed_at = utcnow()
        row.quiz_step = ASSET_QUIZ_TOTAL_STEPS
        row.updated_at = utcnow()
        db.session.add(row)
        db.session.commit()
    return ratio, missing, completed


def _sync_assets_to_nhis_profile(user_pk: int, *, month_key: str | None = None) -> None:
    # 순환 import 방지용 local import
    from services.assets_estimator import build_assets_feedback
    from services.nhis_profile import get_or_create_nhis_profile

    feedback = build_assets_feedback(user_pk=user_pk, month_key=(month_key or _default_month_key()))
    derived = dict(feedback.get("derived_nhis_profile") or {})
    if not derived:
        return

    row = get_or_create_nhis_profile(user_pk)
    changed = False

    def _set_if_diff(attr: str, value: Any) -> None:
        nonlocal changed
        if getattr(row, attr, None) != value:
            setattr(row, attr, value)
            changed = True

    # 자산 입력 기반으로 실제 건보료 추정 로직에서 쓰는 프로필 값을 동기화
    for key in (
        "household_has_others",
        "annual_income_krw",
        "salary_monthly_krw",
        "non_salary_annual_income_krw",
        "property_tax_base_total_krw",
        "rent_deposit_krw",
        "rent_monthly_krw",
        "last_bill_total_krw",
        "last_bill_health_only_krw",
        "last_bill_score_points",
    ):
        if key in derived:
            _set_if_diff(key, derived.get(key))

    member_type = str(derived.get("member_type") or "").strip().lower()
    if member_type in {"regional", "employee", "dependent", "unknown"}:
        _set_if_diff("member_type", member_type)

    target_month = str(derived.get("target_month") or (month_key or "")).strip()
    if target_month:
        _set_if_diff("target_month", target_month)

    if changed:
        row.updated_at = utcnow()
        db.session.add(row)
        db.session.commit()


def sync_assets_to_nhis_profile(user_pk: int, *, month_key: str | None = None) -> bool:
    """자산 입력을 NhisUserProfile에 동기화한다.

    예외는 내부에서 롤백하고 False를 반환해 호출부가 안전하게 폴백할 수 있게 한다.
    """
    try:
        _sync_assets_to_nhis_profile(user_pk=user_pk, month_key=month_key)
        return True
    except Exception:
        db.session.rollback()
        return False


def save_assets_quiz_step(
    user_pk: int,
    step: int,
    form: Any,
    *,
    month_key: str | None = None,
) -> tuple[bool, str, int]:
    try:
        _dedupe_asset_items(user_pk)
        row = get_or_create_asset_profile(user_pk)
        current_step = max(1, min(ASSET_QUIZ_TOTAL_STEPS, int(step or 1)))

        if current_step == 1:
            row.household_has_others = _safe_bool(form.get("household_has_others"))
            row.dependents_count = _safe_int(form.get("dependents_count"))
            if ("company_nhis" in form) or ("dependent_nhis" in form):
                _sync_member_type_from_hints(
                    user_pk=user_pk,
                    company_nhis=_safe_tri_state(form.get("company_nhis")),
                    dependent_nhis=_safe_tri_state(form.get("dependent_nhis")),
                    month_key=month_key,
                )
        elif current_step == 2:
            mode = str(form.get("housing_mode") or "unknown").strip().lower()
            row.housing_mode = mode if mode in HOUSING_MODES else "unknown"
        elif current_step == 3:
            home_count = _safe_int(form.get("home_count"))
            housing_mode = str(row.housing_mode or "unknown")
            if home_count is None:
                home_count = (1 if housing_mode == "own" else 0)
            home_count = max(0, min(3, int(home_count)))
            if housing_mode == "own" and home_count <= 0:
                home_count = 1

            kept_labels: set[str] = set()
            first_home_input: dict[str, Any] | None = None
            for idx in range(1, home_count + 1):
                address = str(form.get(f"home_address_{idx}") or "").strip()
                home_type = str(form.get(f"home_type_{idx}") or "").strip().lower()
                area_sqm = _safe_float(form.get(f"home_area_sqm_{idx}"))
                # 1번은 기존 단일 필드명과 호환
                if idx == 1:
                    address = address or str(form.get("home_address") or "").strip()
                    home_type = home_type or str(form.get("home_type") or "").strip().lower()
                    area_sqm = area_sqm if area_sqm is not None else _safe_float(form.get("home_area_sqm"))

                input_json = {
                    "address_text": address,
                    "home_type": home_type,
                    "area_sqm": area_sqm,
                }
                label = f"보유주택 {idx}"
                kept_labels.add(label)
                upsert_asset_item_by_label(
                    user_pk=user_pk,
                    kind="home",
                    label=label,
                    input_json=input_json,
                )
                if idx == 1:
                    first_home_input = dict(input_json)

            # 기존 보유주택 라벨 중 count 밖의 항목 정리
            for old in list_asset_item_rows(user_pk, kind="home"):
                label = str(old.label or "").strip()
                if label.startswith("보유주택 ") and label not in kept_labels:
                    db.session.delete(old)
            db.session.commit()

            # 기존 단일 페이지와의 호환용 대표값 동기화
            if first_home_input is not None:
                upsert_asset_item(
                    user_pk=user_pk,
                    kind="home",
                    label="부동산",
                    input_json=first_home_input,
                )
            else:
                _sync_home_representative(user_pk)
        elif current_step == 4:
            # 자가인 경우 전월세 입력은 생략한다.
            if str(row.housing_mode or "unknown") == "own":
                input_json = {
                    "rent_deposit_krw": None,
                    "rent_monthly_krw": None,
                }
            else:
                rent_mode = str(form.get("rent_input_mode") or row.housing_mode or "unknown").strip().lower()
                if rent_mode not in {"jeonse", "rent", "none", "unknown"}:
                    rent_mode = "unknown"
                if rent_mode in HOUSING_MODES:
                    row.housing_mode = rent_mode

                if rent_mode == "jeonse":
                    input_json = {
                        "rent_deposit_krw": _safe_int(form.get("rent_deposit_krw")),
                        "rent_monthly_krw": None,
                    }
                elif rent_mode == "rent":
                    input_json = {
                        "rent_deposit_krw": _safe_int(form.get("rent_deposit_krw")),
                        "rent_monthly_krw": _safe_int(form.get("rent_monthly_krw")),
                    }
                else:
                    input_json = {
                        "rent_deposit_krw": None,
                        "rent_monthly_krw": None,
                    }
            upsert_asset_item(user_pk=user_pk, kind="rent", label="전월세", input_json=input_json)
        elif current_step == 5:
            has_car = str(form.get("has_car") or "").strip().lower()
            if has_car in {"yes", "y", "1", "true", "on"}:
                car_count = _safe_int(form.get("car_count"))
                if car_count is None:
                    car_count = 1
                car_count = max(1, min(3, int(car_count)))
                row.has_car = True

                kept_labels: set[str] = set()
                first_car_input: dict[str, Any] | None = None
                for idx in range(1, car_count + 1):
                    brand = str(form.get(f"car_brand_{idx}") or "").strip()
                    model = str(form.get(f"car_model_{idx}") or "").strip()
                    year = _safe_int(form.get(f"car_year_{idx}"))
                    if idx == 1:
                        brand = brand or str(form.get("car_brand") or "").strip()
                        model = model or str(form.get("car_model") or "").strip()
                        year = year if year is not None else _safe_int(form.get("car_year"))
                    input_json = {"brand": brand, "model": model, "year": year}
                    label = f"차량 {idx}"
                    kept_labels.add(label)
                    upsert_asset_item_by_label(
                        user_pk=user_pk,
                        kind="car",
                        label=label,
                        input_json=input_json,
                    )
                    if idx == 1:
                        first_car_input = dict(input_json)

                for old in list_asset_item_rows(user_pk, kind="car"):
                    label = str(old.label or "").strip()
                    if label.startswith("차량 ") and label not in kept_labels:
                        db.session.delete(old)
                db.session.commit()

                if first_car_input is not None:
                    upsert_asset_item(
                        user_pk=user_pk,
                        kind="car",
                        label="차량",
                        input_json=first_car_input,
                    )
                else:
                    _sync_car_representative(user_pk)
            elif has_car in {"no", "n", "0", "false", "off"}:
                row.has_car = False
                for old in list_asset_item_rows(user_pk, kind="car"):
                    db.session.delete(old)
                db.session.commit()
            else:
                row.has_car = None
        elif current_step == 6:
            selected: list[str] = []
            for option in INCOME_TYPE_OPTIONS:
                raw = form.get(f"other_income_{option}")
                if _safe_bool(raw) is True:
                    selected.append(option)
            row.other_income_types_json = selected
            row.other_income_annual_krw = _safe_int(form.get("other_income_annual_krw"))

        next_step = current_step + 1
        # 자가 선택 시 Step4(전월세)는 자동 건너뛴다.
        if current_step == 3 and str(row.housing_mode or "unknown") == "own":
            next_step = 5
        row.quiz_step = min(ASSET_QUIZ_TOTAL_STEPS, max(1, int(next_step)))
        row.updated_at = utcnow()
        db.session.add(row)
        db.session.commit()
        try:
            _sync_assets_to_nhis_profile(user_pk, month_key=month_key)
        except Exception:
            db.session.rollback()
        return True, "저장됐어요. 정확도가 올라가고 있어요.", int(row.quiz_step or next_step)
    except Exception:
        db.session.rollback()
        return False, "저장 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", max(1, int(step or 1))


def save_assets_page(
    user_pk: int,
    form: Any,
    *,
    month_key: str | None = None,
) -> tuple[bool, str]:
    try:
        _dedupe_asset_items(user_pk)
        # 순환 import 방지
        from services.nhis_profile import save_nhis_bill_history_from_form

        row = get_or_create_asset_profile(user_pk)
        def _get_list(name: str) -> list[Any]:
            if hasattr(form, "getlist"):
                return list(form.getlist(name))
            value = form.get(name) if hasattr(form, "get") else None
            return [] if value is None else [value]

        def _get_at(values: list[Any], idx: int, default: Any = "") -> Any:
            if idx < 0 or idx >= len(values):
                return default
            return values[idx]

        action_raw = safe_str(form.get("action") or "save_main", max_len=64).lower()
        action = action_raw
        action_item_id = None
        if ":" in action_raw:
            maybe_action, maybe_item_id = action_raw.split(":", 1)
            maybe_action = maybe_action.strip().lower()
            if maybe_action in {"update_item", "delete_item"}:
                action = maybe_action
                action_item_id = _safe_int(maybe_item_id)

        if action == "delete_item":
            item_id = action_item_id or _safe_int(form.get("_target_item_id")) or _safe_int(form.get("item_id"))
            if not item_id:
                return False, "삭제할 항목을 찾지 못했어요."
            row_to_delete = get_asset_item_by_id(user_pk, int(item_id))
            deleted = delete_asset_item(user_pk, int(item_id))
            if not deleted:
                return False, "이미 삭제됐거나 권한이 없는 항목이에요."
            if row_to_delete and row_to_delete.kind == "home":
                _sync_home_representative(user_pk)
            if row_to_delete and row_to_delete.kind == "car":
                _sync_car_representative(user_pk)
            try:
                _sync_assets_to_nhis_profile(user_pk, month_key=month_key)
            except Exception:
                db.session.rollback()
            return True, "항목을 삭제했어요."

        if action == "save_history_only":
            if "history_rows" not in form:
                return False, "저장할 과거 고지 이력이 없어요."
            ok_hist, msg_hist = save_nhis_bill_history_from_form(user_pk=user_pk, form_data=form)
            if not ok_hist:
                return False, msg_hist
            return True, "과거 고지 이력을 저장했어요."

        if action == "add_home_item":
            label = _next_index_label(user_pk, kind="home", prefix="보유주택")
            rental_mode = _normalize_home_rental_mode(form.get("add_home_rental_mode"))
            input_json = {
                "address_text": str(form.get("add_home_address") or "").strip(),
                "home_type": str(form.get("add_home_type") or "").strip().lower(),
                "area_sqm": _safe_float(form.get("add_home_area_sqm")),
                "property_tax_base_manual_krw": _safe_int(form.get("add_home_property_tax_base_manual_krw")),
                "rental_mode": rental_mode,
                "rent_deposit_krw": (_safe_int(form.get("add_home_rent_deposit_krw")) if rental_mode in {"jeonse", "rent"} else None),
                "rent_monthly_krw": (_safe_int(form.get("add_home_rent_monthly_krw")) if rental_mode == "rent" else None),
            }
            if not _has_meaningful_home_input(input_json):
                return False, "추가할 주택 정보를 한 개 이상 입력해 주세요."
            upsert_asset_item_by_label(user_pk=user_pk, kind="home", label=label, input_json=input_json)
            _sync_home_representative(user_pk)
            try:
                _sync_assets_to_nhis_profile(user_pk, month_key=month_key)
            except Exception:
                db.session.rollback()
            return True, "보유 주택 항목을 추가했어요."

        if action == "add_car_item":
            label = _next_index_label(user_pk, kind="car", prefix="차량")
            input_json = {
                "brand": str(form.get("add_car_brand") or "").strip(),
                "model": str(form.get("add_car_model") or "").strip(),
                "year": _safe_int(form.get("add_car_year")),
            }
            if not _has_meaningful_car_input(input_json):
                return False, "추가할 차량 정보를 한 개 이상 입력해 주세요."
            row.has_car = True
            db.session.add(row)
            db.session.commit()
            upsert_asset_item_by_label(user_pk=user_pk, kind="car", label=label, input_json=input_json)
            _sync_car_representative(user_pk)
            try:
                _sync_assets_to_nhis_profile(user_pk, month_key=month_key)
            except Exception:
                db.session.rollback()
            return True, "차량 항목을 추가했어요."

        if action == "update_item":
            item_id = action_item_id or _safe_int(form.get("_target_item_id")) or _safe_int(form.get("item_id"))
            if not item_id:
                return False, "수정할 항목을 찾지 못했어요."
            item = get_asset_item_by_id(user_pk, int(item_id))
            if not item:
                return False, "수정할 항목을 찾지 못했어요."
            input_json = dict(item.input_json or {})
            if item.kind == "home":
                home_ids = [_safe_int(v) for v in _get_list("home_item_id")]
                if not any(v for v in home_ids):
                    fallback_ids: list[int] = []
                    for maybe in [_safe_int(v) for v in _get_list("item_id")]:
                        rid = int(maybe or 0)
                        if rid <= 0:
                            continue
                        row_item = get_asset_item_by_id(user_pk, rid)
                        if row_item and row_item.kind == "home":
                            fallback_ids.append(rid)
                    home_ids = fallback_ids

                target_idx = -1
                for i, hid in enumerate(home_ids):
                    if int(hid or 0) == int(item_id):
                        target_idx = i
                        break

                addr_values = _get_list("item_home_address")
                type_values = _get_list("item_home_type")
                area_values = _get_list("item_home_area_sqm")
                mode_values = _get_list("item_home_rental_mode")
                deposit_values = _get_list("item_home_rent_deposit_krw")
                monthly_values = _get_list("item_home_rent_monthly_krw")

                def _pick_home(field_name: str, values: list[Any], default: Any = "") -> Any:
                    if target_idx >= 0 and target_idx < len(values):
                        return _get_at(values, target_idx, default)
                    return form.get(field_name, default)

                rental_mode = _normalize_home_rental_mode(_pick_home("item_home_rental_mode", mode_values, "unknown"))
                input_json.update(
                    {
                        "address_text": str(_pick_home("item_home_address", addr_values, "") or "").strip(),
                        "home_type": str(_pick_home("item_home_type", type_values, "") or "").strip().lower(),
                        "area_sqm": _safe_float(_pick_home("item_home_area_sqm", area_values, "")),
                        "rental_mode": rental_mode,
                        "rent_deposit_krw": (
                            _safe_int(_pick_home("item_home_rent_deposit_krw", deposit_values, ""))
                            if rental_mode in {"jeonse", "rent"}
                            else None
                        ),
                        "rent_monthly_krw": (
                            _safe_int(_pick_home("item_home_rent_monthly_krw", monthly_values, ""))
                            if rental_mode == "rent"
                            else None
                        ),
                    }
                )
                if not _has_meaningful_home_input(input_json):
                    return False, "모든 주택 입력값이 비어 있어요. 삭제를 눌러 항목을 지우거나 값을 입력해 주세요."
            elif item.kind == "car":
                car_ids = [_safe_int(v) for v in _get_list("car_item_id")]
                if not any(v for v in car_ids):
                    fallback_ids: list[int] = []
                    for maybe in [_safe_int(v) for v in _get_list("item_id")]:
                        rid = int(maybe or 0)
                        if rid <= 0:
                            continue
                        row_item = get_asset_item_by_id(user_pk, rid)
                        if row_item and row_item.kind == "car":
                            fallback_ids.append(rid)
                    car_ids = fallback_ids

                target_idx = -1
                for i, cid in enumerate(car_ids):
                    if int(cid or 0) == int(item_id):
                        target_idx = i
                        break

                brand_values = _get_list("item_car_brand")
                model_values = _get_list("item_car_model")
                year_values = _get_list("item_car_year")

                def _pick_car(field_name: str, values: list[Any], default: Any = "") -> Any:
                    if target_idx >= 0 and target_idx < len(values):
                        return _get_at(values, target_idx, default)
                    return form.get(field_name, default)

                input_json.update(
                    {
                        "brand": str(_pick_car("item_car_brand", brand_values, "") or "").strip(),
                        "model": str(_pick_car("item_car_model", model_values, "") or "").strip(),
                        "year": _safe_int(_pick_car("item_car_year", year_values, "")),
                    }
                )
                if not _has_meaningful_car_input(input_json):
                    return False, "모든 차량 입력값이 비어 있어요. 삭제를 눌러 항목을 지우거나 값을 입력해 주세요."
            else:
                return False, "이 항목은 여기서 수정할 수 없어요."
            item.input_json = input_json
            item.updated_at = utcnow()
            db.session.add(item)
            db.session.commit()
            if item.kind == "home":
                _sync_home_representative(user_pk)
            elif item.kind == "car":
                _sync_car_representative(user_pk)
            try:
                _sync_assets_to_nhis_profile(user_pk, month_key=month_key)
            except Exception:
                db.session.rollback()
            return True, "항목을 수정했어요."

        # profile fields
        if "household_has_others" in form:
            row.household_has_others = _safe_bool(form.get("household_has_others"))
        if "dependents_count" in form:
            row.dependents_count = _safe_int(form.get("dependents_count"))
        if "housing_mode" in form:
            mode = str(form.get("housing_mode") or "unknown").strip().lower()
            row.housing_mode = mode if mode in HOUSING_MODES else "unknown"
        if "has_car" in form:
            has_car = str(form.get("has_car") or "").strip().lower()
            if has_car in {"yes", "y", "1", "true", "on"}:
                row.has_car = True
            elif has_car in {"no", "n", "0", "false", "off"}:
                row.has_car = False
            else:
                row.has_car = None
        if "member_type" in form:
            _sync_member_type_direct(
                user_pk=user_pk,
                member_type=_normalize_member_type(form.get("member_type")),
                month_key=month_key,
            )
        elif ("company_nhis" in form) or ("dependent_nhis" in form):
            _sync_member_type_from_hints(
                user_pk=user_pk,
                company_nhis=_safe_tri_state(form.get("company_nhis")),
                dependent_nhis=_safe_tri_state(form.get("dependent_nhis")),
                month_key=month_key,
            )

        selected: list[str] = []
        for option in INCOME_TYPE_OPTIONS:
            if _safe_bool(form.get(f"other_income_{option}")) is True:
                selected.append(option)
        if selected or "other_income_annual_krw" in form:
            row.other_income_types_json = selected
            row.other_income_annual_krw = _safe_int(form.get("other_income_annual_krw"))

        ok_income_hybrid, income_hybrid_msg = _save_income_hybrid_from_form(
            user_pk=int(user_pk),
            form=form,
            month_key=month_key,
        )
        if not ok_income_hybrid:
            return False, income_hybrid_msg

        # 선택 입력: 연도별 고지 이력
        # 기본은 동기화 ON(기존 화면 호환)이고, 통합 화면에서는 사용자가 이력 섹션을 실제로 건드렸을 때만 ON으로 보낼 수 있다.
        history_sync_enabled = _safe_bool(form.get("history_sync_enabled"))
        if history_sync_enabled is None:
            history_sync_enabled = True
        if ("history_rows" in form) and history_sync_enabled:
            ok_hist, msg_hist = save_nhis_bill_history_from_form(user_pk=user_pk, form_data=form)
            if not ok_hist:
                return False, msg_hist

        # 카드별 인라인 입력을 "저장하고 다시 계산"에서도 한 번에 반영한다.
        home_item_ids = [_safe_int(v) for v in _get_list("home_item_id")]
        if not any(v for v in home_item_ids):
            # 이전 템플릿(숨은 home_item_id 없음) 폴백
            fallback_ids: list[int] = []
            for maybe_item_id in [_safe_int(v) for v in _get_list("item_id")]:
                item_id = int(maybe_item_id or 0)
                if item_id <= 0:
                    continue
                row_item = get_asset_item_by_id(user_pk, item_id)
                if row_item and row_item.kind == "home":
                    fallback_ids.append(item_id)
            home_item_ids = fallback_ids
        if any(v for v in home_item_ids):
            home_addr_list = _get_list("item_home_address")
            home_type_list = _get_list("item_home_type")
            home_area_list = _get_list("item_home_area_sqm")
            home_rental_mode_list = _get_list("item_home_rental_mode")
            home_deposit_list = _get_list("item_home_rent_deposit_krw")
            home_monthly_list = _get_list("item_home_rent_monthly_krw")
            for idx, maybe_item_id in enumerate(home_item_ids):
                item_id = int(maybe_item_id or 0)
                if item_id <= 0:
                    continue
                item = get_asset_item_by_id(user_pk, item_id)
                if not item or item.kind != "home":
                    continue
                rental_mode = _normalize_home_rental_mode(_get_at(home_rental_mode_list, idx, "unknown"))
                input_json = dict(item.input_json or {})
                input_json.update(
                    {
                        "address_text": str(_get_at(home_addr_list, idx, "") or "").strip(),
                        "home_type": str(_get_at(home_type_list, idx, "") or "").strip().lower(),
                        "area_sqm": _safe_float(_get_at(home_area_list, idx, "")),
                        "rental_mode": rental_mode,
                        "rent_deposit_krw": (
                            _safe_int(_get_at(home_deposit_list, idx, "")) if rental_mode in {"jeonse", "rent"} else None
                        ),
                        "rent_monthly_krw": (
                            _safe_int(_get_at(home_monthly_list, idx, "")) if rental_mode == "rent" else None
                        ),
                    }
                )
                item.input_json = input_json
                item.updated_at = utcnow()
                db.session.add(item)

        car_item_ids = [_safe_int(v) for v in _get_list("car_item_id")]
        if not any(v for v in car_item_ids):
            # 이전 템플릿(숨은 car_item_id 없음) 폴백
            fallback_ids: list[int] = []
            for maybe_item_id in [_safe_int(v) for v in _get_list("item_id")]:
                item_id = int(maybe_item_id or 0)
                if item_id <= 0:
                    continue
                row_item = get_asset_item_by_id(user_pk, item_id)
                if row_item and row_item.kind == "car":
                    fallback_ids.append(item_id)
            car_item_ids = fallback_ids
        if any(v for v in car_item_ids):
            car_brand_list = _get_list("item_car_brand")
            car_model_list = _get_list("item_car_model")
            car_year_list = _get_list("item_car_year")
            for idx, maybe_item_id in enumerate(car_item_ids):
                item_id = int(maybe_item_id or 0)
                if item_id <= 0:
                    continue
                item = get_asset_item_by_id(user_pk, item_id)
                if not item or item.kind != "car":
                    continue
                input_json = dict(item.input_json or {})
                input_json.update(
                    {
                        "brand": str(_get_at(car_brand_list, idx, "") or "").strip(),
                        "model": str(_get_at(car_model_list, idx, "") or "").strip(),
                        "year": _safe_int(_get_at(car_year_list, idx, "")),
                    }
                )
                item.input_json = input_json
                item.updated_at = utcnow()
                db.session.add(item)

        # items
        home_input = {
            "address_text": str(form.get("home_address") or "").strip(),
            "home_type": str(form.get("home_type") or "").strip().lower(),
            "area_sqm": _safe_float(form.get("home_area_sqm")),
            "property_tax_base_manual_krw": _safe_int(form.get("property_tax_base_manual_krw")),
            "rental_mode": _normalize_home_rental_mode(form.get("home_rental_mode")),
            "rent_deposit_krw": _safe_int(form.get("home_rent_deposit_krw")),
            "rent_monthly_krw": _safe_int(form.get("home_rent_monthly_krw")),
        }
        has_labeled_homes = _has_labeled_items(user_pk, kind="home", prefix="보유주택 ")
        home_has_input = _has_meaningful_home_input(home_input)
        if has_labeled_homes:
            if home_has_input:
                upsert_asset_item(user_pk=user_pk, kind="home", label="부동산", input_json=home_input)
                upsert_asset_item(user_pk=user_pk, kind="home", label="보유주택 1", input_json=home_input)
            else:
                # 다중 항목이 이미 있을 때 대표 입력이 비어있으면 1번 항목을 덮어쓰지 않는다.
                _sync_home_representative(user_pk)
        elif home_has_input:
            # 단일 대표 항목만 있는 기존 데이터는 의미 있는 값이 있을 때만 갱신한다.
            upsert_asset_item(user_pk=user_pk, kind="home", label="부동산", input_json=home_input)

        housing_mode = str(row.housing_mode or "unknown").strip().lower()
        if housing_mode in {"own", "none", "unknown"}:
            rent_input = {
                "rent_deposit_krw": None,
                "rent_monthly_krw": None,
            }
        elif housing_mode == "jeonse":
            rent_input = {
                "rent_deposit_krw": _safe_int(form.get("rent_deposit_krw")),
                "rent_monthly_krw": None,
            }
        elif housing_mode == "rent":
            rent_input = {
                "rent_deposit_krw": _safe_int(form.get("rent_deposit_krw")),
                "rent_monthly_krw": _safe_int(form.get("rent_monthly_krw")),
            }
        else:
            rent_input = {
                "rent_deposit_krw": None,
                "rent_monthly_krw": None,
            }
        upsert_asset_item(user_pk=user_pk, kind="rent", label="전월세", input_json=rent_input)

        if row.has_car is False:
            for old in list_asset_item_rows(user_pk, kind="car"):
                db.session.delete(old)
        else:
            car_input = {
                "brand": str(form.get("car_brand") or "").strip(),
                "model": str(form.get("car_model") or "").strip(),
                "year": _safe_int(form.get("car_year")),
            }
            has_labeled_cars = _has_labeled_items(user_pk, kind="car", prefix="차량 ")
            car_has_input = _has_meaningful_car_input(car_input)
            if has_labeled_cars:
                if car_has_input:
                    upsert_asset_item(user_pk=user_pk, kind="car", label="차량", input_json=car_input)
                    upsert_asset_item(user_pk=user_pk, kind="car", label="차량 1", input_json=car_input)
                else:
                    # 다중 항목이 이미 있을 때 대표 입력이 비어있으면 1번 항목을 덮어쓰지 않는다.
                    _sync_car_representative(user_pk)
            elif car_has_input:
                # 단일 대표 항목만 있는 기존 데이터는 의미 있는 값이 있을 때만 갱신한다.
                upsert_asset_item(user_pk=user_pk, kind="car", label="차량", input_json=car_input)

        row.updated_at = utcnow()
        db.session.add(row)
        db.session.commit()
        try:
            _sync_assets_to_nhis_profile(user_pk, month_key=month_key)
        except Exception:
            db.session.rollback()
        return True, "저장됐어요. 바로 추정 결과를 다시 계산할게요."
    except Exception:
        db.session.rollback()
        return False, "저장 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."


def build_assets_context(user_pk: int, *, month_key: str | None = None) -> dict[str, Any]:
    # 순환 import 방지
    from services.nhis_profile import list_nhis_bill_history

    normalized_month = parse_month_key(month_key)
    _dedupe_asset_items(user_pk)
    profile_row = get_or_create_asset_profile(user_pk)
    items = list_asset_items(user_pk)
    home_rows = list_asset_item_rows(user_pk, kind="home")
    car_rows = list_asset_item_rows(user_pk, kind="car")
    rent_rows = list_asset_item_rows(user_pk, kind="rent")
    ratio, missing, completed = mark_assets_completed_if_ready(user_pk)
    profile_row = get_or_create_asset_profile(user_pk)

    home_items_only = [r for r in home_rows if str(r.label or "").strip() != "부동산"]
    home_items_only.sort(key=lambda r: _label_index(str(r.label or ""), "보유주택"))
    car_items_only = [r for r in car_rows if str(r.label or "").strip() != "차량"]
    car_items_only.sort(key=lambda r: _label_index(str(r.label or ""), "차량"))

    home_list = [asset_item_to_dict(r) for r in home_items_only]
    car_list = [asset_item_to_dict(r) for r in car_items_only]
    # 과거 단일 저장 데이터(대표 행만 있는 경우) 폴백:
    # 대표 입력이 "의미 있는 값"일 때만 목록에 노출한다.
    # (삭제 후 대표 행이 빈 값으로 재생성되어도 목록에 다시 보이지 않게)
    home_rep = items.get("home")
    if not home_list and home_rep and _has_meaningful_home_input(dict(home_rep.input_json or {})):
        home_list = [asset_item_to_dict(home_rep)]
    car_rep = items.get("car")
    if not car_list and car_rep and _has_meaningful_car_input(dict(car_rep.input_json or {})):
        car_list = [asset_item_to_dict(car_rep)]

    return {
        "profile": asset_profile_to_dict(profile_row),
        "items": {
            "home": asset_item_to_dict(items.get("home")),
            "rent": asset_item_to_dict(items.get("rent")),
            "car": asset_item_to_dict(items.get("car")),
        },
        "income_hybrid": _build_income_hybrid_context(user_pk=int(user_pk), month_key=normalized_month),
        "home_list": home_list,
        "car_list": car_list,
        "rent_list": [asset_item_to_dict(r) for r in rent_rows],
        "bill_history": list_nhis_bill_history(user_pk),
        "completion_ratio": ratio,
        "missing_fields": missing,
        "is_completed": completed or bool(profile_row.completed_at),
    }

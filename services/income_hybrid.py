from __future__ import annotations

from typing import Any

from core.extensions import db
from core.time import utcnow
from domain.models import TaxProfile

INCOME_HYBRID_FIELDS: tuple[str, ...] = (
    "business_income_amount_krw",
    "fin_income_amount_krw",
    "salary_income_amount_krw",
    "pension_income_amount_krw",
    "other_income_amount_krw",
)
INCOME_HYBRID_SCOPE_OPTIONS: tuple[str, ...] = ("nhis", "tax", "both")
INCOME_HYBRID_INPUT_BASIS_OPTIONS: tuple[str, ...] = (
    "income_amount_pre_tax",
    "salary_gross_annual",
    "salary_income_amount_annual",
)


def _safe_int(raw: Any) -> int | None:
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if not s:
        return None
    try:
        value = int(float(s))
    except Exception:
        return None
    return max(0, int(value))


def _month_key_now() -> str:
    return utcnow().strftime("%Y-%m")


def parse_month_key(raw: Any) -> str:
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


def recommended_income_year(month_key: str | None) -> int:
    key = parse_month_key(month_key)
    year = int(key[:4])
    month = int(key[5:7])
    # NHIS 반영 연도 규칙(11월 전후 적용연도 차이)을 기본 추천연도로 사용
    return int(year - 1) if month >= 11 else int(year - 2)


def _normalize_scope(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in INCOME_HYBRID_SCOPE_OPTIONS:
        return s
    return "both"


def _normalize_basis(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in INCOME_HYBRID_INPUT_BASIS_OPTIONS:
        return s
    return "income_amount_pre_tax"


def _normalize_note(raw: Any, *, max_len: int = 200) -> str:
    text = " ".join(str(raw or "").split()).strip()
    if len(text) > max_len:
        return text[:max_len]
    return text


def _normalize_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    year_raw = _safe_int(raw.get("year"))
    if year_raw is None or year_raw < 2000 or year_raw > 2100:
        return None
    year = int(year_raw)
    scope = _normalize_scope(raw.get("scope"))
    input_basis = _normalize_basis(raw.get("input_basis"))
    is_pre_tax = bool(raw.get("is_pre_tax") is not False)
    note = _normalize_note(raw.get("note"))
    out: dict[str, Any] = {
        "year": year,
        "scope": scope,
        "input_basis": input_basis,
        "is_pre_tax": bool(is_pre_tax),
        "note": note,
        "updated_at": str(raw.get("updated_at") or ""),
    }
    for key in INCOME_HYBRID_FIELDS:
        out[key] = _safe_int(raw.get(key))
    return out


def normalize_income_hybrid(raw: Any, *, month_key: str | None = None) -> dict[str, Any]:
    recommended = recommended_income_year(month_key)
    base = {
        "enabled": False,
        "recommended_year": int(recommended),
        "entries": [],
        "updated_at": "",
    }
    if not isinstance(raw, dict):
        return base

    entries_raw = raw.get("entries")
    parsed_entries: list[dict[str, Any]] = []
    if isinstance(entries_raw, list):
        for node in entries_raw:
            entry = _normalize_entry(node)
            if entry is None:
                continue
            parsed_entries.append(entry)

    # year+scope unique
    dedup: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in parsed_entries:
        key = (int(entry["year"]), str(entry["scope"]))
        dedup[key] = entry

    entries = sorted(dedup.values(), key=lambda item: (int(item["year"]), str(item["scope"])), reverse=True)
    return {
        "enabled": bool(raw.get("enabled") is True),
        "recommended_year": int(_safe_int(raw.get("recommended_year")) or recommended),
        "entries": entries,
        "updated_at": str(raw.get("updated_at") or ""),
    }


def get_income_hybrid(user_pk: int, *, month_key: str | None = None) -> dict[str, Any]:
    row = TaxProfile.query.filter_by(user_pk=int(user_pk)).first()
    profile_json = row.profile_json if row and isinstance(row.profile_json, dict) else {}
    return normalize_income_hybrid(profile_json.get("income_hybrid"), month_key=month_key)


def set_income_hybrid_enabled(
    *,
    user_pk: int,
    enabled: bool,
    month_key: str | None = None,
) -> dict[str, Any]:
    row = TaxProfile.query.filter_by(user_pk=int(user_pk)).first()
    if row is None:
        row = TaxProfile(user_pk=int(user_pk), profile_json={})

    profile_json = row.profile_json if isinstance(row.profile_json, dict) else {}
    hybrid = normalize_income_hybrid(profile_json.get("income_hybrid"), month_key=month_key)
    hybrid["enabled"] = bool(enabled)
    hybrid["updated_at"] = utcnow().isoformat(timespec="seconds")

    profile_next = dict(profile_json)
    profile_next["income_hybrid"] = hybrid
    row.profile_json = profile_next
    db.session.add(row)
    db.session.commit()
    return normalize_income_hybrid(hybrid, month_key=month_key)


def save_income_hybrid_entry(
    *,
    user_pk: int,
    enabled: bool,
    year: int,
    scope: str,
    input_basis: str,
    is_pre_tax: bool,
    note: str,
    fields: dict[str, Any],
    month_key: str | None = None,
) -> dict[str, Any]:
    row = TaxProfile.query.filter_by(user_pk=int(user_pk)).first()
    if row is None:
        row = TaxProfile(user_pk=int(user_pk), profile_json={})

    profile_json = row.profile_json if isinstance(row.profile_json, dict) else {}
    hybrid = normalize_income_hybrid(profile_json.get("income_hybrid"), month_key=month_key)
    hybrid["enabled"] = bool(enabled)
    safe_year = int(max(2000, min(2100, int(year))))
    safe_scope = _normalize_scope(scope)
    safe_basis = _normalize_basis(input_basis)
    safe_note = _normalize_note(note)

    value_map: dict[str, int | None] = {}
    for key in INCOME_HYBRID_FIELDS:
        value_map[key] = _safe_int(fields.get(key))

    has_any_value = any((value_map.get(key) is not None) for key in INCOME_HYBRID_FIELDS)
    entries = list(hybrid.get("entries") or [])
    filtered = [
        e
        for e in entries
        if not (int(e.get("year") or 0) == safe_year and str(e.get("scope") or "") == safe_scope)
    ]
    if has_any_value:
        entry = {
            "year": safe_year,
            "scope": safe_scope,
            "input_basis": safe_basis,
            "is_pre_tax": bool(is_pre_tax),
            "note": safe_note,
            "updated_at": utcnow().isoformat(timespec="seconds"),
        }
        entry.update(value_map)
        filtered.append(entry)

    hybrid["entries"] = sorted(filtered, key=lambda item: (int(item.get("year") or 0), str(item.get("scope") or "")), reverse=True)
    hybrid["updated_at"] = utcnow().isoformat(timespec="seconds")

    profile_next = dict(profile_json)
    profile_next["income_hybrid"] = hybrid
    row.profile_json = profile_next
    db.session.add(row)
    db.session.commit()
    return normalize_income_hybrid(hybrid, month_key=month_key)


def pick_income_override_for_month(
    *,
    user_pk: int,
    month_key: str | None,
    purpose: str,  # nhis | tax
) -> dict[str, Any]:
    mode = str(purpose or "").strip().lower()
    if mode not in {"nhis", "tax"}:
        mode = "nhis"

    target_year = recommended_income_year(month_key)
    hybrid = get_income_hybrid(user_pk=user_pk, month_key=month_key)
    entries = list(hybrid.get("entries") or [])
    if not hybrid.get("enabled") or not entries:
        return {
            "applied": False,
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "target_year": int(target_year),
            "used_year": None,
            "used_scope": None,
            "entry": None,
        }

    allowed_scopes = {mode, "both"}
    candidates = [e for e in entries if str(e.get("scope") or "") in allowed_scopes]
    if not candidates:
        return {
            "applied": False,
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "target_year": int(target_year),
            "used_year": None,
            "used_scope": None,
            "entry": None,
        }

    exact = [e for e in candidates if int(e.get("year") or 0) == int(target_year)]
    selected = None
    if exact:
        exact.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        exact.sort(key=lambda item: (str(item.get("scope") or "") != mode))
        selected = exact[0]
    else:
        candidates.sort(key=lambda item: (int(item.get("year") or 0), str(item.get("updated_at") or "")), reverse=True)
        selected = candidates[0]

    if not selected:
        return {
            "applied": False,
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "target_year": int(target_year),
            "used_year": None,
            "used_scope": None,
            "entry": None,
        }

    has_values = any((_safe_int(selected.get(field)) is not None) for field in INCOME_HYBRID_FIELDS)
    if not has_values:
        return {
            "applied": False,
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "target_year": int(target_year),
            "used_year": None,
            "used_scope": None,
            "entry": None,
        }

    return {
        "applied": True,
        "source_code": "user_input",
        "source_label": "사용자 입력(확정)",
        "target_year": int(target_year),
        "used_year": int(selected.get("year") or target_year),
        "used_scope": str(selected.get("scope") or "both"),
        "entry": dict(selected),
    }


def aggregate_income_override(entry: dict[str, Any] | None) -> dict[str, int]:
    node = entry if isinstance(entry, dict) else {}
    business = int(_safe_int(node.get("business_income_amount_krw")) or 0)
    fin = int(_safe_int(node.get("fin_income_amount_krw")) or 0)
    salary = int(_safe_int(node.get("salary_income_amount_krw")) or 0)
    pension = int(_safe_int(node.get("pension_income_amount_krw")) or 0)
    other = int(_safe_int(node.get("other_income_amount_krw")) or 0)
    non_salary = int(max(0, business + fin + pension + other))
    annual_total = int(max(0, business + fin + salary + pension + other))
    return {
        "business_income_amount_krw": business,
        "fin_income_amount_krw": fin,
        "salary_income_amount_krw": salary,
        "pension_income_amount_krw": pension,
        "other_income_amount_krw": other,
        "non_salary_annual_income_krw": non_salary,
        "annual_total_income_krw": annual_total,
    }

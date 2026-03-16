from __future__ import annotations

import hashlib
import re
from typing import Any

from core.extensions import db
from domain.models import ActionLog, BankAccountLink, SafeToSpendSettings, Transaction, UserBankAccount


BANK_CODE_NAME = {
    "0003": "IBK기업",
    "0004": "KB국민",
    "0011": "NH농협",
    "0020": "우리",
    "0023": "SC제일",
    "0027": "씨티",
    "0031": "iM뱅크(대구)",
    "0032": "부산",
    "0034": "광주",
    "0035": "제주",
    "0037": "전북",
    "0039": "경남",
    "0081": "하나",
    "0088": "신한",
    "0090": "카카오뱅크",
    "0092": "토스뱅크",
}

_DIGIT_RE = re.compile(r"[^0-9]")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_DIGIT_SEQ_RE = re.compile(r"\d+")
_COLOR_PALETTE = (
    "#2563EB",
    "#0EA5E9",
    "#0F766E",
    "#16A34A",
    "#CA8A04",
    "#EA580C",
    "#DC2626",
    "#9333EA",
    "#7C3AED",
    "#0891B2",
)
_ACCOUNT_PREFS_META_KEY = "bank_account_ui_prefs"
_MERGE_UNDO_MAX_IDS = 5000


def _bank_name(code: str) -> str:
    c = (code or "").strip()
    return BANK_CODE_NAME.get(c, f"은행({c})" if c else "은행")


def normalize_account_number(raw: str | None) -> str:
    return _DIGIT_RE.sub("", str(raw or ""))


def fingerprint(digits: str | None) -> str | None:
    d = normalize_account_number(digits)
    if not d:
        return None
    return hashlib.sha256(d.encode("utf-8")).hexdigest()


def last4(digits: str | None) -> str | None:
    d = normalize_account_number(digits)
    if len(d) < 4:
        return None
    return d[-4:]


def mask_last4(raw_last4: str | None) -> str:
    tail = str(raw_last4 or "").strip()
    if len(tail) == 4 and tail.isdigit():
        return f"****{tail}"
    return "미지정"


def display_name(account: UserBankAccount | None) -> str:
    if not account:
        return "미지정"
    masked = mask_last4(account.account_last4)
    alias = (account.alias or "").strip()
    if alias:
        return f"{alias} {masked}".strip()
    return masked


def _normalize_color(color_hex: str | None) -> str | None:
    raw = str(color_hex or "").strip()
    if not raw:
        return None
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if _HEX_COLOR_RE.fullmatch(raw):
        return raw.upper()
    return None


def _normalize_id_list(raw: Any) -> list[int]:
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            account_id = int(item)
        except Exception:
            continue
        if account_id <= 0 or account_id in seen:
            continue
        seen.add(account_id)
        out.append(account_id)
    return out


def _get_or_create_settings_row(user_pk: int) -> SafeToSpendSettings:
    row = SafeToSpendSettings.query.get(int(user_pk))
    if row:
        return row
    row = SafeToSpendSettings(user_pk=int(user_pk), default_tax_rate=0.15, custom_rates={})
    db.session.add(row)
    db.session.flush()
    return row


def get_account_ui_prefs(user_pk: int) -> dict[str, list[int]]:
    row = _get_or_create_settings_row(int(user_pk))
    raw = row._get_meta(_ACCOUNT_PREFS_META_KEY, {}) if hasattr(row, "_get_meta") else {}
    payload = raw if isinstance(raw, dict) else {}
    hidden_ids = _normalize_id_list(payload.get("hidden_ids"))
    order_ids = _normalize_id_list(payload.get("order_ids"))
    return {"hidden_ids": hidden_ids, "order_ids": order_ids}


def _save_account_ui_prefs(user_pk: int, *, hidden_ids: list[int], order_ids: list[int]) -> dict[str, list[int]]:
    row = _get_or_create_settings_row(int(user_pk))
    normalized_hidden = _normalize_id_list(hidden_ids)
    normalized_order = _normalize_id_list(order_ids)
    payload = {"hidden_ids": normalized_hidden, "order_ids": normalized_order}
    if hasattr(row, "_set_meta"):
        row._set_meta(_ACCOUNT_PREFS_META_KEY, payload)
    else:
        base = dict(row.custom_rates or {})
        meta = dict(base.get("_meta") or {})
        meta[_ACCOUNT_PREFS_META_KEY] = payload
        base["_meta"] = meta
        row.custom_rates = base
    db.session.add(row)
    db.session.flush()
    return payload


def set_account_hidden(user_pk: int, account_id: int, *, hidden: bool) -> dict[str, list[int]]:
    prefs = get_account_ui_prefs(int(user_pk))
    hidden_ids = list(prefs.get("hidden_ids") or [])
    account_int = int(account_id)
    if hidden:
        if account_int not in hidden_ids:
            hidden_ids.append(account_int)
    else:
        hidden_ids = [x for x in hidden_ids if int(x) != account_int]
    return _save_account_ui_prefs(int(user_pk), hidden_ids=hidden_ids, order_ids=list(prefs.get("order_ids") or []))


def move_account_order(user_pk: int, account_id: int, *, direction: str) -> dict[str, list[int]]:
    prefs = get_account_ui_prefs(int(user_pk))
    order_ids = list(prefs.get("order_ids") or [])
    account_int = int(account_id)
    if account_int not in order_ids:
        order_ids.append(account_int)
    idx = order_ids.index(account_int)
    if direction == "up" and idx > 0:
        order_ids[idx - 1], order_ids[idx] = order_ids[idx], order_ids[idx - 1]
    elif direction == "down" and idx < len(order_ids) - 1:
        order_ids[idx + 1], order_ids[idx] = order_ids[idx], order_ids[idx + 1]
    return _save_account_ui_prefs(int(user_pk), hidden_ids=list(prefs.get("hidden_ids") or []), order_ids=order_ids)


def ensure_color(account: UserBankAccount | None) -> str:
    if not account:
        return "#94A3B8"
    if _normalize_color(account.color_hex):
        return _normalize_color(account.color_hex) or "#94A3B8"
    seed = str(account.account_fingerprint or account.id or account.alias or "")
    idx = 0
    if seed:
        idx = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(_COLOR_PALETTE)
    account.color_hex = _COLOR_PALETTE[idx]
    db.session.add(account)
    db.session.flush()
    return account.color_hex


def get_or_create_by_fingerprint(
    user_pk: int,
    bank_code_opt: str | None,
    account_fingerprint: str | None,
    account_last4: str | None,
    alias_opt: str | None = None,
) -> UserBankAccount:
    fp = str(account_fingerprint or "").strip() or None
    alias = (str(alias_opt or "").strip() or None)
    bank_code = (str(bank_code_opt or "").strip() or None)
    last4_value = (str(account_last4 or "").strip() or None)

    row = None
    if fp:
        row = (
            UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
            .filter(UserBankAccount.account_fingerprint == fp)
            .first()
        )
    if not row:
        row = UserBankAccount(
            user_pk=int(user_pk),
            bank_code=bank_code,
            account_fingerprint=fp,
            account_last4=last4_value,
            alias=alias,
            color_hex=None,
        )
        db.session.add(row)
        db.session.flush()
    else:
        changed = False
        if bank_code and not row.bank_code:
            row.bank_code = bank_code
            changed = True
        if last4_value and not row.account_last4:
            row.account_last4 = last4_value
            changed = True
        if alias and not row.alias:
            row.alias = alias
            changed = True
        if changed:
            db.session.add(row)
            db.session.flush()

    ensure_color(row)
    return row


def create_alias_account(*, user_pk: int, alias: str, color_hex: str | None = None) -> UserBankAccount:
    safe_alias = (str(alias or "").strip() or "기타 계좌")[:64]
    existing = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.account_fingerprint.is_(None))
        .filter(UserBankAccount.alias == safe_alias)
        .first()
    )
    if existing:
        if _normalize_color(color_hex):
            existing.color_hex = _normalize_color(color_hex)
            db.session.add(existing)
            db.session.flush()
        ensure_color(existing)
        return existing
    row = UserBankAccount(
        user_pk=int(user_pk),
        bank_code=None,
        account_fingerprint=None,
        account_last4=None,
        alias=safe_alias,
        color_hex=_normalize_color(color_hex),
    )
    db.session.add(row)
    db.session.flush()
    ensure_color(row)
    return row


def ensure_manual_bucket(user_pk: int) -> UserBankAccount:
    row = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.account_fingerprint.is_(None))
        .filter(UserBankAccount.alias == "기타(수동)")
        .first()
    )
    if row:
        ensure_color(row)
        return row
    row = UserBankAccount(
        user_pk=int(user_pk),
        bank_code=None,
        account_fingerprint=None,
        account_last4=None,
        alias="기타(수동)",
        color_hex="#64748B",
    )
    db.session.add(row)
    db.session.flush()
    ensure_color(row)
    return row


def list_accounts_for_ui(
    user_pk: int,
    *,
    include_hidden: bool = False,
    keep_ids: list[int] | tuple[int, ...] | None = None,
) -> list[dict]:
    try:
        rows = UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk)).all()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return []
    if not rows:
        return []
    try:
        linked_ids = {
            int(v)
            for (v,) in (
                db.session.query(BankAccountLink.bank_account_id)
                .filter(BankAccountLink.user_pk == int(user_pk))
                .filter(BankAccountLink.is_active.is_(True))
                .filter(BankAccountLink.bank_account_id.isnot(None))
                .all()
            )
            if v
        }
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        linked_ids = set()

    prefs = get_account_ui_prefs(int(user_pk))
    hidden_ids = {int(x) for x in (prefs.get("hidden_ids") or [])}
    order_ids = [int(x) for x in (prefs.get("order_ids") or [])]
    order_rank = {account_id: idx for idx, account_id in enumerate(order_ids)}
    keep_set = {int(x) for x in (keep_ids or []) if int(x) > 0}

    out = []
    for row in rows:
        color = ensure_color(row)
        row_id = int(row.id)
        is_hidden = row_id in hidden_ids
        if is_hidden and (not include_hidden) and (row_id not in keep_set):
            continue
        out.append(
            {
                "id": row_id,
                "display_name": display_name(row),
                "alias": (row.alias or ""),
                "color_hex": color,
                "bank_code": (row.bank_code or ""),
                "last4": (row.account_last4 or ""),
                "is_linked": row_id in linked_ids,
                "is_hidden": bool(is_hidden),
                "sort_rank": int(order_rank.get(row_id, 10_000 + row_id)),
            }
        )

    out.sort(
        key=lambda x: (
            int(x.get("sort_rank") or 0),
            0 if x["is_linked"] else 1,
            str(x["display_name"]),
            int(x["id"]),
        )
    )
    return out


def merge_user_bank_accounts(
    *,
    user_pk: int,
    from_account_id: int,
    to_account_id: int,
    actor: str = "user",
) -> tuple[bool, str, dict[str, Any] | None]:
    if int(from_account_id) == int(to_account_id):
        return False, "같은 계좌끼리는 병합할 수 없어요.", None

    from_row = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.id == int(from_account_id))
        .first()
    )
    to_row = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.id == int(to_account_id))
        .first()
    )
    if (not from_row) or (not to_row):
        return False, "선택한 계좌를 찾을 수 없어요.", None

    tx_ids = [
        int(tid)
        for (tid,) in (
            db.session.query(Transaction.id)
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.bank_account_id == int(from_account_id))
            .all()
        )
        if tid
    ]
    link_ids = [
        int(link_id)
        for (link_id,) in (
            db.session.query(BankAccountLink.id)
            .filter(BankAccountLink.user_pk == int(user_pk))
            .filter(BankAccountLink.bank_account_id == int(from_account_id))
            .all()
        )
        if link_id
    ]

    if len(tx_ids) > _MERGE_UNDO_MAX_IDS:
        return False, "거래 수가 많아 자동 되돌리기를 보장하기 어렵습니다. 계좌를 나눠서 정리해 주세요.", None

    prefs_before = get_account_ui_prefs(int(user_pk))

    db.session.query(Transaction).filter(Transaction.user_pk == int(user_pk)).filter(
        Transaction.bank_account_id == int(from_account_id)
    ).update({"bank_account_id": int(to_account_id)}, synchronize_session=False)
    db.session.query(BankAccountLink).filter(BankAccountLink.user_pk == int(user_pk)).filter(
        BankAccountLink.bank_account_id == int(from_account_id)
    ).update({"bank_account_id": int(to_account_id)}, synchronize_session=False)

    hidden_after = list(prefs_before.get("hidden_ids") or [])
    if int(from_account_id) not in hidden_after:
        hidden_after.append(int(from_account_id))
    order_after = [x for x in list(prefs_before.get("order_ids") or []) if int(x) != int(from_account_id)]
    _save_account_ui_prefs(
        int(user_pk),
        hidden_ids=hidden_after,
        order_ids=order_after,
    )

    payload = {
        "kind": "account_merge",
        "from_account_id": int(from_account_id),
        "to_account_id": int(to_account_id),
        "moved_tx_ids": tx_ids,
        "moved_link_ids": link_ids,
        "prefs_before": prefs_before,
        "prefs_after": {"hidden_ids": hidden_after, "order_ids": order_after},
        "actor": str(actor or "user"),
    }
    try:
        action_log = ActionLog(
            user_pk=int(user_pk),
            action_type="bulk_update",
            target_ids=[int(from_account_id), int(to_account_id)],
            before_state={"payload": payload},
            after_state={"message": "account_merge"},
            is_reverted=False,
        )
        db.session.add(action_log)
    except Exception:
        # ActionLog 저장 실패가 병합 자체를 막지는 않되, 되돌리기 사용성은 낮아질 수 있다.
        pass
    db.session.commit()
    return True, "계좌를 병합했어요. 필요하면 바로 되돌릴 수 있어요.", payload


def undo_last_account_merge(
    *,
    user_pk: int,
    undo_log_id: int | None = None,
) -> tuple[bool, str]:
    query = (
        ActionLog.query.filter(ActionLog.user_pk == int(user_pk))
        .filter(ActionLog.action_type == "bulk_update")
        .filter(ActionLog.is_reverted.is_(False))
    )
    if undo_log_id and int(undo_log_id) > 0:
        query = query.filter(ActionLog.id == int(undo_log_id))
    row = query.order_by(ActionLog.created_at.desc(), ActionLog.id.desc()).first()
    if not row:
        return False, "되돌릴 병합 기록이 없어요."

    payload = {}
    if isinstance(row.before_state, dict):
        raw_payload = row.before_state.get("payload")
        if isinstance(raw_payload, dict):
            payload = dict(raw_payload)
    if str(payload.get("kind") or "") != "account_merge":
        return False, "되돌릴 수 있는 계좌 병합 기록이 아니에요."

    from_account_id = int(payload.get("from_account_id") or 0)
    to_account_id = int(payload.get("to_account_id") or 0)
    tx_ids = _normalize_id_list(payload.get("moved_tx_ids"))
    link_ids = _normalize_id_list(payload.get("moved_link_ids"))
    prefs_before = payload.get("prefs_before") if isinstance(payload.get("prefs_before"), dict) else {}
    hidden_before = _normalize_id_list((prefs_before or {}).get("hidden_ids"))
    order_before = _normalize_id_list((prefs_before or {}).get("order_ids"))

    if from_account_id <= 0 or to_account_id <= 0:
        return False, "병합 기록이 올바르지 않아 되돌릴 수 없어요."

    if tx_ids:
        db.session.query(Transaction).filter(Transaction.user_pk == int(user_pk)).filter(
            Transaction.id.in_(tx_ids)
        ).filter(Transaction.bank_account_id == int(to_account_id)).update(
            {"bank_account_id": int(from_account_id)},
            synchronize_session=False,
        )
    if link_ids:
        db.session.query(BankAccountLink).filter(BankAccountLink.user_pk == int(user_pk)).filter(
            BankAccountLink.id.in_(link_ids)
        ).filter(BankAccountLink.bank_account_id == int(to_account_id)).update(
            {"bank_account_id": int(from_account_id)},
            synchronize_session=False,
        )

    _save_account_ui_prefs(
        int(user_pk),
        hidden_ids=hidden_before,
        order_ids=order_before,
    )
    row.is_reverted = True
    db.session.add(row)
    db.session.commit()
    return True, "마지막 계좌 병합을 되돌렸어요."


def get_linked_account_balances(user_pk: int, *, limit: int = 8) -> tuple[list[dict], bool]:
    """
    연동 ON 계좌 목록 + (가능하면) 현재 잔액.
    - 계좌번호 전체는 반환하지 않고 last4 마스킹만 노출한다.
    """
    try:
        links = (
            BankAccountLink.query.filter(BankAccountLink.user_pk == int(user_pk))
            .filter(BankAccountLink.is_active.is_(True))
            .order_by(BankAccountLink.bank_code.asc(), BankAccountLink.account_number.asc())
            .limit(max(1, int(limit)))
            .all()
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return [], False

    if not links:
        return [], False

    changed = False
    rows: list[dict] = []
    for link in links:
        digits = normalize_account_number(link.account_number)
        fp = fingerprint(digits)
        l4 = last4(digits)
        account_obj = None
        if link.bank_account_id:
            account_obj = UserBankAccount.query.filter_by(user_pk=int(user_pk), id=int(link.bank_account_id)).first()
        if not account_obj:
            account_obj = get_or_create_by_fingerprint(
                user_pk=int(user_pk),
                bank_code_opt=link.bank_code,
                account_fingerprint=fp,
                account_last4=l4,
                alias_opt=link.alias,
            )
            if int(link.bank_account_id or 0) != int(account_obj.id):
                link.bank_account_id = int(account_obj.id)
                db.session.add(link)
                changed = True

        masked = mask_last4(account_obj.account_last4 or l4)
        alias = (account_obj.alias or "").strip()
        rows.append(
            {
                "bank_code": (link.bank_code or "").strip(),
                "bank_name": _bank_name(link.bank_code or ""),
                "account_number": f"{alias} {masked}".strip() if alias else masked,
                "account_last4": (account_obj.account_last4 or l4 or ""),
                "balance_krw": int(link.last_balance_krw) if getattr(link, "last_balance_krw", None) is not None else None,
                "balance_checked_at": getattr(link, "last_balance_checked_at", None),
            }
        )

    if changed:
        db.session.commit()

    has_unavailable = any(row.get("balance_krw") is None for row in rows)
    return rows, has_unavailable

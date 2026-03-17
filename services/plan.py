from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.extensions import db
from core.time import utcnow
from domain.models import BankAccountLink, User


PLAN_FREE = "free"
PLAN_BASIC = "basic"
PLAN_PRO = "pro"
PLAN_VALUES = {PLAN_FREE, PLAN_BASIC, PLAN_PRO}

PLAN_STATUS_ACTIVE = "active"
PLAN_STATUS_INACTIVE = "inactive"
PLAN_STATUS_CANCELED = "canceled"
PLAN_STATUS_PAST_DUE = "past_due"
PLAN_STATUS_VALUES = {
    PLAN_STATUS_ACTIVE,
    PLAN_STATUS_INACTIVE,
    PLAN_STATUS_CANCELED,
    PLAN_STATUS_PAST_DUE,
}

FEATURE_BANK_LINK = "bank_link"
FEATURE_PACKAGE_DOWNLOAD = "package_download"
FEATURE_REVIEW_ACCESS = "review_access"
FEATURE_RECEIPT_ATTACH = "receipt_attach"
FEATURE_EVIDENCE_MANAGE = "evidence_manage"
FEATURE_CSV_IMPORT = "csv_import"
FEATURE_TAX_VIEW = "tax_view"


@dataclass(frozen=True)
class PlanEntitlements:
    plan_code: str
    plan_status: str
    included_account_limit: int
    extra_account_slots: int
    max_linked_accounts: int
    sync_interval_minutes: int | None
    can_bank_link: bool
    can_package_download: bool
    can_access_review: bool
    can_attach_receipt: bool
    can_manage_evidence: bool
    can_import_csv: bool
    can_view_tax: bool


class PlanPermissionError(RuntimeError):
    def __init__(self, message: str, *, feature: str):
        super().__init__(message)
        self.feature = str(feature)


def normalize_plan(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in PLAN_VALUES:
        return v
    return PLAN_FREE


def normalize_plan_status(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in PLAN_STATUS_VALUES:
        return v
    return PLAN_STATUS_ACTIVE


def _legacy_plan_to_code(value: str | None) -> str:
    legacy = (value or "").strip().lower()
    if legacy == PLAN_PRO:
        return PLAN_PRO
    if legacy == PLAN_BASIC:
        return PLAN_BASIC
    return PLAN_FREE


def _plan_to_legacy(plan_code: str) -> str:
    if plan_code == PLAN_FREE:
        return PLAN_FREE
    return PLAN_PRO


def _coerce_user(user_or_user_pk: Any) -> User | None:
    if isinstance(user_or_user_pk, User):
        return user_or_user_pk
    try:
        user_pk = int(user_or_user_pk)
    except Exception:
        return None
    if user_pk <= 0:
        return None
    return User.query.filter_by(id=user_pk).first()


def get_plan_code(user_or_user_pk: Any) -> str:
    user = _coerce_user(user_or_user_pk)
    if not user:
        return PLAN_FREE
    raw_code = getattr(user, "plan_code", None)
    if raw_code:
        return normalize_plan(str(raw_code))
    return _legacy_plan_to_code(getattr(user, "plan", None))


def get_plan_status(user_or_user_pk: Any) -> str:
    user = _coerce_user(user_or_user_pk)
    if not user:
        return PLAN_STATUS_ACTIVE
    raw_status = getattr(user, "plan_status", None)
    if raw_status:
        return normalize_plan_status(str(raw_status))
    return PLAN_STATUS_ACTIVE


def _included_account_limit(plan_code: str) -> int:
    code = normalize_plan(plan_code)
    if code == PLAN_BASIC:
        return 1
    if code == PLAN_PRO:
        return 2
    return 0


def _sync_interval_minutes(plan_code: str, plan_status: str) -> int | None:
    if normalize_plan_status(plan_status) != PLAN_STATUS_ACTIVE:
        return None
    code = normalize_plan(plan_code)
    if code == PLAN_BASIC:
        return 240
    if code == PLAN_PRO:
        return 60
    return None


def _build_entitlements(*, plan_code: str, plan_status: str, extra_account_slots: int) -> PlanEntitlements:
    normalized_plan = normalize_plan(plan_code)
    normalized_status = normalize_plan_status(plan_status)
    extra = max(0, int(extra_account_slots or 0))
    included = _included_account_limit(normalized_plan)
    max_accounts = max(0, included + extra)
    active_paid = normalized_status == PLAN_STATUS_ACTIVE and normalized_plan in {PLAN_BASIC, PLAN_PRO}
    return PlanEntitlements(
        plan_code=normalized_plan,
        plan_status=normalized_status,
        included_account_limit=included,
        extra_account_slots=extra,
        max_linked_accounts=max_accounts,
        sync_interval_minutes=_sync_interval_minutes(normalized_plan, normalized_status),
        can_bank_link=bool(active_paid and max_accounts > 0),
        can_package_download=bool(active_paid),
        can_access_review=True,
        can_attach_receipt=True,
        can_manage_evidence=True,
        can_import_csv=True,
        can_view_tax=True,
    )


def get_user_entitlements(user_or_user_pk: Any) -> PlanEntitlements:
    user = _coerce_user(user_or_user_pk)
    if not user:
        return _build_entitlements(
            plan_code=PLAN_FREE,
            plan_status=PLAN_STATUS_ACTIVE,
            extra_account_slots=0,
        )
    extra_slots = 0
    try:
        extra_slots = int(getattr(user, "extra_account_slots", 0) or 0)
    except Exception:
        extra_slots = 0
    return _build_entitlements(
        plan_code=get_plan_code(user),
        plan_status=get_plan_status(user),
        extra_account_slots=extra_slots,
    )


def get_user_plan(user_pk: int) -> str:
    return get_plan_code(user_pk)


def is_pro_user(user_pk: int) -> bool:
    # deprecated: 신규 권한 판단은 get_user_entitlements()/has_feature()를 사용한다.
    ent = get_user_entitlements(user_pk)
    return ent.plan_code == PLAN_PRO and ent.plan_status == PLAN_STATUS_ACTIVE


def max_linked_accounts(user_or_user_pk: Any) -> int:
    return int(get_user_entitlements(user_or_user_pk).max_linked_accounts)


def sync_interval_minutes(user_or_user_pk: Any) -> int | None:
    return get_user_entitlements(user_or_user_pk).sync_interval_minutes


def can_download_package(user_or_user_pk: Any) -> bool:
    return bool(get_user_entitlements(user_or_user_pk).can_package_download)


def has_feature(user_or_user_pk: Any, feature: str) -> bool:
    ent = get_user_entitlements(user_or_user_pk)
    key = str(feature or "").strip().lower()
    if key == FEATURE_BANK_LINK:
        return bool(ent.can_bank_link)
    if key == FEATURE_PACKAGE_DOWNLOAD:
        return bool(ent.can_package_download)
    if key == FEATURE_REVIEW_ACCESS:
        return bool(ent.can_access_review)
    if key == FEATURE_RECEIPT_ATTACH:
        return bool(ent.can_attach_receipt)
    if key == FEATURE_EVIDENCE_MANAGE:
        return bool(ent.can_manage_evidence)
    if key == FEATURE_CSV_IMPORT:
        return bool(ent.can_import_csv)
    if key == FEATURE_TAX_VIEW:
        return bool(ent.can_view_tax)
    return False


def require_plan_feature(user_or_user_pk: Any, feature: str, *, message: str) -> None:
    if has_feature(user_or_user_pk, feature):
        return
    raise PlanPermissionError(message, feature=feature)


def count_active_linked_accounts(user_or_user_pk: Any) -> int:
    user = _coerce_user(user_or_user_pk)
    if not user:
        return 0
    try:
        return int(
            BankAccountLink.query.filter(BankAccountLink.user_pk == int(user.id))
            .filter(BankAccountLink.is_active.is_(True))
            .count()
            or 0
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0


def can_activate_more_bank_links(
    user_or_user_pk: Any,
    *,
    additional: int = 1,
) -> tuple[bool, int]:
    ent = get_user_entitlements(user_or_user_pk)
    if not ent.can_bank_link:
        return False, int(ent.max_linked_accounts)
    active_count = count_active_linked_accounts(user_or_user_pk)
    allowed = int(ent.max_linked_accounts)
    next_count = int(active_count) + max(0, int(additional or 0))
    return next_count <= allowed, allowed


def ensure_can_link_bank_account(user_or_user_pk: Any) -> None:
    require_plan_feature(
        user_or_user_pk,
        FEATURE_BANK_LINK,
        message="계좌 자동 연동은 베이직 이상 플랜에서 사용할 수 있어요.",
    )


def ensure_can_download_package(user_or_user_pk: Any) -> None:
    require_plan_feature(
        user_or_user_pk,
        FEATURE_PACKAGE_DOWNLOAD,
        message="패키지 ZIP 다운로드는 베이직 이상 플랜에서 사용할 수 있어요.",
    )


def ensure_can_access_review(user_or_user_pk: Any) -> None:
    require_plan_feature(
        user_or_user_pk,
        FEATURE_REVIEW_ACCESS,
        message="정리하기 기능을 사용할 수 없어요.",
    )


def ensure_can_attach_receipt(user_or_user_pk: Any) -> None:
    require_plan_feature(
        user_or_user_pk,
        FEATURE_RECEIPT_ATTACH,
        message="영수증 첨부 기능을 사용할 수 없어요.",
    )


def set_user_plan(
    *,
    user_pk: int,
    plan: str,
    status: str | None = PLAN_STATUS_ACTIVE,
    extra_account_slots: int | None = None,
) -> tuple[bool, str]:
    user = User.query.filter_by(id=int(user_pk)).first()
    if not user:
        return False, "계정을 찾을 수 없습니다."

    target_plan = normalize_plan(plan)
    target_status = normalize_plan_status(status)
    if extra_account_slots is None:
        try:
            target_slots = int(getattr(user, "extra_account_slots", 0) or 0)
        except Exception:
            target_slots = 0
    else:
        target_slots = max(0, int(extra_account_slots or 0))

    # backward compatibility: legacy `users.plan` still maintained until full removal.
    user.plan = _plan_to_legacy(target_plan)
    if hasattr(user, "plan_code"):
        user.plan_code = target_plan
    if hasattr(user, "plan_status"):
        user.plan_status = target_status
    if hasattr(user, "extra_account_slots"):
        user.extra_account_slots = target_slots
    if hasattr(user, "plan_updated_at"):
        user.plan_updated_at = utcnow()
    db.session.add(user)
    db.session.commit()
    return True, target_plan


def plan_label_ko(plan_code: str | None) -> str:
    code = normalize_plan(plan_code)
    if code == PLAN_BASIC:
        return "베이직"
    if code == PLAN_PRO:
        return "프로"
    return "무료"

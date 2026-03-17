from __future__ import annotations

import unittest
from unittest.mock import patch

from services.plan import (
    FEATURE_BANK_LINK,
    FEATURE_CSV_IMPORT,
    FEATURE_EVIDENCE_MANAGE,
    FEATURE_PACKAGE_DOWNLOAD,
    FEATURE_RECEIPT_ATTACH,
    FEATURE_REVIEW_ACCESS,
    FEATURE_TAX_VIEW,
    PLAN_BASIC,
    PLAN_FREE,
    PLAN_PRO,
    PLAN_STATUS_ACTIVE,
    PlanPermissionError,
    _build_entitlements,
    can_activate_more_bank_links,
    has_feature,
    plan_label_ko,
    require_plan_feature,
)


class PlanEntitlementsTest(unittest.TestCase):
    def test_free_policy_keeps_core_features_and_blocks_paid_features(self) -> None:
        ent = _build_entitlements(plan_code=PLAN_FREE, plan_status=PLAN_STATUS_ACTIVE, extra_account_slots=0)
        self.assertFalse(ent.can_bank_link)
        self.assertFalse(ent.can_package_download)
        self.assertTrue(ent.can_access_review)
        self.assertTrue(ent.can_attach_receipt)
        self.assertTrue(ent.can_manage_evidence)
        self.assertTrue(ent.can_import_csv)
        self.assertTrue(ent.can_view_tax)
        self.assertEqual(ent.max_linked_accounts, 0)
        self.assertIsNone(ent.sync_interval_minutes)

    def test_basic_policy_has_one_account_and_4h_sync(self) -> None:
        ent = _build_entitlements(plan_code=PLAN_BASIC, plan_status=PLAN_STATUS_ACTIVE, extra_account_slots=0)
        self.assertTrue(ent.can_bank_link)
        self.assertTrue(ent.can_package_download)
        self.assertEqual(ent.max_linked_accounts, 1)
        self.assertEqual(ent.sync_interval_minutes, 240)

    def test_pro_policy_with_extra_slots_expands_limit(self) -> None:
        ent = _build_entitlements(plan_code=PLAN_PRO, plan_status=PLAN_STATUS_ACTIVE, extra_account_slots=2)
        self.assertTrue(ent.can_bank_link)
        self.assertEqual(ent.included_account_limit, 2)
        self.assertEqual(ent.max_linked_accounts, 4)
        self.assertEqual(ent.sync_interval_minutes, 60)

    def test_require_plan_feature_raises_for_free_on_paid_feature(self) -> None:
        with self.assertRaises(PlanPermissionError):
            require_plan_feature(None, FEATURE_BANK_LINK, message="계좌 연동 불가")

    def test_has_feature_keeps_review_open_for_free(self) -> None:
        self.assertTrue(has_feature(None, FEATURE_REVIEW_ACCESS))
        self.assertTrue(has_feature(None, FEATURE_RECEIPT_ATTACH))
        self.assertTrue(has_feature(None, FEATURE_EVIDENCE_MANAGE))
        self.assertTrue(has_feature(None, FEATURE_CSV_IMPORT))
        self.assertTrue(has_feature(None, FEATURE_TAX_VIEW))
        self.assertFalse(has_feature(None, FEATURE_PACKAGE_DOWNLOAD))

    def test_plan_label_ko(self) -> None:
        self.assertEqual(plan_label_ko("free"), "무료")
        self.assertEqual(plan_label_ko("basic"), "베이직")
        self.assertEqual(plan_label_ko("pro"), "프로")

    def test_over_limit_user_cannot_activate_more_links(self) -> None:
        ent = _build_entitlements(plan_code=PLAN_BASIC, plan_status=PLAN_STATUS_ACTIVE, extra_account_slots=0)
        with patch("services.plan.get_user_entitlements", return_value=ent):
            with patch("services.plan.count_active_linked_accounts", return_value=2):
                can_add, max_allowed = can_activate_more_bank_links(1, additional=1)
        self.assertFalse(can_add)
        self.assertEqual(max_allowed, 1)


if __name__ == "__main__":
    unittest.main()

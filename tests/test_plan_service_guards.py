from __future__ import annotations

import unittest
from unittest.mock import patch

from services.import_popbill import PopbillImportError, sync_popbill_for_user
from services.plan import PlanPermissionError
from services.tax_package import build_tax_package_zip


class PlanServiceGuardsTest(unittest.TestCase):
    def test_sync_popbill_service_has_second_guard(self) -> None:
        with patch(
            "services.import_popbill.ensure_can_link_bank_account",
            side_effect=PlanPermissionError("계좌 연동 불가", feature="bank_link"),
        ):
            with self.assertRaises(PopbillImportError) as ctx:
                sync_popbill_for_user(user_pk=1)
        self.assertIn("계좌 연동 불가", str(ctx.exception))

    def test_package_zip_service_has_second_guard(self) -> None:
        with patch(
            "services.tax_package.ensure_can_download_package",
            side_effect=PlanPermissionError("패키지 다운로드 불가", feature="package_download"),
        ):
            with self.assertRaises(PlanPermissionError) as ctx:
                build_tax_package_zip(user_pk=1, month_key="2026-03")
        self.assertIn("패키지 다운로드 불가", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

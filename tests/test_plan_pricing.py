from __future__ import annotations

import unittest

from services.billing.constants import BASIC_PRICE_KRW, PLAN_BASIC, PLAN_FREE, PLAN_PRO, PRO_PRICE_KRW
from services.billing.pricing import build_pricing_comparison_rows, build_pricing_plan_cards, format_monthly_krw
from services.plan import build_runtime_plan_state, plan_label_ko


class PlanPricingLogicTest(unittest.TestCase):
    def test_plan_labels_match_expected_korean_names(self) -> None:
        self.assertEqual(plan_label_ko(PLAN_FREE), "무료")
        self.assertEqual(plan_label_ko(PLAN_BASIC), "베이직")
        self.assertEqual(plan_label_ko(PLAN_PRO), "프로")

    def test_pricing_plan_cards_expose_recovered_price_points(self) -> None:
        cards = {card.code: card for card in build_pricing_plan_cards()}

        self.assertEqual(cards[PLAN_FREE].price_label, "0원")
        self.assertEqual(cards[PLAN_BASIC].price_label, format_monthly_krw(BASIC_PRICE_KRW))
        self.assertEqual(cards[PLAN_PRO].price_label, format_monthly_krw(PRO_PRICE_KRW))
        self.assertTrue(cards[PLAN_BASIC].recommended)

    def test_runtime_plan_state_stays_display_only(self) -> None:
        runtime = build_runtime_plan_state()

        self.assertEqual(runtime.current_plan_code, PLAN_FREE)
        self.assertFalse(runtime.subscription_ready)
        self.assertEqual(runtime.runtime_mode, "display_only")
        self.assertIn("결제 승인", runtime.note)

    def test_pricing_comparison_rows_include_sync_difference(self) -> None:
        rows = build_pricing_comparison_rows()
        labels = {row["label"] for row in rows}

        self.assertIn("자동 동기화 주기", labels)
        self.assertIn("자동 연동 계좌 수", labels)


if __name__ == "__main__":
    unittest.main()

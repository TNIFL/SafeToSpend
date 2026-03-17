from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from routes.web.calendar.review import _build_review_display_fields


def _tx(
    *,
    direction: str = "out",
    counterparty: str | None = None,
    memo: str | None = None,
    source: str | None = "csv",
    occurred_at: datetime | None = None,
    amount_krw: int = 0,
):
    return SimpleNamespace(
        direction=direction,
        counterparty=counterparty,
        memo=memo,
        source=source,
        occurred_at=occurred_at,
        amount_krw=amount_krw,
    )


class ReviewDetailFieldsTest(unittest.TestCase):
    def test_counterparty_has_highest_title_priority(self) -> None:
        row = _build_review_display_fields(
            _tx(
                counterparty="스타벅스 강남점",
                memo="법인카드 점심",
                source="popbill",
                occurred_at=datetime(2026, 3, 14, 12, 40),
                amount_krw=15300,
            ),
            account_badge={"name": "KB 주거래", "color_hex": "#64748B"},
        )
        self.assertEqual(row["display_title"], "스타벅스 강남점")
        self.assertEqual(row["display_time"], "03-14 12:40")
        self.assertEqual(row["display_amount"], 15300)
        self.assertEqual(row["display_account"], "KB 주거래")
        self.assertEqual(row["display_source"], "계좌 연동")

    def test_memo_summary_is_used_when_counterparty_missing(self) -> None:
        row = _build_review_display_fields(
            _tx(
                counterparty="",
                memo="카카오페이 자동결제(테스트) 장문 메모가 들어오는 케이스",
                source="csv",
                occurred_at=datetime(2026, 3, 1, 9, 5),
                amount_krw=8900,
            ),
            account_badge={"name": "우리 체크카드", "color_hex": "#64748B"},
        )
        self.assertTrue(row["display_title"].startswith("카카오페이 자동결제"))
        self.assertEqual(row["display_source"], "CSV 업로드")
        self.assertEqual(row["display_subtitle"], "지출 거래")

    def test_source_label_is_used_when_counterparty_and_memo_missing(self) -> None:
        row = _build_review_display_fields(
            _tx(
                direction="in",
                counterparty="",
                memo="",
                source="unknown_source",
                occurred_at=None,
                amount_krw=500000,
            ),
            account_badge={"name": "미지정", "color_hex": "#64748B"},
        )
        self.assertEqual(row["display_title"], "UNKNOWN_SOURCE")
        self.assertEqual(row["display_time"], "시간 정보 없음")
        self.assertEqual(row["display_account"], "계좌 정보 없음")
        self.assertEqual(row["display_subtitle"], "입금 거래")

    def test_memo_is_hidden_when_same_as_counterparty(self) -> None:
        row = _build_review_display_fields(
            _tx(counterparty="네이버페이", memo="네이버페이", source="csv"),
            account_badge={"name": "KB카드", "color_hex": "#64748B"},
        )
        self.assertEqual(row["display_title"], "네이버페이")
        self.assertEqual(row["display_memo"], "")

    def test_builder_always_emits_template_safe_display_keys(self) -> None:
        row = _build_review_display_fields(
            _tx(counterparty="", memo="", source="", occurred_at=None, amount_krw=0),
            account_badge={},
        )
        self.assertEqual(
            set(row.keys()),
            {
                "display_title",
                "display_subtitle",
                "display_time",
                "display_amount",
                "display_account",
                "display_source",
                "display_memo",
                "raw_counterparty",
            },
        )
        self.assertEqual(row["display_source"], "출처 미상")
        self.assertEqual(row["display_account"], "계좌 정보 없음")
        self.assertEqual(row["display_time"], "시간 정보 없음")

    def test_unknown_source_is_normalized_without_null_like_strings(self) -> None:
        row = _build_review_display_fields(
            _tx(counterparty=None, memo=None, source=None, occurred_at=None),
            account_badge={"name": "선택 계좌", "color_hex": "#64748B"},
        )
        for key in ("display_title", "display_source", "display_account", "display_time"):
            self.assertNotIn("None", str(row[key]))
            self.assertNotIn("null", str(row[key]).lower())


if __name__ == "__main__":
    unittest.main()

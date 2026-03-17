from __future__ import annotations

import unittest
from datetime import date, timedelta

from services.import_popbill import _build_backfill_max_3m_ranges, _shift_months


class PopbillBackfillRangeTest(unittest.TestCase):
    def test_builds_up_to_three_ranges(self):
        ranges = _build_backfill_max_3m_ranges(date(2026, 3, 8))
        self.assertTrue(1 <= len(ranges) <= 3)
        self.assertEqual(ranges[0][0], date(2025, 12, 9))
        self.assertEqual(ranges[-1][1], date(2026, 3, 8))

    def test_each_range_is_not_longer_than_one_month(self):
        ranges = _build_backfill_max_3m_ranges(date(2026, 5, 31))
        self.assertTrue(1 <= len(ranges) <= 3)
        for start, end in ranges:
            next_month = _shift_months(start, 1)
            self.assertLessEqual(end, next_month - timedelta(days=1))

    def test_ranges_are_contiguous(self):
        ranges = _build_backfill_max_3m_ranges(date(2026, 7, 17))
        for idx in range(1, len(ranges)):
            prev_end = ranges[idx - 1][1]
            cur_start = ranges[idx][0]
            self.assertEqual((prev_end + timedelta(days=1)), cur_start)


if __name__ == "__main__":
    unittest.main()

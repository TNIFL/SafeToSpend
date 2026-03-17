from __future__ import annotations

import unittest

from routes.web.package import _with_account_query


class PackageAccountQueryTest(unittest.TestCase):
    def test_keeps_all_without_append(self) -> None:
        self.assertEqual(_with_account_query('/dashboard/review?month=2026-03', 'all'), '/dashboard/review?month=2026-03')

    def test_appends_account_param(self) -> None:
        out = _with_account_query('/dashboard/review?month=2026-03&focus=receipt_required', '5')
        self.assertIn('account=5', out)
        self.assertIn('month=2026-03', out)
        self.assertIn('focus=receipt_required', out)


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SeasonalUxRenderTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_overview_wires_seasonal_experience_and_checklist(self) -> None:
        route_body = self._read("routes/web/overview.py")
        template_body = self._read("templates/overview.html")
        service_body = self._read("services/seasonal_ux.py")
        self.assertIn("build_seasonal_experience", route_body)
        self.assertIn('ctx["seasonal_experience"]', route_body)
        self.assertIn("decorate_seasonal_cards_for_tracking", route_body)
        self.assertIn("seasonal-checklist", template_body)
        self.assertIn("seasonal_experience.cards", template_body)
        self.assertIn("metric_cta_url", template_body)
        self.assertIn("priority_adjustment_reason", service_body)
        self.assertIn("priority_effective", service_body)

    def test_review_tax_buffer_package_render_context_blocks(self) -> None:
        review_route = self._read("routes/web/calendar/review.py")
        tax_route = self._read("routes/web/calendar/tax.py")
        package_route = self._read("routes/web/package.py")
        review_template = self._read("templates/calendar/review.html")
        tax_template = self._read("templates/calendar/tax_buffer.html")
        package_template = self._read("templates/package/index.html")

        self.assertIn("seasonal_context=seasonal_context", review_route)
        self.assertIn("seasonal_context=seasonal_context", tax_route)
        self.assertIn("seasonal_context=seasonal_context", package_route)
        self.assertIn("decorate_seasonal_context_for_tracking", review_route)
        self.assertIn("decorate_seasonal_context_for_tracking", tax_route)
        self.assertIn("decorate_seasonal_context_for_tracking", package_route)
        self.assertIn("seasonal-review-context", review_template)
        self.assertIn("seasonal-tax-buffer-context", tax_template)
        self.assertIn("seasonal-package-context", package_template)
        self.assertIn("metric_cta_url", review_template)
        self.assertIn("metric_cta_url", tax_template)
        self.assertIn("metric_cta_url", package_template)


if __name__ == "__main__":
    unittest.main()

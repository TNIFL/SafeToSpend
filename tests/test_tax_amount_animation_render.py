from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]


class TaxAmountAnimationRenderTest(unittest.TestCase):
    def test_base_template_loads_tax_animation_script_and_receipt_effect_toast_bridge(self) -> None:
        body = (ROOT / "templates/base.html").read_text(encoding="utf-8")
        self.assertIn("tax-number-animate.js", body)
        self.assertIn('request.args.get("receipt_effect_toast") == "1"', body)
        self.assertIn('delivery: "toast_and_center"', body)

    def test_review_and_tax_buffer_templates_render_animation_data_attributes(self) -> None:
        review = (ROOT / "templates/calendar/review.html").read_text(encoding="utf-8")
        tax_buffer = (ROOT / "templates/calendar/tax_buffer.html").read_text(encoding="utf-8")
        self.assertIn('data-tax-animate="currency"', review)
        self.assertIn('data-tax-changed="{{ 1 if receipt_effect_event', review)
        self.assertIn('data-tax-animate="currency"', tax_buffer)
        self.assertIn('data-tax-prefix="~"', tax_buffer)

    def test_month_template_animates_tax_estimate_and_safe_to_spend(self) -> None:
        month = (ROOT / "templates/calendar/month.html").read_text(encoding="utf-8")
        self.assertIn('data-tax-current-value="{{ month_safe_current }}"', month)
        self.assertIn('data-tax-current-value="{{ month_tax_current }}"', month)
        self.assertIn('data-tax-prefix="- "', month)
        self.assertIn("{{ tax_buffer_url }}", month)

    def test_animation_script_uses_server_current_and_previous_values(self) -> None:
        body = (ROOT / "static/js/tax-number-animate.js").read_text(encoding="utf-8")
        self.assertIn('node.dataset.taxCurrentValue', body)
        self.assertIn('node.dataset.taxPreviousValue', body)
        self.assertIn('node.dataset.taxChanged', body)
        self.assertIn('prefers-reduced-motion: reduce', body)

    def test_templates_parse_as_valid_jinja(self) -> None:
        env = Environment()
        for rel_path in (
            "templates/base.html",
            "templates/calendar/review.html",
            "templates/calendar/tax_buffer.html",
            "templates/calendar/month.html",
        ):
            body = (ROOT / rel_path).read_text(encoding="utf-8")
            env.parse(body)


if __name__ == "__main__":
    unittest.main()

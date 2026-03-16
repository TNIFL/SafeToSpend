from __future__ import annotations

import unittest

from sqlalchemy import CheckConstraint

from domain.models import OfficialDataDocument


class OfficialDataTrustFieldsTest(unittest.TestCase):
    def test_trust_and_verification_columns_exist(self) -> None:
        columns = set(OfficialDataDocument.__table__.columns.keys())
        expected = {
            "trust_grade",
            "trust_grade_label",
            "trust_scope_label",
            "structure_validation_status",
            "verification_source",
            "verification_status",
            "verification_checked_at",
            "verification_reference_masked",
            "user_modified_flag",
            "sensitive_data_redacted",
        }
        self.assertTrue(expected.issubset(columns))

    def test_conservative_defaults_exist(self) -> None:
        table = OfficialDataDocument.__table__
        self.assertEqual(table.c.structure_validation_status.default.arg, "not_applicable")
        self.assertEqual(table.c.verification_status.default.arg, "none")
        self.assertFalse(table.c.user_modified_flag.default.arg)
        self.assertTrue(table.c.sensitive_data_redacted.default.arg)
        self.assertTrue(table.c.trust_grade.nullable)

    def test_constraints_cover_trust_and_verification_values(self) -> None:
        constraints = " ".join(
            str(constraint.sqltext)
            for constraint in OfficialDataDocument.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        )
        self.assertIn("trust_grade", constraints)
        self.assertIn("verification_status", constraints)
        self.assertIn("structure_validation_status", constraints)
        self.assertNotIn("trust_grade = 'A'", constraints)


if __name__ == "__main__":
    unittest.main()

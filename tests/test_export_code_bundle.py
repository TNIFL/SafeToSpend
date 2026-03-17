from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import export_code_bundle


class ExportCodeBundleTest(unittest.TestCase):
    def _write_file(self, root: Path, rel_path: str, content: str = "x") -> Path:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_iter_bundle_files_excludes_runtime_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_file(root, "app.py", "print('ok')\n")
            self._write_file(root, ".env", "SECRET_KEY=dont-ship\n")
            self._write_file(root, ".env.example", "SECRET_KEY=replace-me\n")
            self._write_file(root, "uploads/evidence/file.txt", "sensitive\n")
            self._write_file(root, "reports/rehearsals/test.dump", "dump\n")
            self._write_file(root, "reports/official_data_effects_smoke.json", "{}\n")
            self._write_file(root, "__pycache__/mod.cpython-312.pyc", "pyc\n")

            with patch.object(export_code_bundle, "ROOT", root):
                include_paths, excluded = export_code_bundle._iter_bundle_files()

            included = {path.relative_to(root).as_posix() for path in include_paths}
            self.assertEqual(included, {".env.example", "app.py"})
            self.assertIn(".env", excluded)
            self.assertIn("uploads/evidence/file.txt", excluded)
            self.assertIn("reports/rehearsals/test.dump", excluded)
            self.assertIn("reports/official_data_effects_smoke.json", excluded)
            self.assertIn("__pycache__/mod.cpython-312.pyc", excluded)

    def test_main_writes_archive_without_forbidden_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "repo"
            output = Path(tmp_dir) / "bundle.zip"
            root.mkdir(parents=True, exist_ok=True)
            self._write_file(root, "app.py", "print('ok')\n")
            self._write_file(root, "services/sample.py", "VALUE = 1\n")
            self._write_file(root, "uploads/evidence/file.txt", "sensitive\n")

            with patch.object(export_code_bundle, "ROOT", root):
                exit_code = export_code_bundle.main(["--output", str(output)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.exists())
            with zipfile.ZipFile(output, mode="r") as zf:
                names = set(zf.namelist())
            self.assertEqual(names, {"app.py", "services/sample.py"})

    def test_main_dry_run_reports_excluded_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_file(root, "app.py", "print('ok')\n")
            self._write_file(root, "uploads/evidence/file.txt", "sensitive\n")

            with patch.object(export_code_bundle, "ROOT", root), patch("sys.stdout.write") as stdout_write:
                exit_code = export_code_bundle.main(["--dry-run"])

            self.assertEqual(exit_code, 0)
            written = "".join(call.args[0] for call in stdout_write.mock_calls if call.args)
            payload = json.loads(written)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["mode"], "dry-run")
            self.assertIn("uploads/evidence/file.txt", payload["excluded_samples"])


if __name__ == "__main__":
    unittest.main()

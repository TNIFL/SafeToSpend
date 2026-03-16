from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from scripts.reference_watchdog import run_watchdog


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "") -> None:
        self.status_code = int(status_code)
        self.text = str(text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


class ReferenceWatchdogTest(unittest.TestCase):
    def _write_config(self, root: Path, payload: dict) -> Path:
        path = root / "targets.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_initial_run_has_no_changed_alert(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._write_config(
                root,
                {
                    "allowed_domains": ["example.com"],
                    "targets": [
                        {
                            "key": "nhis_health_rate",
                            "url": "https://example.com/rate",
                            "patterns": ["건강보험료율"],
                            "keywords": ["건강보험", "보험료율"],
                        }
                    ]
                },
            )
            state = root / "state.json"

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                mocked_get.return_value = _FakeResponse(
                    text="건강보험 보험료율은 7.19% 입니다. 점수당 금액도 공지됩니다."
                )
                payload, code = run_watchdog(config_path=config, state_path=state, timeout=3, strict=False)

            self.assertEqual(code, 0)
            summary = payload.get("summary") or {}
            self.assertEqual(int(summary.get("checked_count") or 0), 1)
            self.assertEqual(int(summary.get("changed_count") or 0), 0)
            self.assertEqual(int(summary.get("failing_count") or 0), 0)
            target = (payload.get("targets") or {}).get("nhis_health_rate") or {}
            self.assertTrue(str(target.get("content_hash") or ""))
            self.assertTrue(state.exists())

    def test_changed_detection_returns_nonzero_in_strict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._write_config(
                root,
                {
                    "allowed_domains": ["example.com"],
                    "targets": [
                        {
                            "key": "nhis_health_rate",
                            "url": "https://example.com/rate",
                            "patterns": ["건강보험료율"],
                            "keywords": ["건강보험"],
                        }
                    ]
                },
            )
            state = root / "state.json"

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                mocked_get.return_value = _FakeResponse(text="건강보험료율 7.19%")
                run_watchdog(config_path=config, state_path=state, timeout=3, strict=False)

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                mocked_get.return_value = _FakeResponse(text="건강보험료율 7.50%")
                payload, code = run_watchdog(config_path=config, state_path=state, timeout=3, strict=True)

            self.assertEqual(code, 1)
            summary = payload.get("summary") or {}
            self.assertEqual(int(summary.get("changed_count") or 0), 1)
            self.assertEqual(int(summary.get("failing_count") or 0), 0)

    def test_keyword_missing_marked_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._write_config(
                root,
                {
                    "allowed_domains": ["example.com"],
                    "targets": [
                        {
                            "key": "nts_table",
                            "url": "https://example.com/nts",
                            "keywords": ["과세표준", "누진공제"],
                        }
                    ]
                },
            )
            state = root / "state.json"

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                mocked_get.return_value = _FakeResponse(text="키워드가 없는 일반 페이지")
                payload, code = run_watchdog(config_path=config, state_path=state, timeout=3, strict=True)

            self.assertEqual(code, 1)
            summary = payload.get("summary") or {}
            self.assertEqual(int(summary.get("failing_count") or 0), 1)
            target = (payload.get("targets") or {}).get("nts_table") or {}
            self.assertEqual(str(target.get("failure_reason") or ""), "keyword_not_found")
            self.assertEqual(int(target.get("failure_streak") or 0), 1)

    def test_failure_streak_increments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._write_config(
                root,
                {
                    "allowed_domains": ["example.com"],
                    "targets": [
                        {
                            "key": "nhis_health_rate",
                            "url": "https://example.com/rate",
                            "keywords": ["건강보험"],
                        }
                    ]
                },
            )
            state = root / "state.json"

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                mocked_get.return_value = _FakeResponse(text="키워드 없음")
                payload1, _ = run_watchdog(config_path=config, state_path=state, timeout=3, strict=False)
            target1 = (payload1.get("targets") or {}).get("nhis_health_rate") or {}
            self.assertEqual(int(target1.get("failure_streak") or 0), 1)

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                mocked_get.return_value = _FakeResponse(text="여전히 키워드 없음")
                payload2, _ = run_watchdog(config_path=config, state_path=state, timeout=3, strict=False)
            target2 = (payload2.get("targets") or {}).get("nhis_health_rate") or {}
            self.assertEqual(int(target2.get("failure_streak") or 0), 2)

    def test_domain_allowlist_blocks_untrusted_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._write_config(
                root,
                {
                    "allowed_domains": ["nhis.or.kr"],
                    "targets": [
                        {
                            "key": "blocked_target",
                            "url": "https://example.com/rate",
                        }
                    ],
                },
            )
            state = root / "state.json"

            with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
                payload, code = run_watchdog(config_path=config, state_path=state, timeout=3, strict=True)

            self.assertEqual(code, 1)
            self.assertEqual(mocked_get.call_count, 0)
            target = (payload.get("targets") or {}).get("blocked_target") or {}
            self.assertEqual(str(target.get("failure_reason") or ""), "domain_not_allowed")


if __name__ == "__main__":
    unittest.main()

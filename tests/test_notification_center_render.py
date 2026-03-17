from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NotificationCenterRenderTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_notice_center_uses_title_and_level_fallback(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn("const level = normalizeLevel(item.level || kind);", body)
        self.assertIn("const defaultTitle = (", body)
        self.assertIn('const title = escapeHtml(String(item.title || "").trim() || defaultTitle);', body)
        self.assertIn("<p class=\"title\">${title}</p>", body)

    def test_push_notice_stores_extended_meta(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn("function pushNotice({", body)
        self.assertIn("level = \"\",", body)
        self.assertIn("title = \"\",", body)
        self.assertIn("source = \"\",", body)
        self.assertIn("level: lvl,", body)
        self.assertIn("title: safeTitle,", body)
        self.assertIn("source: safeSource,", body)

    def test_notice_bridge_flushes_queued_calls_before_render(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn("const queuedBridgeCalls =", body)
        self.assertIn("if (fn === \"notify\") {", body)
        self.assertIn("else if (fn === \"pushNotice\") {", body)
        self.assertIn("bridgeFlashMessagesToCenter();", body)
        self.assertIn("renderNoticeRail();", body)

    def test_inline_toast_container_and_levels_exist(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn(".global-inline-toast-wrap", body)
        self.assertIn(".global-inline-toast-wrap .toast.failure", body)
        self.assertIn(".global-inline-toast-wrap .toast.info", body)
        self.assertIn("function pushInlineToast({ message, level = \"success\", title = \"\", timeoutMs = 2800, dedupeKey = \"\" })", body)


if __name__ == "__main__":
    unittest.main()

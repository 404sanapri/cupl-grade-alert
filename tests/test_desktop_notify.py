from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from grade import CourseGrade  # noqa: E402
from notify import DesktopNotifier, NullNotifier, build_notifier  # noqa: E402


class BuildTest(unittest.TestCase):
    def test_desktop_channel_needs_no_webhook(self) -> None:
        notifier = build_notifier("desktop", "")
        self.assertIsInstance(notifier, DesktopNotifier)

    def test_unknown_channel_falls_back_to_null(self) -> None:
        self.assertIsInstance(build_notifier("none", ""), NullNotifier)

    def test_dingtalk_still_requires_webhook(self) -> None:
        self.assertIsInstance(build_notifier("dingtalk", ""), NullNotifier)


class MessageTest(unittest.TestCase):
    def test_new_grades_notification_lists_only_course_names(self) -> None:
        calls: list[tuple[str, str]] = []
        grades = [CourseGrade("民法学原理四：亲属法与继承法", "2", "98", "6.3", "2025-2026-1")]
        with mock.patch("notify.toast", side_effect=lambda t, b, timeout=20: calls.append((t, b)) or True):
            DesktopNotifier().send_new_grades(grades)

        self.assertEqual(calls[0][0], "新增 1 门成绩")
        self.assertIn("民法学原理四", calls[0][1])
        self.assertNotIn("98", calls[0][1])
        self.assertNotIn("绩点", calls[0][1])

    def test_empty_grades_sends_nothing(self) -> None:
        with mock.patch("notify.toast") as fake:
            DesktopNotifier().send_new_grades([])
        fake.assert_not_called()

    def test_toast_failure_never_raises(self) -> None:
        with mock.patch("notify.toast", return_value=False):
            notifier = DesktopNotifier()
            notifier.send_new_grades([CourseGrade("A", term="2024-1")])
            notifier.send_alert("boom")


class StrongAlertTest(unittest.TestCase):
    def test_hook_called_when_strong(self) -> None:
        seen: list[str] = []
        with mock.patch("notify.toast", return_value=True):
            notifier = DesktopNotifier(strong=True, notice_hook=lambda title, body: seen.append(title))
            notifier.send_new_grades([CourseGrade("A", term="2024-1")])
        self.assertEqual(seen, ["新增 1 门成绩"])

    def test_hook_skipped_when_not_strong(self) -> None:
        seen: list[str] = []
        with mock.patch("notify.toast", return_value=True):
            notifier = DesktopNotifier(strong=False, notice_hook=lambda title, body: seen.append(title))
            notifier.send_new_grades([CourseGrade("A", term="2024-1")])
        self.assertEqual(seen, [])

    def test_alert_never_interrupts(self) -> None:
        """异常告警只发普通通知，不该弹置顶窗口。"""
        seen: list[str] = []
        with mock.patch("notify.toast", return_value=True):
            notifier = DesktopNotifier(strong=True, notice_hook=lambda title, body: seen.append(title))
            notifier.send_alert("boom")
            notifier.send_text("hello", "标题")
        self.assertEqual(seen, [])

    def test_broken_hook_does_not_propagate(self) -> None:
        def boom(title: str, body: str) -> None:
            raise RuntimeError("窗口已关闭")

        with mock.patch("notify.toast", return_value=True):
            DesktopNotifier(strong=True, notice_hook=boom).send_new_grades([CourseGrade("A", term="2024-1")])


if __name__ == "__main__":
    unittest.main()

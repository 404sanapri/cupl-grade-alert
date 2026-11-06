from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from grade import CourseGrade  # noqa: E402
from notify import (  # noqa: E402
    DingTalkNotifier,
    FeishuNotifier,
    NullNotifier,
    dingtalk_sign,
    feishu_sign,
    format_alert,
    format_new_grades,
)


class SignTest(unittest.TestCase):
    def test_dingtalk_sign_is_deterministic_and_url_encoded(self) -> None:
        first = dingtalk_sign("my-secret", 1700000000000)
        second = dingtalk_sign("my-secret", 1700000000000)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 20)
        self.assertNotIn("+", first)  # quote_plus 后不应有裸加号
        self.assertNotEqual(first, dingtalk_sign("my-secret", 1700000000001))

    def test_feishu_sign_length(self) -> None:
        signature = feishu_sign("my-secret", 1700000000)
        self.assertEqual(len(signature), 44)  # SHA-256 的 Base64
        self.assertNotEqual(signature, feishu_sign("my-secret", 1700000001))


class EndpointTest(unittest.TestCase):
    def test_dingtalk_endpoint_contains_signature_when_secret_present(self) -> None:
        notifier = DingTalkNotifier("https://oapi.dingtalk.com/robot/send?access_token=abc", "secret")
        endpoint = notifier._endpoint()
        self.assertIn("timestamp=", endpoint)
        self.assertIn("sign=", endpoint)
        self.assertIn("access_token=abc", endpoint)

    def test_dingtalk_endpoint_unchanged_without_secret(self) -> None:
        url = "https://oapi.dingtalk.com/robot/send?access_token=abc"
        self.assertEqual(DingTalkNotifier(url)._endpoint(), url)

    def test_feishu_payload_contains_signature(self) -> None:
        payload = FeishuNotifier("https://open.feishu.cn/x", "secret")._payload("hello", "title")
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("sign", payload)
        self.assertIn("timestamp", payload)


class MessageTest(unittest.TestCase):
    def test_new_grade_message_lists_only_course_names(self) -> None:
        grades = [CourseGrade("法学基本理论", "2", "98", "6.3", "2024-2025-2")]
        text = format_new_grades(grades)
        self.assertIn("新增 1 门成绩", text)
        self.assertIn("法学基本理论", text)
        # 只报科目，不带分数 / 学分 / 绩点
        self.assertNotIn("98", text)
        self.assertNotIn("学分", text)
        self.assertNotIn("绩点", text)

    def test_alert_message(self) -> None:
        self.assertIn("监控异常", format_alert("登录失败"))

    def test_null_notifier_never_raises(self) -> None:
        notifier = NullNotifier()
        notifier.send_new_grades([CourseGrade("A", term="2024-1")])
        notifier.send_alert("boom")


if __name__ == "__main__":
    unittest.main()

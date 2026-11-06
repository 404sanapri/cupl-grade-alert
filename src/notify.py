"""可插拔通知适配器：钉钉 / 飞书自定义机器人。

加签算法（GitHub Actions 出口 IP 动态，无法用 IP 白名单，只能用加签或关键词）：
  - 钉钉：sign = urlencode(base64(HMAC-SHA256(key=secret, msg=f"{ts}\n{secret}")))
  - 飞书：sign = base64(HMAC-SHA256(key=f"{ts}\n{secret}", msg=b""))
消息只包含课程名 / 学分 / 分数 / 绩点 / 学期，不含学号、密码或 Cookie。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime
from typing import Protocol
from urllib.parse import quote_plus

import requests

from grade import CourseGrade

log = logging.getLogger(__name__)

_SEND_RETRIES = 2
_TIMEOUT = 15


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def dingtalk_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    return quote_plus(base64.b64encode(digest).decode("utf-8"))


def feishu_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class Notifier(Protocol):
    def send_new_grades(self, grades: list[CourseGrade]) -> None: ...

    def send_alert(self, reason: str) -> None: ...


def format_new_grades(grades: list[CourseGrade]) -> str:
    """推送只暴露科目名：分数、学分、绩点一律不进通知。"""
    lines = [f"### 新增 {len(grades)} 门成绩", ""]
    for item in grades:
        lines.append(f"- {item.course_name}")
    lines.append("")
    lines.append(f"检测时间：{_now_str()}")
    return "\n".join(lines)


def format_alert(reason: str) -> str:
    return f"### 成绩监控异常\n\n{reason}\n\n时间：{_now_str()}"


class _WebhookNotifier:
    """共同的发送与重试逻辑；发送失败只记日志，绝不把主流程带崩。"""

    def __init__(self, webhook_url: str, secret: str = "") -> None:
        self.webhook_url = webhook_url
        self.secret = secret

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _payload(self, text: str, title: str) -> dict:
        raise NotImplementedError

    def _post(self, text: str, title: str) -> bool:
        url = self._endpoint()
        payload = self._payload(text, title)
        for attempt in range(_SEND_RETRIES + 1):
            try:
                resp = requests.post(url, json=payload, timeout=_TIMEOUT)
                data: dict = {}
                if "json" in resp.headers.get("Content-Type", ""):
                    data = resp.json()
                code = data.get("errcode", data.get("code", 0))
                if resp.ok and code in (0, None):
                    log.info("通知发送成功：%s", title)
                    return True
                log.warning(
                    "通知发送失败 attempt=%d code=%s msg=%s",
                    attempt,
                    code,
                    data.get("errmsg") or data.get("msg") or "",
                )
            except (requests.RequestException, ValueError) as exc:
                log.warning("通知发送异常 attempt=%d：%s", attempt, type(exc).__name__)
            if attempt < _SEND_RETRIES:
                time.sleep(1 + attempt)
        log.error("通知连续发送失败，已放弃本次推送：%s", title)
        return False

    def send_new_grades(self, grades: list[CourseGrade]) -> None:
        if grades:
            self._post(format_new_grades(grades), "教务成绩更新")

    def send_alert(self, reason: str) -> None:
        self._post(format_alert(reason), "成绩监控异常")

    def send_text(self, text: str, title: str = "通知") -> bool:
        return self._post(text, title)


class DingTalkNotifier(_WebhookNotifier):
    def _endpoint(self) -> str:
        if not self.secret:
            return self.webhook_url
        timestamp = int(time.time() * 1000)
        separator = "&" if "?" in self.webhook_url else "?"
        return (
            f"{self.webhook_url}{separator}timestamp={timestamp}"
            f"&sign={dingtalk_sign(self.secret, timestamp)}"
        )

    def _payload(self, text: str, title: str) -> dict:
        return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}


class FeishuNotifier(_WebhookNotifier):
    def _payload(self, text: str, title: str) -> dict:
        payload: dict = {"msg_type": "text", "content": {"text": f"{title}\n\n{text}"}}
        if self.secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = feishu_sign(self.secret, timestamp)
        return payload


class NullNotifier:
    """未配置通知渠道时使用，只打印到日志。"""

    def send_new_grades(self, grades: list[CourseGrade]) -> None:
        log.info("[未配置通知] 新增 %d 门成绩", len(grades))

    def send_alert(self, reason: str) -> None:
        log.warning("[未配置通知] %s", reason)

    def send_text(self, text: str, title: str = "通知") -> bool:
        log.info("[未配置通知] %s：%s", title, text[:120].replace("\n", " "))
        return False


def build_notifier(channel: str, webhook_url: str, secret: str = "") -> Notifier:
    if channel == "dingtalk" and webhook_url:
        return DingTalkNotifier(webhook_url, secret)
    if channel == "feishu" and webhook_url:
        return FeishuNotifier(webhook_url, secret)
    return NullNotifier()


def send_text(channel: str, webhook_url: str, secret: str, text: str, title: str = "通知") -> bool:
    """给公告广播层与界面复用的通用文本发送。"""
    notifier = build_notifier(channel, webhook_url, secret)
    send = getattr(notifier, "send_text", None)
    if send is None:
        log.info("[未配置通知] %s", title)
        return False
    return bool(send(text, title))

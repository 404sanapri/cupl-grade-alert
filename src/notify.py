"""可插拔通知适配器：Windows 本地通知（默认）/ 钉钉 / 飞书自定义机器人。

电脑本地通知通过 PowerShell 的 WinRT Toast API 实现，不需要任何第三方库，
也不需要注册 AppID；发不出去时只记日志，绝不影响主流程。

钉钉 / 飞书的加签算法保留给公告广播层用：
  - 钉钉：sign = urlencode(base64(HMAC-SHA256(key=secret, msg=f"{ts}\n{secret}")))
  - 飞书：sign = base64(HMAC-SHA256(key=f"{ts}\n{secret}", msg=b""))
消息只包含课程名 / 学分 / 分数 / 绩点 / 学期，不含学号、密码或 Cookie。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Callable, Protocol
from urllib.parse import quote_plus
from xml.sax.saxutils import escape

import requests

from grade import CourseGrade

log = logging.getLogger(__name__)

_SEND_RETRIES = 2
_TIMEOUT = 15

# 借用 Windows 自带的 PowerShell AUMID：系统已注册，toast 才敢往外弹
_TOAST_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($env:CUPL_TOAST_XML)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CUPL_TOAST_AUMID).Show($toast)
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


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


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plain(text: str) -> str:
    """把 markdown 消息压成适合通知正文的纯文本。"""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lines.append(line.lstrip("#").lstrip("-").strip())
    return _shorten("；".join(lines), 200)


def toast(title: str, body: str, timeout: int = 20) -> bool:
    """弹一条 Windows 通知。失败只返回 False，不抛异常。"""
    if sys.platform != "win32":
        return False
    xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual></toast>"
    )
    encoded = base64.b64encode(_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ)
    env["CUPL_TOAST_XML"] = xml
    env["CUPL_TOAST_AUMID"] = _TOAST_AUMID
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded,
    ]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("调用系统通知失败：%s", type(exc).__name__)
        return False
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "ignore").strip() or f"exit {proc.returncode}"
        log.warning("系统通知发送失败：%s", detail)
        return False
    return True


class DesktopNotifier:
    """电脑本地通知。

    strong=True 且提供了 notice_hook 时，除了 toast 还会回调一层「强提醒」
    （桌面程序用它弹置顶窗口 + 闪烁任务栏），命令行场景下 hook 为空则自动跳过。
    """

    def __init__(
        self, strong: bool = False, notice_hook: Callable[[str, str], None] | None = None
    ) -> None:
        self.strong = strong
        self.notice_hook = notice_hook
        self._warned = False

    def send_new_grades(self, grades: list[CourseGrade]) -> None:
        if not grades:
            return
        names = [item.course_name for item in grades]
        # 只有新成绩才值得打断你，异常告警走普通通知
        self._notify(f"新增 {len(names)} 门成绩", "、".join(names), strong=True)

    def send_alert(self, reason: str) -> None:
        self._notify("成绩监控异常", _shorten(reason, 200))

    def send_text(self, text: str, title: str = "通知") -> bool:
        return self._notify(title, _plain(text))

    def _notify(self, title: str, body: str, strong: bool = False) -> bool:
        body = _shorten(body, 200)
        ok = toast(title, body)
        if not ok and not self._warned:
            self._warned = True
            log.warning("系统通知发不出去，之后只更新界面（可在设置里改用强提醒）")
        if strong and self.strong and self.notice_hook is not None:
            try:
                self.notice_hook(title, body)
            except Exception as exc:  # noqa: BLE001
                log.warning("强提醒回调失败：%s", type(exc).__name__)
        return ok


def build_notifier(
    channel: str,
    webhook_url: str,
    secret: str = "",
    *,
    strong: bool = False,
    notice_hook: Callable[[str, str], None] | None = None,
) -> Notifier:
    if channel == "desktop":
        return DesktopNotifier(strong=strong, notice_hook=notice_hook)
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

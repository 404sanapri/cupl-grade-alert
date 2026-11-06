"""本机网页界面：配置账号、查看成绩、开关监控。

只用标准库起一个仅监听 127.0.0.1 的小服务，不引入新依赖。
启动后浏览器自动打开 http://127.0.0.1:8765。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import config  # noqa: E402
import grade  # noqa: E402
import monitor  # noqa: E402
import notify  # noqa: E402
import state  # noqa: E402
from notify import NullNotifier, build_notifier  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / "config" / ".env"
RUN_LOG = PROJECT_ROOT / "logs" / "runs.jsonl"
DEFAULT_STATE = PROJECT_ROOT / "state" / "grades.json"
STATIC_FILES = {"index.html", "style.css", "app.js"}

log = logging.getLogger("ui")


# ---------- 配置读写 ----------


def _read_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        return {}
    result: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _write_env(values: dict[str, str]) -> None:
    current = _read_env()
    current.update(values)
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in current.items() if key]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _state_path() -> Path:
    raw = _read_env().get("STATE_PATH", "").strip()
    if not raw:
        return DEFAULT_STATE
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _mask_user(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return value[:2] + "*" * max(0, len(value) - 2)
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


def _mask_webhook(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    tail = url[-4:]
    return f"{parts.netloc}/…{tail}" if parts.netloc else f"…{tail}"


# ---------- 运行历史 ----------


def _append_run(entry: dict) -> None:
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_runs(limit: int = 30) -> list[dict]:
    if not RUN_LOG.is_file():
        return []
    lines = RUN_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    runs = []
    for line in lines:
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(runs))


# ---------- 定时监控 ----------


class Scheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exec = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.interval = 10
        self.last: dict | None = None

    def status(self) -> dict:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            return {"running": running, "interval": self.interval, "last": self.last}

    def start(self, interval: int) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.interval = max(3, min(120, int(interval)))
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not (self._thread and self._thread.is_alive()):
                return False
            self._stop.set()
            self._thread = None
            return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.trigger("scheduled")
            self._stop.wait(self.interval * 60)

    def trigger(self, source: str) -> dict:
        started = time.time()
        entry: dict = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": source}
        with self._exec:
            try:
                config.reload_dotenv()
                settings = config.load_settings()
                notifier = (
                    build_notifier(settings.channel, settings.webhook_url, settings.webhook_secret)
                    if settings.notify_enabled
                    else NullNotifier()
                )
                result = monitor.run_once(settings, notifier)
                entry.update(
                    {
                        "ok": True,
                        "total": result["total"],
                        "new": [g.course_name for g in result["new_grades"]],
                        "baseline_created": result["baseline_created"],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("运行失败：%s", exc)
                entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        entry["elapsed"] = round(time.time() - started, 1)
        with self._lock:
            self.last = entry
        _append_run(entry)
        return entry


scheduler = Scheduler()


# ---------- 业务接口 ----------


def api_overview() -> dict:
    env = _read_env()
    try:
        snapshot = state.load(_state_path())
    except Exception:  # noqa: BLE001
        snapshot = {}
    return {
        "configured": bool(env.get("CUPL_USERNAME") and env.get("CUPL_PASSWORD")),
        "username": _mask_user(env.get("CUPL_USERNAME", "")),
        "channel": (env.get("NOTIFY_CHANNEL") or "dingtalk").lower(),
        "webhook": _mask_webhook(env.get("NOTIFY_WEBHOOK", "")),
        "has_webhook": bool(env.get("NOTIFY_WEBHOOK")),
        "has_secret": bool(env.get("NOTIFY_SECRET")),
        "state": {
            "baseline_ready": bool(snapshot.get("baseline_ready")),
            "count": len(snapshot.get("keys", [])),
            "updated_at": snapshot.get("updated_at"),
            "failures": int(snapshot.get("consecutive_failures", 0)),
        },
        "scheduler": scheduler.status(),
    }


def api_save_config(payload: dict) -> dict:
    """空字段表示「保持原值」，避免用户只改一项时把密码或密钥清空。"""
    existing = _read_env()
    username = str(payload.get("username", "")).strip() or existing.get("CUPL_USERNAME", "").strip()
    password = str(payload.get("password", ""))
    channel = str(payload.get("channel", existing.get("NOTIFY_CHANNEL", "dingtalk"))).strip().lower()
    webhook = str(payload.get("webhook", "")).strip() or existing.get("NOTIFY_WEBHOOK", "").strip()
    secret = str(payload.get("secret", "")).strip()

    if channel not in {"dingtalk", "feishu", "none"}:
        raise ValueError("通知渠道只能是 dingtalk / feishu / none")
    if not username:
        raise ValueError("请填写学号")
    if not password and not existing.get("CUPL_PASSWORD"):
        raise ValueError("首次配置必须填写密码")
    if channel != "none" and not webhook:
        raise ValueError("已选择通知渠道，请填写 Webhook")

    values = {
        "CUPL_USERNAME": username,
        "AUTH_MODE": "tpass",
        "NOTIFY_CHANNEL": channel,
    }
    if password:
        values["CUPL_PASSWORD"] = password
    if webhook:
        values["NOTIFY_WEBHOOK"] = webhook
    if secret:
        values["NOTIFY_SECRET"] = secret

    _write_env(values)
    config.reload_dotenv()
    return {"saved": True}


def api_test_login() -> dict:
    """只返回本学期已出分的科目名，不向前端暴露任何分数信息。"""
    config.reload_dotenv()
    settings = config.load_settings(dry_run_override=True)
    result = monitor.run_once(settings, NullNotifier(), dry_run=True)

    term = grade.current_term()
    courses = grade.courses_of_term(result["grades"], term)
    return {
        "term": term,
        "term_label": grade.term_label(term),
        "count": len(courses),
        "courses": courses,
        "all_count": result["total"],
        "available_terms": sorted({item.term for item in result["grades"] if item.term}),
        "baseline_created": result["baseline_created"],
    }


def api_test_notify() -> dict:
    config.reload_dotenv()
    settings = config.load_settings(dry_run_override=True)
    if not settings.notify_enabled:
        raise ValueError("还没配置通知渠道，请先在上面填写 Webhook")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok = notify.send_text(
        settings.channel,
        settings.webhook_url,
        settings.webhook_secret,
        f"### 测试消息\n\n收到这条消息，说明通知渠道配置正确。\n\n时间：{stamp}",
        "成绩提醒测试",
    )
    if not ok:
        raise RuntimeError("发送失败，请检查加签密钥或自定义关键词")
    return {"sent": True}


def api_trigger_run() -> dict:
    entry = scheduler.trigger("manual")
    if not entry.get("ok"):
        raise RuntimeError(entry.get("error", "运行失败"))
    return entry


def api_reset() -> dict:
    state.reset(_state_path())
    return {"reset": True}


# ---------- HTTP ----------


class Handler(BaseHTTPRequestHandler):
    server_version = "GradeAlertUI/0.1"

    def log_message(self, fmt: str, *args) -> None:  # 静默访问日志
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/overview":
                return self._json({"ok": True, "data": api_overview()})
            if path == "/api/runs":
                return self._json({"ok": True, "data": {"runs": read_runs()}})
            return self._static(path)
        except Exception as exc:  # noqa: BLE001
            log.exception("GET %s 失败", path)
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_payload()
        try:
            if path == "/api/config":
                data = api_save_config(payload)
            elif path == "/api/test-login":
                data = api_test_login()
            elif path == "/api/test-notify":
                data = api_test_notify()
            elif path == "/api/run":
                data = api_trigger_run()
            elif path == "/api/reset":
                data = api_reset()
            elif path == "/api/scheduler/start":
                data = {"running": scheduler.start(int(payload.get("interval", 10)))}
            elif path == "/api/scheduler/stop":
                data = {"running": False if scheduler.stop() else scheduler.status()["running"]}
            else:
                return self._json({"ok": False, "error": "未知接口"}, 404)
            return self._json({"ok": True, "data": data})
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            log.exception("POST %s 失败", path)
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    # ---------- 工具 ----------

    def _read_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        if name not in STATIC_FILES:
            return self._json({"ok": False, "error": "not found"}, 404)
        file_path = WEB_DIR / name
        if not file_path.is_file():
            return self._json({"ok": False, "error": "not found"}, 404)
        content = file_path.read_bytes()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in ("application/javascript",):
            mime += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def _find_port(start: int = 8765) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    port = _find_port()
    if "--port" in sys.argv:
        index = sys.argv.index("--port")
        if index + 1 < len(sys.argv):
            port = int(sys.argv[index + 1])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print()
    print("  成绩提醒界面已启动")
    print(f"  {url}")
    print("  关闭这个窗口或按 Ctrl+C 即停止")
    print()
    if not os.environ.get("GRADE_UI_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

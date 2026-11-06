"""桌面版入口：打开就开始监测，关掉窗口就停止。

双击 exe（或 `python app.py`）后：
  - 立即检查一次成绩；
  - 之后按设定的间隔（默认 10 分钟）自动检查；
  - 窗口关闭 → 定时线程结束、进程退出，不再有任何后台活动。

配置写在 exe 同目录的 config\\.env 里，首次打开会自动弹出设置窗口。
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

import config  # noqa: E402
import monitor  # noqa: E402
import runlog  # noqa: E402
from notify import NullNotifier, build_notifier  # noqa: E402

APP_TITLE = "法大成绩提醒"
APP_LOG = logging.getLogger("app")
ENV_ORDER = (
    "CUPL_USERNAME",
    "CUPL_PASSWORD",
    "AUTH_MODE",
    "NOTIFY_CHANNEL",
    "NOTIFY_WEBHOOK",
    "NOTIFY_SECRET",
    "CHECK_INTERVAL_MIN",
)
FONT = ("Microsoft YaHei UI", 9)


def _ensure_std_streams() -> None:
    """PyInstaller 的 --windowed 模式下 stdout/stderr 是 None，任何 print 都会崩。"""
    global _DEVNULL
    if sys.stdout is None or sys.stderr is None:
        _DEVNULL = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _DEVNULL
        if sys.stderr is None:
            sys.stderr = _DEVNULL


_DEVNULL = None


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env(path: Path, patch: dict[str, str]) -> None:
    current = _read_env(path)
    current.update(patch)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 由桌面程序写入，含明文密码，请勿提交到仓库"]
    for key in ENV_ORDER:
        value = current.get(key, "")
        if value:
            lines.append(f"{key}={value}")
    for key, value in current.items():
        if key not in ENV_ORDER and value:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _QueueHandler(logging.Handler):
    def __init__(self, sink: queue.Queue[str]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class DesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.env_path = config.PROJECT_ROOT / "config" / ".env"
        self.busy = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.closed = False
        self.interval = self._load_interval()
        self.last_result = "尚未检查"
        self._log_lines = 0

        self.log_queue: queue.Queue[str] = queue.Queue()
        self._setup_logging()
        self._build_ui()
        self._poll_log()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self._boot)

    # ---------- 基础设施 ----------

    def _load_interval(self) -> int:
        raw = _read_env(self.env_path).get("CHECK_INTERVAL_MIN", "10")
        try:
            value = int(raw)
        except ValueError:
            value = 10
        return max(3, min(120, value))

    def _setup_logging(self) -> None:
        log_dir = config.PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "app.log"

        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        for old in list(root_logger.handlers):
            root_logger.removeHandler(old)

        queue_handler = _QueueHandler(self.log_queue)
        queue_handler.setFormatter(formatter)
        root_logger.addHandler(queue_handler)

        try:
            file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except OSError:
            pass

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("640x500")
        self.root.minsize(560, 420)
        self.root.option_add("*Font", FONT)
        self.root.configure(padx=12, pady=10)

        status_box = ttk.LabelFrame(self.root, text="状态")
        status_box.pack(fill="x")

        self.status_var = tk.StringVar(value="● 已停止")
        self.status_label = tk.Label(
            status_box, textvariable=self.status_var, font=("Microsoft YaHei UI", 13, "bold"), anchor="w"
        )
        self.status_label.pack(fill="x", padx=10, pady=(8, 2))

        self.detail_var = tk.StringVar(value="上次检查：尚未检查")
        ttk.Label(status_box, textvariable=self.detail_var, anchor="w").pack(fill="x", padx=10)
        self.hint_var = tk.StringVar(value="窗口开着才会监测，关掉即停。")
        ttk.Label(status_box, textvariable=self.hint_var, foreground="#666666", anchor="w").pack(
            fill="x", padx=10, pady=(0, 8)
        )

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="立即检查", command=self.manual_check).pack(side="left")
        self.toggle_button = ttk.Button(buttons, text="暂停监测", command=self.toggle_run)
        self.toggle_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="设置…", command=self.open_settings).pack(side="left")
        ttk.Button(buttons, text="打开日志", command=self.open_logs).pack(side="left", padx=6)
        ttk.Button(buttons, text="退出", command=self.on_close).pack(side="right")

        log_box = ttk.LabelFrame(self.root, text="运行日志")
        log_box.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_box, height=14, wrap="none", state="disabled", bg="#111111", fg="#dddddd")
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)

    # ---------- 启动 ----------

    def _boot(self) -> None:
        if not self.env_path.is_file() or not _read_env(self.env_path).get("CUPL_USERNAME"):
            APP_LOG.warning("还没有配置账号，先填一下设置")
            self.open_settings(first_run=True)
            return
        self.start()

    # ---------- 监测循环 ----------

    def start(self) -> None:
        if self.running:
            return
        self.stop_event = threading.Event()
        self.running = True
        self.worker = threading.Thread(target=self._loop, name="monitor-loop", daemon=True)
        self.worker.start()
        self._set_status(f"● 监测中（每 {self.interval} 分钟）", "#137333")
        self.toggle_button.configure(text="暂停监测")
        APP_LOG.info("开始监测，间隔 %d 分钟", self.interval)

    def pause(self) -> None:
        if not self.running:
            return
        self.running = False
        self.stop_event.set()
        self._set_status("● 已暂停", "#a8071a")
        self.toggle_button.configure(text="继续监测")
        APP_LOG.info("已暂停监测（窗口仍然开着，但不再检查）")

    def toggle_run(self) -> None:
        if self.running:
            self.pause()
        else:
            self.start()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self._run_once()
            if self.stop_event.wait(self.interval * 60):
                break

    def manual_check(self) -> None:
        if self.busy.locked():
            APP_LOG.info("上一次检查还没结束，请稍等")
            return
        threading.Thread(target=self._run_once, name="manual-check", daemon=True).start()

    def _run_once(self) -> None:
        if not self.busy.acquire(blocking=False):
            return
        started = time.time()
        try:
            config.reload_dotenv()
            try:
                settings = config.load_settings()
            except config.ConfigError as exc:
                APP_LOG.error("配置不完整：%s", exc)
                self._after_status("未配置", "请点「设置…」填写学号与密码", "#a8071a")
                return

            notifier = (
                build_notifier(settings.channel, settings.webhook_url, settings.webhook_secret)
                if settings.notify_enabled
                else NullNotifier()
            )
            result, code = monitor.run_checked(settings, notifier)
            if result is None:
                summary = "检查失败，原因见日志"
                self._after_status(self._current_status_text(), f"上次检查：{self._now()} · {summary}", "#a8071a", detail_only=True)
            else:
                summary = self._summarize(result)
                self._after_status(self._current_status_text(), f"上次检查：{self._now()} · {summary}", None, detail_only=True)
                APP_LOG.info("本次结果：%s", summary)
            self._log_run(result, summary, code, time.time() - started)
        except Exception as exc:  # noqa: BLE001
            APP_LOG.error("未预期异常：%s", exc)
            APP_LOG.debug(traceback.format_exc())
        finally:
            self.busy.release()

    def _summarize(self, result: dict) -> str:
        if result.get("baseline_created"):
            return f"已建立基线，共 {result['total']} 门（本次不推送）"
        new_grades = result.get("new_grades") or []
        if new_grades:
            names = "、".join(grade.course_name for grade in new_grades)
            return f"新增 {len(new_grades)} 门：{names}"
        return f"无新增（共 {result['total']} 门）"

    def _log_run(self, result: dict | None, summary: str, code: int, elapsed: float) -> None:
        entry = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "desktop",
            "ok": code == 0 and result is not None,
            "total": (result or {}).get("total", 0),
            "new": [grade.course_name for grade in (result or {}).get("new_grades", [])],
            "baseline_created": (result or {}).get("baseline_created", False),
            "elapsed": round(elapsed, 1),
        }
        if result is None:
            entry["error"] = "见 logs\\app.log"
        try:
            runlog.append(entry)
        except OSError:
            pass

    # ---------- 状态与日志刷新 ----------

    def _now(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _current_status_text(self) -> str:
        if self.running:
            return f"● 监测中（每 {self.interval} 分钟）"
        return "● 已暂停"

    def _set_status(self, text: str, color: str | None) -> None:
        def apply() -> None:
            if self.closed:
                return
            self.status_var.set(text)
            if color:
                self.status_label.configure(fg=color)

        self._safe_after(apply)

    def _after_status(
        self, text: str, detail: str, color: str | None, detail_only: bool = False
    ) -> None:
        def apply() -> None:
            if self.closed:
                return
            if not detail_only:
                self.status_var.set(text)
                if color:
                    self.status_label.configure(fg=color)
            self.detail_var.set(detail)

        self._safe_after(apply)

    def _safe_after(self, callback) -> None:
        """窗口销毁后还可能有工作线程回调进来，这里兜住 TclError。"""
        try:
            self.root.after(0, callback)
        except tk.TclError:
            pass

    def _poll_log(self) -> None:
        if self.closed:
            return
        chunk: list[str] = []
        try:
            while True:
                chunk.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if chunk:
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", "\n".join(chunk) + "\n")
                self._log_lines += len(chunk)
                if self._log_lines > 800:
                    self.log_text.delete("1.0", f"{self._log_lines - 500}.0")
                    self._log_lines = 500
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            except tk.TclError:
                return
        try:
            self.root.after(150, self._poll_log)
        except tk.TclError:
            pass

    # ---------- 设置 ----------

    def open_settings(self, first_run: bool = False) -> None:
        values = _read_env(self.env_path)

        win = tk.Toplevel(self.root)
        win.title("设置")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        win.option_add("*Font", FONT)

        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)

        def add_row(row: int, label: str, show: str | None = None) -> ttk.Entry:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, width=38, show=show or "")
            entry.grid(row=row, column=1, sticky="we", pady=4)
            return entry

        if first_run:
            ttk.Label(
                frame,
                text="第一次使用，先填学号与统一认证密码。",
                foreground="#a8071a",
            ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        offset = 1 if first_run else 0
        user_entry = add_row(offset + 0, "学号")
        user_entry.insert(0, values.get("CUPL_USERNAME", ""))
        pass_entry = add_row(offset + 1, "统一认证密码", show="*")
        pass_entry.insert(0, values.get("CUPL_PASSWORD", ""))

        ttk.Label(frame, text="通知渠道").grid(row=offset + 2, column=0, sticky="w", pady=4)
        channel = ttk.Combobox(
            frame, values=("dingtalk", "feishu", "none"), state="readonly", width=36
        )
        channel.set(values.get("NOTIFY_CHANNEL", "dingtalk") or "dingtalk")
        channel.grid(row=offset + 2, column=1, sticky="we", pady=4)

        webhook_entry = add_row(offset + 3, "机器人 Webhook")
        webhook_entry.insert(0, values.get("NOTIFY_WEBHOOK", ""))
        secret_entry = add_row(offset + 4, "加签密钥（可空）")
        secret_entry.insert(0, values.get("NOTIFY_SECRET", ""))

        ttk.Label(frame, text="检查间隔（分钟）").grid(row=offset + 5, column=0, sticky="w", pady=4)
        interval = tk.Spinbox(frame, from_=3, to=120, width=36, font=FONT)
        interval.delete(0, "end")
        interval.insert(0, str(self._load_interval()))
        interval.grid(row=offset + 5, column=1, sticky="we", pady=4)

        ttk.Label(
            frame,
            text="密码只写入本机 config\\.env，不上传任何地方。",
            foreground="#666666",
        ).grid(row=offset + 6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def save() -> None:
            username = user_entry.get().strip()
            password = pass_entry.get()
            if not username or not password:
                messagebox.showwarning(APP_TITLE, "学号和密码都要填。", parent=win)
                return
            try:
                minutes = max(3, min(120, int(interval.get())))
            except ValueError:
                messagebox.showwarning(APP_TITLE, "间隔要填 3～120 的整数。", parent=win)
                return
            hook = webhook_entry.get().strip()
            if channel.get() != "none" and not hook:
                messagebox.showwarning(APP_TITLE, "选了通知渠道就要填 Webhook。", parent=win)
                return

            _write_env(
                self.env_path,
                {
                    "CUPL_USERNAME": username,
                    "CUPL_PASSWORD": password,
                    "AUTH_MODE": "tpass",
                    "NOTIFY_CHANNEL": channel.get(),
                    "NOTIFY_WEBHOOK": hook,
                    "NOTIFY_SECRET": secret_entry.get().strip(),
                    "CHECK_INTERVAL_MIN": str(minutes),
                },
            )
            config.reload_dotenv()
            self.interval = minutes
            win.destroy()
            APP_LOG.info("设置已保存：账号 %s***，渠道 %s，间隔 %d 分钟", username[:3], channel.get(), minutes)

            if self.running:
                self.pause()
                self.start()
            else:
                self.start()

        button_row = ttk.Frame(frame)
        button_row.grid(row=offset + 7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(button_row, text="保存并开始", command=save).pack(side="right")
        ttk.Button(button_row, text="取消", command=win.destroy).pack(side="right", padx=6)

        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_x() + 60}+{self.root.winfo_y() + 60}")

    # ---------- 其他 ----------

    def open_logs(self) -> None:
        try:
            os.startfile(config.PROJECT_ROOT / "logs")  # noqa: S606
        except OSError as exc:
            messagebox.showinfo(APP_TITLE, f"打开失败：{exc}", parent=self.root)

    def on_close(self) -> None:
        if self.closed:
            return
        self.running = False
        self.closed = True
        self.stop_event.set()
        APP_LOG.info("窗口关闭，监测停止")
        try:
            self.root.after(120, self.root.destroy)
        except tk.TclError:
            pass


def _set_dpi_awareness() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    _ensure_std_streams()
    _set_dpi_awareness()
    root = tk.Tk()
    try:
        DesktopApp(root)
        root.mainloop()
    except Exception:  # noqa: BLE001
        crash = config.PROJECT_ROOT / "logs" / "crash.log"
        crash.parent.mkdir(parents=True, exist_ok=True)
        crash.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror(APP_TITLE, f"启动失败，详情见 {crash}")
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

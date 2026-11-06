"""桌面版入口：打开就开始监测，关掉窗口就停止。

双击 exe（或 `python app.py`）后：
  - 立即检查一次成绩；
  - 之后按设定的间隔（默认 10 分钟）自动检查；
  - 窗口关闭 → 定时线程结束、进程退出，不再有任何后台活动。

界面两页：「已出科目」按学期列全部已出分科目（新增的置顶），「运行日志」看实时过程。
通知走 Windows 本地通知，可选强提醒（置顶弹窗 + 任务栏闪烁 + 提示音）。
配置写在 exe 同目录的 config\\.env 里，首次打开会自动弹出设置窗口。
"""

from __future__ import annotations

import ctypes
import json
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

try:
    import winsound
except ImportError:  # 非 Windows
    winsound = None  # type: ignore[assignment]

APP_TITLE = "法大成绩提醒"
APP_LOG = logging.getLogger("app")
ENV_ORDER = (
    "CUPL_USERNAME",
    "CUPL_PASSWORD",
    "AUTH_MODE",
    "NOTIFY_CHANNEL",
    "NOTIFY_STRONG",
    "CHECK_INTERVAL_MIN",
)
FONT = ("Microsoft YaHei UI", 9)
CACHE_NAME = "grades_cache.json"
COLOR_OK = "#137333"
COLOR_BAD = "#a8071a"
COLOR_MUTED = "#666666"


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


def _beep() -> None:
    if winsound is None:
        return
    try:
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except RuntimeError:
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except RuntimeError:
            pass


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
        self.cache_path = config.PROJECT_ROOT / "state" / CACHE_NAME

        self.busy = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.closed = False
        self._strong_win: tk.Toplevel | None = None
        self._log_lines = 0

        env = _read_env(self.env_path)
        self.interval = self._parse_interval(env.get("CHECK_INTERVAL_MIN", "10"))
        self.strong = env.get("NOTIFY_STRONG", "1").strip().lower() not in {"0", "false", "no", "off"}

        # 成绩表：rows 里每项 {term, course, new}
        self.rows: list[dict] = []
        self.session_new: list[str] = []
        self.cache_loaded_at = ""

        self.log_queue: queue.Queue[str] = queue.Queue()
        self._setup_logging()
        self._build_ui()
        self._poll_log()
        self._load_cache()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self._boot)

    # ---------- 基础设施 ----------

    @staticmethod
    def _parse_interval(raw: str) -> int:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
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
        self.root.geometry("780x600")
        self.root.minsize(700, 520)
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
        ttk.Label(
            status_box, textvariable=self.hint_var, foreground=COLOR_MUTED, anchor="w"
        ).pack(fill="x", padx=10, pady=(0, 8))

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="立即检查", command=self.manual_check).pack(side="left")
        self.toggle_button = ttk.Button(buttons, text="暂停监测", command=self.toggle_run)
        self.toggle_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="设置…", command=self.open_settings).pack(side="left")
        ttk.Button(buttons, text="打开日志", command=self.open_logs).pack(side="left", padx=6)
        ttk.Button(buttons, text="退出", command=self.on_close).pack(side="right")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self._build_grades_tab()
        self._build_log_tab()

    def _build_grades_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="已出科目")

        head = ttk.Frame(tab)
        head.pack(fill="x")
        self.grades_stat_var = tk.StringVar(value="还没有数据，点「立即检查」抓一次。")
        ttk.Label(head, textvariable=self.grades_stat_var, foreground=COLOR_MUTED).pack(side="left")
        ttk.Button(head, text="刷新列表", command=self.manual_check).pack(side="right")

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True, pady=(6, 0))

        columns = ("term", "course", "flag")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("term", text="学期")
        self.tree.heading("course", text="科目")
        self.tree.heading("flag", text="新增")
        self.tree.column("term", width=120, anchor="w", stretch=False)
        self.tree.column("course", width=470, anchor="w")
        self.tree.column("flag", width=60, anchor="center", stretch=False)
        self.tree.tag_configure("new", background="#fff3bf", font=("Microsoft YaHei UI", 9, "bold"))
        self.tree.tag_configure("cached", foreground="#888888")

        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_log_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="运行日志")
        self.log_text = tk.Text(
            tab, height=14, wrap="none", state="disabled", bg="#111111", fg="#dddddd"
        )
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ---------- 成绩表 ----------

    def _render_rows(self) -> None:
        if self.closed:
            return
        try:
            self.tree.delete(*self.tree.get_children())
            for row in self.rows:
                tags: tuple[str, ...] = ()
                if row.get("cached"):
                    tags = ("cached",)
                elif row.get("new"):
                    tags = ("new",)
                self.tree.insert(
                    "",
                    "end",
                    values=(row.get("term") or "—", row.get("course", ""), "新增" if row.get("new") else ""),
                    tags=tags,
                )
        except tk.TclError:
            return

        terms = {row.get("term") for row in self.rows if row.get("term")}
        new_count = sum(1 for row in self.rows if row.get("new"))
        if not self.rows:
            self.grades_stat_var.set("还没有数据，点「立即检查」抓一次。")
            return
        text = f"共 {len(self.rows)} 门 · {len(terms)} 个学期"
        if new_count:
            text += f" · 本次新增 {new_count} 门（置顶）"
        if self.rows and self.rows[0].get("cached"):
            text = f"上次抓取 {self.cache_loaded_at} · " + text
        self.grades_stat_var.set(text)

    def _apply_grades(self, grades: list, new_grades: list) -> None:
        """新增的置顶（按出现顺序），其余按学期倒序。"""
        for item in new_grades:
            if item.key not in self.session_new:
                self.session_new.append(item.key)

        index = {item.key: item for item in grades}
        rows: list[dict] = []
        for key in self.session_new:
            item = index.get(key)
            if item is not None:
                rows.append({"term": item.term, "course": item.course_name, "new": True})

        rest = [item for item in grades if item.key not in set(self.session_new)]
        rest.sort(key=lambda item: item.term, reverse=True)
        rows.extend({"term": item.term, "course": item.course_name, "new": False} for item in rest)

        self.rows = rows
        self._save_cache(grades)
        self._safe_after(self._render_rows)

    def _save_cache(self, grades: list) -> None:
        """只缓存学期与科目名，不存分数。"""
        payload = {
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "items": [{"term": item.term, "course": item.course_name} for item in grades],
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            APP_LOG.warning("成绩列表缓存写入失败：%s", exc)

    def _load_cache(self) -> None:
        if not self.cache_path.is_file():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        items = data.get("items") or []
        if not items:
            return
        rows = [
            {"term": str(item.get("term", "")), "course": str(item.get("course", "")), "new": False, "cached": True}
            for item in items
            if item.get("course")
        ]
        rows.sort(key=lambda row: row["term"], reverse=True)
        self.rows = rows
        self.cache_loaded_at = str(data.get("saved_at", ""))[:16].replace("T", " ")
        self._render_rows()

    # ---------- 启动 ----------

    def _boot(self) -> None:
        env = _read_env(self.env_path)
        if not env.get("CUPL_USERNAME"):
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
        self._set_status(f"● 监测中（每 {self.interval} 分钟）", COLOR_OK)
        self.toggle_button.configure(text="暂停监测")
        self.hint_var.set("窗口开着才会监测，关掉即停。" + ("强提醒已开启。" if self.strong else ""))
        APP_LOG.info("开始监测，间隔 %d 分钟，强提醒=%s", self.interval, "开" if self.strong else "关")

    def pause(self) -> None:
        if not self.running:
            return
        self.running = False
        self.stop_event.set()
        self._set_status("● 已暂停", COLOR_BAD)
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
                self._after_status("未配置", "请点「设置…」填写学号与密码", COLOR_BAD)
                return

            notifier = (
                build_notifier(
                    settings.channel,
                    settings.webhook_url,
                    settings.webhook_secret,
                    strong=settings.strong_alert,
                    notice_hook=self._strong_notice if settings.strong_alert else None,
                )
                if settings.notify_enabled
                else NullNotifier()
            )
            result, code = monitor.run_checked(settings, notifier)
            if result is None:
                summary = "检查失败，原因见日志"
                self._after_status(
                    self._current_status_text(), f"上次检查：{self._now()} · {summary}", COLOR_BAD, detail_only=True
                )
            else:
                summary = self._summarize(result)
                self._apply_grades(result["grades"], result["new_grades"])
                self._after_status(
                    self._current_status_text(), f"上次检查：{self._now()} · {summary}", None, detail_only=True
                )
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
            names = "、".join(item.course_name for item in new_grades)
            return f"新增 {len(new_grades)} 门：{names}"
        return f"无新增（共 {result['total']} 门）"

    def _log_run(self, result: dict | None, summary: str, code: int, elapsed: float) -> None:
        entry = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "desktop",
            "ok": code == 0 and result is not None,
            "total": (result or {}).get("total", 0),
            "new": [item.course_name for item in (result or {}).get("new_grades", [])],
            "baseline_created": (result or {}).get("baseline_created", False),
            "elapsed": round(elapsed, 1),
        }
        if result is None:
            entry["error"] = "见 logs\\app.log"
        try:
            runlog.append(entry)
        except OSError:
            pass

    # ---------- 强提醒 ----------

    def _strong_notice(self, title: str, body: str) -> None:
        """从监测线程回调：切回 UI 线程弹窗。"""
        self._safe_after(lambda: self._show_strong(title, body))

    def _show_strong(self, title: str, body: str) -> None:
        if self.closed:
            return
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            return

        self._close_strong()
        win = tk.Toplevel(self.root)
        win.title(title)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(padx=22, pady=18)
        win.option_add("*Font", FONT)

        tk.Label(win, text=title, font=("Microsoft YaHei UI", 15, "bold"), fg=COLOR_BAD).pack(anchor="w")
        tk.Label(win, text=body, justify="left", wraplength=440, font=("Microsoft YaHei UI", 10)).pack(
            anchor="w", pady=(10, 16)
        )
        tk.Button(win, text="知道了", width=10, command=self._close_strong).pack(anchor="e")
        win.protocol("WM_DELETE_WINDOW", self._close_strong)

        win.update_idletasks()
        width, height = win.winfo_width(), win.winfo_height()
        x = max(0, (win.winfo_screenwidth() - width) // 2)
        y = max(0, (win.winfo_screenheight() - height) // 3)
        win.geometry(f"+{x}+{y}")
        win.lift()
        try:
            win.focus_force()
        except tk.TclError:
            pass

        self._strong_win = win
        self._flash_taskbar()
        _beep()
        APP_LOG.info("强提醒：%s / %s", title, body)

    def _close_strong(self) -> None:
        win = self._strong_win
        self._strong_win = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _flash_taskbar(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())  # type: ignore[attr-defined]
            if not hwnd:
                hwnd = self.root.winfo_id()

            class _FlashInfo(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint),
                ]

            flags = 0x00000003 | 0x0000000C  # FLASHW_ALL | FLASHW_TIMERNOFG
            info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, flags, 6, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
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

    def _after_status(self, text: str, detail: str, color: str | None, detail_only: bool = False) -> None:
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
        channel = values.get("NOTIFY_CHANNEL", "desktop").strip().lower()
        if channel in {"dingtalk", "feishu", "钉钉", "飞书"}:
            channel = "desktop"  # 旧配置一律改成本机通知

        win = tk.Toplevel(self.root)
        win.title("设置")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        win.option_add("*Font", FONT)

        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)

        if first_run:
            ttk.Label(
                frame, text="第一次使用，先填学号与统一认证密码。", foreground=COLOR_BAD
            ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        offset = 1 if first_run else 0

        def add_row(row: int, label: str, show: str | None = None) -> ttk.Entry:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, width=38, show=show or "")
            entry.grid(row=row, column=1, sticky="we", pady=4)
            return entry

        user_entry = add_row(offset + 0, "学号")
        user_entry.insert(0, values.get("CUPL_USERNAME", ""))
        pass_entry = add_row(offset + 1, "统一认证密码", show="*")
        pass_entry.insert(0, values.get("CUPL_PASSWORD", ""))

        ttk.Label(frame, text="通知方式").grid(row=offset + 2, column=0, sticky="w", pady=4)
        notify_box = ttk.Combobox(
            frame, values=("电脑通知", "不通知"), state="readonly", width=36
        )
        notify_box.set("不通知" if channel == "none" else "电脑通知")
        notify_box.grid(row=offset + 2, column=1, sticky="we", pady=4)

        strong_var = tk.BooleanVar(value=self.strong)
        strong_check = ttk.Checkbutton(
            frame, text="强提醒（置顶弹窗 + 闪烁任务栏 + 提示音）", variable=strong_var
        )
        strong_check.grid(row=offset + 3, column=0, columnspan=2, sticky="w", pady=(6, 2))

        ttk.Label(frame, text="检查间隔（分钟）").grid(row=offset + 4, column=0, sticky="w", pady=4)
        interval = tk.Spinbox(frame, from_=3, to=120, width=36, font=FONT)
        interval.delete(0, "end")
        interval.insert(0, str(self.interval))
        interval.grid(row=offset + 4, column=1, sticky="we", pady=4)

        ttk.Label(
            frame,
            text="密码只写入本机 config\\.env，不上传任何地方；通知走系统通知，无需机器人 Webhook。",
            foreground=COLOR_MUTED,
        ).grid(row=offset + 5, column=0, columnspan=2, sticky="w", pady=(8, 0))

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

            _write_env(
                self.env_path,
                {
                    "CUPL_USERNAME": username,
                    "CUPL_PASSWORD": password,
                    "AUTH_MODE": "tpass",
                    "NOTIFY_CHANNEL": "none" if notify_box.get() == "不通知" else "desktop",
                    "NOTIFY_STRONG": "1" if strong_var.get() else "0",
                    "CHECK_INTERVAL_MIN": str(minutes),
                },
            )
            config.reload_dotenv()
            self.interval = minutes
            self.strong = bool(strong_var.get())
            win.destroy()
            APP_LOG.info(
                "设置已保存：账号 %s***，通知=%s，强提醒=%s，间隔 %d 分钟",
                username[:3],
                notify_box.get(),
                "开" if self.strong else "关",
                minutes,
            )

            if self.running:
                self.pause()
            self.start()

        button_row = ttk.Frame(frame)
        button_row.grid(row=offset + 6, column=0, columnspan=2, sticky="e", pady=(12, 0))
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
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
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

"""统一配置加载。

优先级：真实环境变量 > 项目根目录 .env > 默认值。
本机与 GitHub Actions 共用同一套键名，区别只在于值从哪来：
  - 本机：config/.env（已 gitignore）或 Windows 凭据管理器注入的环境变量
  - Actions：仓库 Secrets
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    """源码运行时是项目根目录；PyInstaller 打包后是 exe 所在目录。

    这样 exe 旁边的 config\\.env、state\\、logs\\ 才能被正确读写，
    而不是跑到 _MEIPASS 临时目录里。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()
DEFAULT_STATE_PATH = PROJECT_ROOT / "state" / "grades.json"

BASE_URL = "http://jwxt.cupl.edu.cn"

# 已知的本地登录成功后可尝试的成绩页候选（真实地址待登录后确认）
GRADE_URL_CANDIDATES = (
    "/eams/teach/grade/course/person!search.action",
    "/eams/teach/grade/course/person!historyCourseGrade.action",
    "/eams/teach/grade/course/person!report.action",
)

_CHANNEL_ALIASES = {
    "dingtalk": "dingtalk",
    "钉钉": "dingtalk",
    "feishu": "feishu",
    "lark": "feishu",
    "飞书": "feishu",
    "none": "none",
    "": "none",
}


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析，避免为一个文件引入额外依赖。不覆盖已存在的环境变量。"""
    if not path.is_file():
        return
    # utf-8-sig 兼容 Windows PowerShell 写出的带 BOM 文件
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def reload_dotenv(env_file: Path | None = None) -> None:
    """强制用 .env 覆盖进程环境变量。

    界面里改完配置需要立刻生效，而 _load_dotenv 的 setdefault 语义会让旧值残留。
    """
    path = env_file or (PROJECT_ROOT / "config" / ".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    channel: str
    webhook_url: str
    webhook_secret: str
    base_url: str
    grade_url: str
    state_path: Path
    timeout: int
    auth_mode: str
    dry_run: bool
    keep_baseline: bool

    @property
    def notify_enabled(self) -> bool:
        return self.channel != "none" and bool(self.webhook_url)

    def safe_summary(self) -> str:
        """可安全写日志的配置摘要，不包含任何凭据。"""
        return (
            f"user={self.username[:3]}*** auth={self.auth_mode} channel={self.channel} "
            f"notify={'on' if self.notify_enabled else 'off'} "
            f"grade_url={self.grade_url or '(auto)'} state={self.state_path.name} "
            f"dry_run={self.dry_run}"
        )


def load_settings(env_file: Path | None = None, dry_run_override: bool | None = None) -> Settings:
    _load_dotenv(env_file or (PROJECT_ROOT / "config" / ".env"))

    username = os.environ.get("CUPL_USERNAME", "").strip()
    password = os.environ.get("CUPL_PASSWORD", "")
    if not username or not password:
        raise ConfigError("缺少 CUPL_USERNAME / CUPL_PASSWORD，请先完成本机或 Secrets 配置")

    raw_channel = os.environ.get("NOTIFY_CHANNEL", "dingtalk").strip().lower()
    channel = _CHANNEL_ALIASES.get(raw_channel)
    if channel is None:
        raise ConfigError(f"不支持的 NOTIFY_CHANNEL={raw_channel!r}，可选 dingtalk / feishu / none")

    webhook_url = os.environ.get("NOTIFY_WEBHOOK", "").strip()
    webhook_secret = os.environ.get("NOTIFY_SECRET", "").strip()
    dry_run = env_bool("DRY_RUN") if dry_run_override is None else dry_run_override
    if channel != "none" and not webhook_url and not dry_run:
        raise ConfigError("已启用通知渠道但缺少 NOTIFY_WEBHOOK")

    state_raw = os.environ.get("STATE_PATH", "").strip()
    state_path = Path(state_raw) if state_raw else DEFAULT_STATE_PATH
    if not state_path.is_absolute():
        state_path = (PROJECT_ROOT / state_path).resolve()

    try:
        timeout = int(os.environ.get("HTTP_TIMEOUT", "25"))
    except ValueError as exc:
        raise ConfigError("HTTP_TIMEOUT 必须是整数") from exc

    base_url = os.environ.get("CUPL_BASE_URL", BASE_URL).rstrip("/")
    grade_url = os.environ.get("CUPL_GRADE_URL", "").strip()

    auth_mode = os.environ.get("AUTH_MODE", "tpass").strip().lower()
    if auth_mode not in {"tpass", "local"}:
        raise ConfigError("AUTH_MODE 只能是 tpass（统一认证）或 local（教务本地账号）")

    return Settings(
        username=username,
        password=password,
        channel=channel,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        base_url=base_url,
        grade_url=grade_url,
        state_path=state_path,
        timeout=max(5, timeout),
        auth_mode=auth_mode,
        dry_run=dry_run,
        keep_baseline=env_bool("KEEP_BASELINE"),
    )

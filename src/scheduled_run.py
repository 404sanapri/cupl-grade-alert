"""无界面入口：给 Windows 计划任务 / 云函数用，跑一次并把结果写进运行记录。

与 `monitor.py` 的区别：
  - `monitor.py` 只打印日志，适合手工执行与 Actions；
  - `scheduled_run.py` 会把这一次运行追加到 `logs/runs.jsonl`，
    于是在网页界面的「运行记录」里也能看到计划任务跑过的痕迹。

用法：
  python src\\scheduled_run.py              正常执行一次
  python src\\scheduled_run.py --dry-run    只验证登录与解析
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import monitor  # noqa: E402
import runlog  # noqa: E402
from notify import NullNotifier, build_notifier  # noqa: E402

log = logging.getLogger("scheduled")

LOCK_PATH = runlog.RUN_LOG.parent / ".run.lock"
LOCK_STALE_SECONDS = 10 * 60


def _acquire_lock() -> bool:
    """同一时刻只允许一个进程跑，避免计划任务与界面重叠导致重复推送。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            return False
        log.warning("发现 %.0f 秒前的锁文件，视为残留并接管", age)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(datetime.now().astimezone().isoformat(timespec="seconds"))
    return True


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="法大成绩提醒：无界面运行一次")
    parser.add_argument("--dry-run", action="store_true", help="只验证登录与解析，不推送不写快照")
    parser.add_argument("--source", default="task", help="记录里的来源标记，默认 task")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if not _acquire_lock():
        log.warning("上一次运行还没结束，本次跳过")
        return 0

    started = time.time()
    entry: dict = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": args.source,
    }
    code = 0
    try:
        config.reload_dotenv()
        settings = config.load_settings(dry_run_override=True if args.dry_run else None)
        notifier = (
            build_notifier(
                settings.channel,
                settings.webhook_url,
                settings.webhook_secret,
                strong=settings.strong_alert,
            )
            if settings.notify_enabled and not args.dry_run
            else NullNotifier()
        )
        result = monitor.run_once(settings, notifier, dry_run=args.dry_run)
        entry.update(
            {
                "ok": True,
                "total": result["total"],
                "new": [g.course_name for g in result["new_grades"]],
                "baseline_created": result["baseline_created"],
            }
        )
        log.info("完成：共 %d 条，新增 %d 条", result["total"], len(result["new_grades"]))
    except Exception as exc:  # noqa: BLE001
        log.error("运行失败：%s", exc)
        entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        code = 1
    finally:
        entry["elapsed"] = round(time.time() - started, 1)
        try:
            runlog.append(entry)
        except OSError as exc:
            log.warning("运行记录写入失败：%s", exc)
        _release_lock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

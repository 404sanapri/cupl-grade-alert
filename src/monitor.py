"""编排入口：登录 → 抓成绩 → 解析 → 增量比对 → 推送 → 回写快照。

用法：
  python src/monitor.py              正常执行一次
  python src/monitor.py --dry-run    只验证登录与解析，不推送、不写快照
  python src/monitor.py --test-notify  只发一条测试消息
  python src/monitor.py --reset-state  清空本地快照
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import grade  # noqa: E402
import state  # noqa: E402
from eams_client import (  # noqa: E402
    CaptchaRequiredError,
    CredentialError,
    EamsClient,
    EamsError,
    PageChangedError,
    RateLimitedError,
    SessionExpiredError,
)
from notify import NullNotifier, build_notifier  # noqa: E402

log = logging.getLogger("monitor")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _should_alert(exc: EamsError, failures: int) -> bool:
    # 需要人工处理的问题立刻告警；可自愈的问题连续失败才告警，避免刷屏
    if isinstance(exc, (CredentialError, CaptchaRequiredError, PageChangedError)):
        return True
    if isinstance(exc, (RateLimitedError, SessionExpiredError)):
        return failures >= 2
    return failures >= 2


def run_once(settings: config.Settings, notifier, dry_run: bool = False) -> dict:
    """执行一次完整检查，返回结构化结果；异常直接抛给调用方。"""
    client = EamsClient(
        settings.username,
        settings.password,
        base_url=settings.base_url,
        timeout=settings.timeout,
        auth_mode=settings.auth_mode,
    )
    client.login()
    html = client.fetch_grades_html(settings.grade_url)
    grades = grade.parse_grades(html)
    log.info("解析到 %d 条成绩", len(grades))

    snapshot = state.load(settings.state_path)
    baseline_ready = bool(snapshot.get("baseline_ready"))
    new_grades = state.diff(snapshot, grades)

    if not baseline_ready:
        log.info("首次运行：建立基线 %d 条，本次不推送", len(grades))
    elif new_grades:
        log.info("发现新增 %d 门：%s", len(new_grades), ", ".join(g.course_name for g in new_grades))
        if dry_run:
            log.info("[dry-run] 跳过推送")
        else:
            notifier.send_new_grades(new_grades)
    else:
        log.info("无新增成绩")

    if dry_run:
        log.info("[dry-run] 不写快照")
    else:
        snapshot.pop("consecutive_failures", None)
        snapshot.pop("last_error", None)
        updated = state.update(snapshot, grades)
        state.save(settings.state_path, updated)
        log.info("快照已更新：%s（%d 条）", settings.state_path.name, len(updated["keys"]))

    return {
        "total": len(grades),
        "grades": grades,
        "new_grades": new_grades,
        "baseline_created": not baseline_ready,
        "saved": not dry_run,
    }


def run_checked(settings: config.Settings, notifier, dry_run: bool = False) -> tuple[dict | None, int]:
    """跑一次，失败也按既定策略处理（记失败次数、必要时告警）。

    返回 (结果, 退出码)：成功时结果非空、退出码 0；失败时结果为 None。
    桌面程序（app.py）用它，可以同时拿到结果详情与统一的失败处理。
    """
    try:
        return run_once(settings, notifier, dry_run=dry_run), 0
    except EamsError as exc:
        return None, _handle_failure(settings, notifier, exc)
    except grade.GradeParseError as exc:
        return None, _handle_failure(settings, notifier, PageChangedError(str(exc)))
    except state.StateError as exc:
        return None, _handle_failure(settings, notifier, PageChangedError(str(exc)))
    except Exception as exc:  # noqa: BLE001
        log.exception("未预期异常")
        return None, _handle_failure(settings, notifier, EamsError(f"{type(exc).__name__}: {exc}"))


def run(dry_run: bool) -> int:
    settings = config.load_settings(dry_run_override=dry_run)
    log.info("配置：%s", settings.safe_summary())

    notifier = (
        NullNotifier()
        if (dry_run or not settings.notify_enabled)
        else build_notifier(
            settings.channel,
            settings.webhook_url,
            settings.webhook_secret,
            strong=settings.strong_alert,
        )
    )

    _, code = run_checked(settings, notifier, dry_run=dry_run)
    return code


def _handle_failure(settings: config.Settings, notifier, exc: EamsError) -> int:
    log.error("监控失败：%s", exc)
    try:
        snapshot = state.load(settings.state_path)
    except state.StateError:
        snapshot = {}
    failures = int(snapshot.get("consecutive_failures", 0)) + 1
    snapshot["consecutive_failures"] = failures
    snapshot["last_error"] = type(exc).__name__

    if settings.dry_run:
        log.info("[dry-run] 不写失败状态、不发告警")
        return 1

    try:
        state.save(settings.state_path, snapshot)
    except OSError:
        log.warning("失败状态写入不成功，忽略")

    if _should_alert(exc, failures):
        notifier.send_alert(f"{exc}\n\n已连续失败 {failures} 次")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="中国政法大学教务成绩更新提醒")
    parser.add_argument("--dry-run", action="store_true", help="只验证登录与解析，不推送不写快照")
    parser.add_argument("--test-notify", action="store_true", help="发送一条测试通知")
    parser.add_argument("--reset-state", action="store_true", help="清空本地成绩快照")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    dry_run = args.dry_run or config.env_bool("DRY_RUN")

    if args.reset_state:
        settings = config.load_settings()
        state.reset(settings.state_path)
        log.info("已清空快照：%s", settings.state_path)
        return 0

    if args.test_notify:
        settings = config.load_settings()
        if not settings.notify_enabled:
            log.error("未配置通知渠道，无法测试")
            return 2
        notifier = build_notifier(settings.channel, settings.webhook_url, settings.webhook_secret)
        notifier.send_alert("这是一条测试消息，收到说明通知渠道配置正确。")
        log.info("测试消息已发送（若未收到请检查加签/关键词设置）")
        return 0

    return run(dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

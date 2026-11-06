"""运行记录的读写：logs/runs.jsonl，一行一次运行。

网页界面与 Windows 计划任务写同一份文件，所以无论从哪边触发，
「运行记录」里都能看到同一条历史。
"""

from __future__ import annotations

import json
from pathlib import Path

from config import PROJECT_ROOT

RUN_LOG = PROJECT_ROOT / "logs" / "runs.jsonl"


def append(entry: dict, path: Path | None = None) -> None:
    target = path or RUN_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read(limit: int = 30, path: Path | None = None) -> list[dict]:
    """返回最近的 limit 条记录，最新的在前。"""
    target = path or RUN_LOG
    if not target.is_file():
        return []
    lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    runs: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(runs))

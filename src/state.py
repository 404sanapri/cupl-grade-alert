"""成绩快照读写与增量比对。

设计要点：
  - 快照只保存「课程标识 + 学期」的稳定哈希键，不保存分数，降低泄漏面；
  - 首次运行只建立基线，绝不推送，避免一次性轰炸全部历史成绩；
  - 当前解析结果为空但历史基线非空时，视为异常（多半是页面改版），不清空基线。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from grade import CourseGrade

SNAPSHOT_VERSION = 1


class StateError(RuntimeError):
    """快照文件损坏或结构异常。"""


def _empty() -> dict:
    return {
        "version": SNAPSHOT_VERSION,
        "baseline_ready": False,
        "keys": [],
        "updated_at": None,
    }


def load(path: Path) -> dict:
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"快照文件不是合法 JSON：{path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("keys", []), list):
        raise StateError(f"快照结构异常：{path}")
    merged = _empty()
    merged.update(
        {
            "version": int(data.get("version", SNAPSHOT_VERSION)),
            "baseline_ready": bool(data.get("baseline_ready", False)),
            "keys": [str(k) for k in data.get("keys", [])],
            "updated_at": data.get("updated_at"),
        }
    )
    # 保留失败计数等运行期字段，用于「连续失败才告警」的判断
    for extra in ("consecutive_failures", "last_error"):
        if extra in data:
            merged[extra] = data[extra]
    return merged


def save(path: Path, snapshot: dict) -> None:
    """原子写：先写临时文件再替换，避免 CI 中途失败留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".grades-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def diff(snapshot: dict, current: list[CourseGrade]) -> list[CourseGrade]:
    """返回本次新增的科目。首次运行（尚无基线）返回空列表。"""
    if not snapshot.get("baseline_ready"):
        return []
    known = set(snapshot.get("keys", []))
    return [g for g in current if g.key not in known]


def update(snapshot: dict, current: list[CourseGrade]) -> dict:
    """把当前成绩并入快照。"""
    result = dict(snapshot)
    result["version"] = SNAPSHOT_VERSION
    result["keys"] = sorted({g.key for g in current})
    result["baseline_ready"] = True
    result["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return result


def reset(path: Path) -> None:
    """清空快照（合规要求：用户可一键删除自己的比对数据）。"""
    if path.exists():
        path.unlink()

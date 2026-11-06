"""L0 零凭据公告层：抓教务处公开公告，一次抓取、多方广播。

设计取舍：
  - 不接触任何账号密码，合规风险最低；
  - 抓取量 O(1)，与订阅人数无关，适合「扫码进群就能用」的传播场景；
  - 代价是只能提示「发布了成绩相关公告」，无法精确到个人科目——那是 L1 的职责。

教务处首页有 JS 挑战（window.challengeId + POST /dynamic_challenge），
这里复用既有爬虫的解法：先答题拿 cookie，再取页面。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import urllib3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify  # noqa: E402
import state  # noqa: E402

urllib3.disable_warnings()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = PROJECT_ROOT / "state" / "announcements.json"
SOURCES = ("https://jwc.cupl.edu.cn/",)
DEFAULT_KEYWORDS = ("成绩", "分数")
MAX_KEYS = 800

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")
_LINK_RE = re.compile(r"\.(?:htm|html|shtml)$|/info/", re.I)
# 教务处的日期前缀形如 "06 2026-11"（天 + 年月），会混进链接文本里
_DATE_PREFIX_RE = re.compile(
    r"^\s*(?:\d{1,2}\s+\d{4}[-.]\d{1,2}|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}\s*日?)\s*"
)
_DATE_SUFFIX_RE = re.compile(r"\s+\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*$")

log = logging.getLogger("announcement")


@dataclass(frozen=True)
class Announcement:
    key: str
    title: str
    url: str
    date: str


def _base_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _solve_challenge(session: requests.Session, url: str) -> None:
    resp = session.get(url, timeout=25, verify=False)
    if "challengeId" not in resp.text:
        return
    challenge = re.search(r'challengeId\s*=\s*"([^"]+)"', resp.text)
    answer = re.search(r"answer\s*=\s*(\d+)", resp.text)
    if not challenge:
        return
    session.post(
        _base_of(url) + "/dynamic_challenge",
        json={
            "challenge_id": challenge.group(1),
            "answer": int(answer.group(1)) if answer else 5,
            "browser_info": {
                "userAgent": UA,
                "language": "zh-CN",
                "platform": "Win32",
                "cookieEnabled": True,
                "hardwareConcurrency": 8,
                "deviceMemory": 8,
                "timezone": "Asia/Shanghai",
            },
        },
        timeout=25,
        verify=False,
    )


def fetch_html(session: requests.Session, url: str) -> str:
    _solve_challenge(session, url)
    resp = session.get(url, timeout=25, verify=False)
    if "challengeId" in resp.text:
        _solve_challenge(session, url)
        resp = session.get(url, timeout=25, verify=False)
    resp.encoding = "utf-8"
    return resp.text


def _clean_title(text: str) -> str:
    return _DATE_SUFFIX_RE.sub("", _DATE_PREFIX_RE.sub("", text)).strip()


def _extract_date(tag) -> str:
    own_text = tag.get_text(" ", strip=True)
    container = tag.find_parent(["li", "tr"]) or tag.parent
    text = own_text if _DATE_RE.search(own_text) else (container.get_text(" ", strip=True) if container else "")
    matches = list(_DATE_RE.finditer(text))
    if not matches:
        return ""
    match = matches[-1]  # 发布日期通常在标题之后，取最后一个
    year, month, day = (int(g) for g in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_announcements(html: str, base_url: str) -> list[Announcement]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    items: list[Announcement] = []
    for anchor in soup.find_all("a", href=True):
        title = _clean_title(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        if len(title) < 6 or href.startswith(("javascript:", "#", "mailto:")):
            continue
        if not _LINK_RE.search(href):
            continue
        full_url = urljoin(base_url, href)
        date = _extract_date(anchor)
        # 同一公告会出现在多个栏目，用「日期 + 标题」去重，避免重复广播
        key = hashlib.sha1(f"{date}|{title}".encode("utf-8")).hexdigest()[:16]
        if key in seen:
            continue
        seen.add(key)
        items.append(Announcement(key=key, title=title, url=full_url, date=date))
    return items


def filter_by_keywords(items: list[Announcement], keywords: tuple[str, ...]) -> list[Announcement]:
    return [item for item in items if any(word in item.title for word in keywords)]


def format_message(items: list[Announcement]) -> str:
    lines = [f"### 教务处新公告（{len(items)} 条）", ""]
    for item in items[:10]:
        suffix = f"（{item.date}）" if item.date else ""
        lines.append(f"- {item.title}{suffix}")
        lines.append(f"  {item.url}")
    if len(items) > 10:
        lines.append(f"- 另有 {len(items) - 10} 条未列出")
    lines.append("")
    lines.append("提示：这是公开公告，不代表个人成绩已出。")
    return "\n".join(lines)


def load_subscribers() -> list[dict]:
    raw = os.environ.get("ANNOUNCE_SUBSCRIBERS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("ANNOUNCE_SUBSCRIBERS 不是合法 JSON") from exc
    if not isinstance(data, list):
        raise SystemExit("ANNOUNCE_SUBSCRIBERS 必须是数组")
    result = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        result.append(
            {
                "channel": str(entry.get("channel", "dingtalk")).lower(),
                "url": str(entry["url"]),
                "secret": str(entry.get("secret", "")),
            }
        )
    return result


def _load_snapshot(path: Path) -> dict:
    snapshot = {"version": 1, "baseline_ready": False, "keys": [], "updated_at": None}
    if not path.is_file():
        return snapshot
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("公告快照损坏，按首次运行处理")
        return snapshot
    if isinstance(data, dict):
        snapshot.update(
            {
                "baseline_ready": bool(data.get("baseline_ready", False)),
                "keys": [str(k) for k in data.get("keys", [])],
                "updated_at": data.get("updated_at"),
            }
        )
    return snapshot


def run(dry_run: bool) -> int:
    keywords = tuple(
        word.strip()
        for word in os.environ.get("ANNOUNCE_KEYWORDS", ",".join(DEFAULT_KEYWORDS)).split(",")
        if word.strip()
    )
    state_path = Path(os.environ.get("ANNOUNCE_STATE_PATH", str(DEFAULT_STATE_PATH)))
    subscribers = load_subscribers()

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

    all_items: list[Announcement] = []
    for url in SOURCES:
        try:
            html = fetch_html(session, url)
        except requests.RequestException as exc:
            log.error("抓取失败 %s：%s", url, type(exc).__name__)
            return 1
        items = parse_announcements(html, url)
        log.info("%s 解析到 %d 条链接", url, len(items))
        all_items.extend(items)

    matched = filter_by_keywords(all_items, keywords)
    log.info("关键词 %s 命中 %d 条", "/".join(keywords), len(matched))

    snapshot = _load_snapshot(state_path)
    known = set(snapshot.get("keys", []))
    new_items = [] if not snapshot.get("baseline_ready") else [i for i in matched if i.key not in known]

    if not snapshot.get("baseline_ready"):
        log.info("首次运行：建立基线 %d 条，不广播", len(matched))
    elif new_items:
        log.info("新增公告 %d 条", len(new_items))
        if dry_run:
            log.info("[dry-run] 跳过广播")
        else:
            text = format_message(new_items)
            if not subscribers:
                log.warning("未配置 ANNOUNCE_SUBSCRIBERS，仅打印内容")
                log.info(text)
            for subscriber in subscribers:
                notify.send_text(
                    subscriber["channel"],
                    subscriber["url"],
                    subscriber["secret"],
                    text,
                    "教务处新公告",
                )
    else:
        log.info("无新公告")

    if dry_run:
        return 0

    merged = sorted(known | {item.key for item in matched})[-MAX_KEYS:]
    snapshot.update(
        {
            "version": 1,
            "baseline_ready": True,
            "keys": merged,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    state.save(state_path, snapshot)
    log.info("公告快照已更新：%d 条", len(merged))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="教务处公开公告监控（零凭据）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取与解析，不广播不写快照")
    parser.add_argument("--reset-state", action="store_true", help="清空公告快照")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if args.reset_state:
        state.reset(Path(os.environ.get("ANNOUNCE_STATE_PATH", str(DEFAULT_STATE_PATH))))
        log.info("公告快照已清空")
        return 0

    return run(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 0 接口探测脚本（开发期使用，不进入生产依赖）。

用途：
  1. 按登录页真实契约（页面内一次性 salt + SHA1）登录 EAMS；
  2. 导出登录后首页，枚举「成绩」相关入口；
  3. 逐个试探候选成绩地址，记录状态码、长度、是否含成绩表格；
  4. 把原始 HTML 落到 probe_output/ 便于离线分析。

凭据只从环境变量读取，绝不写入磁盘或打印：
  CUPL_USERNAME / CUPL_PASSWORD
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "http://jwxt.cupl.edu.cn"
LOGIN_PAGE = f"{BASE}/eams/homeExt.action"
LOGIN_POST = f"{BASE}/eams/localLogin.action"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

OUT_DIR = Path(__file__).resolve().parent.parent / "probe_output"

# 页面中的真实形态：CryptoJS.SHA1('7d5fc536-...-58651-' + form['password'].value)
SALT_RE = re.compile(r"CryptoJS\.SHA1\(\s*['\"]([^'\"]+)['\"]\s*\+")

# 登录页面可能出现的错误/限流提示
ERROR_HINTS = ("请不要过快点击", "密码", "用户", "验证码", "锁定", "错误")

CANDIDATES = [
    "/eams/teach/grade/course/person!search.action",
    "/eams/teach/grade/course/person!historyCourseGrade.action",
    "/eams/teach/grade/course/person!report.action",
    "/eams/grade/course/person!search.action",
    "/eams/grade/student!search.action",
    "/eams/home.action",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def dump(name: str, text: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(text, encoding="utf-8")
    log(f"    -> 已保存 probe_output/{name} ({len(text)} 字符)")


def looks_like_login(html: str) -> bool:
    return 'id="loginForm"' in html or 'name="loginForm"' in html


def plain_text(html: str, limit: int = 400) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:limit]


def find_grade_links(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    hits: list[dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if "成绩" in text or "grade" in href.lower():
            hits.append({"text": text[:60], "href": href})
    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, str]] = []
    for h in hits:
        k = (h["href"], h["text"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(h)
    return uniq


def table_summary(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for idx, table in enumerate(soup.find_all("table")):
        headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        rows = table.find_all("tr")
        out.append(
            {
                "index": idx,
                "id": table.get("id", ""),
                "class": " ".join(table.get("class", [])),
                "th": headers[:12],
                "tr_count": len(rows),
            }
        )
    return out


def attempt_login(session: requests.Session, user: str, pwd: str) -> tuple[bool, str, str]:
    log("[1] GET 登录页")
    resp = session.get(LOGIN_PAGE, timeout=25)
    resp.encoding = "utf-8"
    page = resp.text
    log(f"    status={resp.status_code} final={resp.url} len={len(page)} 重定向={len(resp.history)}")
    for h in resp.history:
        log(f"    history {h.status_code} -> {h.headers.get('Location')}")
    log(f"    server={resp.headers.get('Server')} cookies={len(session.cookies)}")

    match = SALT_RE.search(page)
    if not match:
        log("[!] 未匹配到 salt，登录页结构可能已变化")
        return False, page, page
    salt = match.group(1)
    log(f"[2] salt 提取成功 len={len(salt)} 末位={salt[-1]!r}")

    digest = hashlib.sha1((salt + pwd).encode("utf-8")).hexdigest()
    log(f"[3] POST 登录 password=SHA1(salt+明文) hex_len={len(digest)}")
    post = session.post(
        LOGIN_POST,
        data={
            "username": user,
            "password": digest,
            "submitBtn": "登录",
            "session_locale": "zh_CN",
        },
        headers={
            "Referer": resp.url,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=25,
        allow_redirects=True,
    )
    post.encoding = "utf-8"
    body = post.text
    log(f"    status={post.status_code} final={post.url} len={len(body)} cookies={len(session.cookies)}")
    for h in post.history:
        log(f"    history {h.status_code} -> {h.headers.get('Location')}")

    ok = not looks_like_login(body)
    if ok:
        log("[4] 登录结果：已离开登录表单")
    else:
        log("[4] 登录结果：仍停留在登录页")
        snippet = plain_text(body, 200)
        log(f"    页面片段：{snippet}")
        for hint in ERROR_HINTS:
            if hint in body:
                log(f"    命中错误提示关键词：{hint}")
    return ok, body, page


def main() -> int:
    user = os.environ.get("CUPL_USERNAME", "").strip()
    pwd = os.environ.get("CUPL_PASSWORD", "")
    if not user or not pwd:
        log("请先设置环境变量 CUPL_USERNAME / CUPL_PASSWORD")
        return 2

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    login_html = ""
    ok = False
    body = ""
    for attempt in range(1, 3):
        log(f"===== 登录尝试 {attempt}/2 =====")
        # 每次重试都用全新会话，避免上一次的限流状态污染 salt
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        ok, body, login_html = attempt_login(session, user, pwd)
        dump(f"login_attempt_{attempt}.html", body)
        if ok:
            break
        if attempt < 2:
            log("    冷却 8 秒后重试（排除频率限制）")
            time.sleep(8)

    if not ok:
        log("[x] 两次均未登录成功，转入人工分析 probe_output/login_attempt_*.html")
        return 4

    log("[5] 登录后页面中与成绩相关的链接")
    links = find_grade_links(body)
    for item in links[:40]:
        log(f"    - {item['text']} => {item['href']}")
    if not links:
        log("    （页面未直接出现成绩链接，可能是 JS 菜单，继续按候选地址探测）")

    log("[6] 候选地址探测")
    results = []
    for path in CANDIDATES:
        url = BASE + path
        try:
            r = session.get(url, timeout=25, allow_redirects=True)
            r.encoding = "utf-8"
            text = r.text
            login_page = looks_like_login(text)
            tables = table_summary(text)
            has_grade_word = "成绩" in text
            log(
                f"    {path}\n"
                f"      status={r.status_code} final={r.url} len={len(text)} "
                f"登录页={login_page} 含'成绩'={has_grade_word} 表格数={len(tables)}"
            )
            for t in tables[:5]:
                log(f"      table[{t['index']}] id={t['id']!r} th={t['th']} tr={t['tr_count']}")
            if not login_page and len(text) > 2000:
                dump("cand_" + re.sub(r"[^0-9a-zA-Z]+", "_", path).strip("_") + ".html", text)
            results.append(
                {
                    "path": path,
                    "status": r.status_code,
                    "final_url": r.url,
                    "len": len(text),
                    "is_login_page": login_page,
                    "has_grade_word": has_grade_word,
                    "tables": tables,
                }
            )
            if not login_page and has_grade_word:
                break
        except Exception as exc:  # noqa: BLE001
            log(f"    {path} 请求失败：{type(exc).__name__}: {exc}")
            results.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})

    summary = {
        "login_ok": ok,
        "login_page_has_captcha_input": bool(re.search(r'name="captcha"', login_html, re.I)),
        "captcha_endpoint_present": "/eams/captcha/image.action" in login_html,
        "grade_links": links,
        "candidates": results,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "probe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("[7] 摘要已写入 probe_output/probe_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

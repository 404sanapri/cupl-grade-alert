#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 tpass 统一认证：表单字段、加密脚本、是否支持 REST 认证。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import requests

BASE = "https://ca.cupl.edu.cn"
SERVICE = "http://jwxt.cupl.edu.cn/eams/homeExt.action"
OUT = Path(__file__).resolve().parent.parent / "probe_output"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    url = f"{BASE}/tpass/login?service={quote(SERVICE, safe='')}"
    resp = session.get(url, timeout=25)
    resp.encoding = "utf-8"
    html = resp.text
    (OUT / "tpass_login.html").write_text(html, encoding="utf-8")

    print(f"status={resp.status_code} final={resp.url} len={len(html)}")
    print(f"cookies={[c.name for c in session.cookies]}")

    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I)
    print("scripts:")
    for src in srcs:
        print("  ", src)

    for name in ("lt", "execution", "rsa", "ul", "pl", "_eventId"):
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
        print(f"{name} = {match.group(1) if match else None}")

    # 顺便看看页面里是否有加密相关的内联脚本关键词
    for keyword in ("RSA", "AES", "encrypt", "publicKey", "JSEncrypt", "sm2", "sm4"):
        print(f"inline contains {keyword}: {keyword.lower() in html.lower()}")

    print("REST probes:")
    for path in ("/tpass/v1/tickets", "/tpass/serviceValidate"):
        try:
            rr = session.post(
                BASE + path, data={"username": "probe-nobody", "password": "probe-nobody"}, timeout=15
            )
            print(f"  {path} -> {rr.status_code} {rr.text[:150]!r}")
        except requests.RequestException as exc:
            print(f"  {path} -> ERR {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

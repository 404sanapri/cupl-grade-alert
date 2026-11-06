#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并分析 tpass 登录脚本，定位 rsa / ul / pl 的生成算法。"""

from __future__ import annotations

import re
from pathlib import Path

import requests

BASE = "https://ca.cupl.edu.cn"
OUT = Path(__file__).resolve().parent.parent / "probe_output"

FILES = [
    "/tpass/comm/se/js/login_se.js?v=20240412001",
    "/tpass/comm/se/js/comm_util.js",
    "/tpass/comm/se/js/hook.js?v=20240412001",
    "/tpass/comm/js/des.js",
]

KEYWORDS = ("ul", "pl", "rsa", "encrypt", "publicKey", "JSEncrypt", "DES", "md5", "password")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/tpass/login"})

    for path in FILES:
        resp = session.get(BASE + path, timeout=25)
        resp.encoding = "utf-8"
        text = resp.text
        name = path.split("?")[0].rsplit("/", 1)[-1]
        (OUT / name).write_text(text, encoding="utf-8")
        print(f"=== {name} len={len(text)} ===")

        for kw in KEYWORDS:
            hits = [m.start() for m in re.finditer(rf"\b{re.escape(kw)}\b", text)][:3]
            for pos in hits:
                snippet = text[max(0, pos - 220) : pos + 220].replace("\n", " ")
                print(f"[{kw}] ...{snippet}...")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

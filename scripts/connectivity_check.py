#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无凭据连通性自检：确认当前出口 IP 能否访问教务系统与统一认证。

在 GitHub Actions 上跑这个脚本，即可验证境外 runner 是否被学校 WAF 拦截。
"""

from __future__ import annotations

import re
import sys

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
EAMS_LOGIN = "http://jwxt.cupl.edu.cn/eams/homeExt.action"
TPASS_LOGIN = (
    "https://ca.cupl.edu.cn/tpass/login?service=http%3A%2F%2Fjwxt.cupl.edu.cn%2Feams%2FhomeExt.action"
)
TPASS_RSA = "https://ca.cupl.edu.cn/tpass/rsa"
SALT_RE = re.compile(r"CryptoJS\.SHA1\(\s*['\"]([^'\"]+)['\"]\s*\+")


def main() -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    checks: list[tuple[str, bool]] = []

    try:
        resp = session.get(EAMS_LOGIN, timeout=20)
        resp.encoding = "utf-8"
        has_salt = bool(SALT_RE.search(resp.text))
        print(f"[EAMS] status={resp.status_code} final={resp.url} len={len(resp.text)} salt={has_salt}")
        checks.append(("jwxt 登录页可达", resp.status_code == 200))
        checks.append(("登录页含 salt", has_salt))
    except requests.RequestException as exc:
        print(f"[EAMS] 失败：{type(exc).__name__}")
        checks.append(("jwxt 登录页可达", False))

    try:
        resp = session.get(TPASS_LOGIN, timeout=20)
        resp.encoding = "utf-8"
        print(f"[TPASS] status={resp.status_code} len={len(resp.text)}")
        checks.append(("tpass 登录页可达", resp.status_code == 200))
    except requests.RequestException as exc:
        print(f"[TPASS] 失败：{type(exc).__name__}")
        checks.append(("tpass 登录页可达", False))

    try:
        resp = session.post(
            TPASS_RSA,
            headers={"Referer": TPASS_LOGIN, "X-Requested-With": "XMLHttpRequest"},
            timeout=20,
        )
        has_key = "publicKey" in resp.text
        print(f"[TPASS-RSA] status={resp.status_code} publicKey={has_key}")
        checks.append(("可获取 RSA 公钥", has_key))
    except requests.RequestException as exc:
        print(f"[TPASS-RSA] 失败：{type(exc).__name__}")
        checks.append(("可获取 RSA 公钥", False))

    print("\n结果汇总：")
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    if not all(ok for _, ok in checks):
        print("\n存在失败项：该出口访问受限，请改用国内云函数或本机计划任务。")
        return 1
    print("\n全部通过：该出口可以完成登录与成绩抓取。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

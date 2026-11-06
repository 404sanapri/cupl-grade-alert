#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面冒烟测试：启动 web/server.py，逐个请求页面与接口，然后关闭。

用法：
  python scripts/ui_smoke_test.py
  设置 CUPL_USERNAME / CUPL_PASSWORD 后还会顺便验证「保存配置 + 测试登录」。
注意：会写入 config/.env，测试完请自行删除。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def get(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.read()


def post(path: str, payload: dict, timeout: int = 120) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {exc.code}"}


def main() -> int:
    env = dict(os.environ, GRADE_UI_NO_BROWSER="1")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "web" / "server.py"), "--port", str(PORT)],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    try:
        overview = None
        for _ in range(40):
            try:
                overview = json.loads(get("/api/overview"))
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.5)
        if overview is None:
            print("FAIL 服务未能启动")
            return 1

        print(f"PASS /api/overview（已配置={overview['data']['configured']}）")

        for name, path in (("index", "/"), ("css", "/style.css"), ("js", "/app.js")):
            size = len(get(path))
            ok = size > 200
            print(f"{'PASS' if ok else 'FAIL'} {path}（{size} 字节）")
            if not ok:
                failures.append(path)

        bad = post("/api/config", {"username": "", "channel": "dingtalk"})
        print("PASS 空学号被拒绝" if not bad.get("ok") else "FAIL 空学号未被拒绝")
        if bad.get("ok"):
            failures.append("config-validation")

        username = os.environ.get("CUPL_USERNAME", "").strip()
        password = os.environ.get("CUPL_PASSWORD", "")
        if username and password:
            saved = post("/api/config", {"username": username, "password": password, "channel": "none"})
            print(f"PASS /api/config（saved={saved['data']['saved']}）")
            result = post("/api/test-login", {})
            data = result["data"]
            print(f"PASS /api/test-login（本学期已出 {data['count']} 门，全部 {data['all_count']} 门）")
            if not data.get("all_count"):
                failures.append("test-login-empty")
        else:
            print("SKIP 未设置 CUPL_USERNAME / CUPL_PASSWORD，跳过登录验证")

        return 0 if not failures else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

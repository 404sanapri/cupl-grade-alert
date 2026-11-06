"""tpass「智慧法大」统一认证登录。

页面脚本的真实流程（来自 login_se.js）：
  1. POST /tpass/rsa 取 {publicKey}（Base64 的 DER SubjectPublicKeyInfo）
  2. ul = RSA(username)、pl = RSA(password)，JSEncrypt 默认 PKCS#1 v1.5
  3. 表单 submit 到 /tpass/login?service=<目标>
  4. CAS 回调目标系统并下发 ticket

RSA 只用到 pow() 与大整数，DER 解析手写，避免引入 cryptography 依赖。
"""

from __future__ import annotations

import base64
import os
import re
from urllib.parse import quote

import requests

TPASS_BASE = "https://ca.cupl.edu.cn"
EAMS_BASE = "http://jwxt.cupl.edu.cn"
EAMS_SERVICE = f"{EAMS_BASE}/eams/homeExt.action"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

LT_RE = re.compile(r'name="lt"[^>]*value="([^"]+)"')


class TpassError(RuntimeError):
    """统一认证交互失败。"""


class TpassCredentialError(TpassError):
    """用户名或密码错误。"""


def _read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    if pos + 2 > len(data):
        raise TpassError("DER 解析越界")
    tag = data[pos]
    pos += 1
    first = data[pos]
    pos += 1
    if first & 0x80:
        count = first & 0x7F
        length = int.from_bytes(data[pos : pos + count], "big")
        pos += count
    else:
        length = first
    value = data[pos : pos + length]
    return tag, value, pos + length


def parse_public_key(public_key_b64: str) -> tuple[int, int]:
    """从 SPKI DER 中取出 RSA 的 (modulus, exponent)。"""
    der = base64.b64decode(public_key_b64)
    _, spki, _ = _read_tlv(der, 0)
    _, _, after_alg = _read_tlv(spki, 0)
    _, bit_string, _ = _read_tlv(spki, after_alg)
    if not bit_string or bit_string[0] != 0:
        raise TpassError("公钥 BIT STRING 结构异常")
    _, rsa_pub, _ = _read_tlv(bit_string, 1)
    _, modulus, next_pos = _read_tlv(rsa_pub, 0)
    _, exponent, _ = _read_tlv(rsa_pub, next_pos)
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big")


def rsa_encrypt_b64(text: str, modulus: int, exponent: int) -> str:
    """等价于 JSEncrypt.encrypt()：PKCS#1 v1.5 + Base64。"""
    message = text.encode("utf-8")
    key_size = (modulus.bit_length() + 7) // 8
    padding_len = key_size - len(message) - 3
    if padding_len < 8:
        raise TpassError("待加密内容过长")
    padding = bytearray()
    while len(padding) < padding_len:
        byte = os.urandom(1)
        if byte != b"\x00":
            padding += byte
    encoded = b"\x00\x02" + bytes(padding) + b"\x00" + message
    cipher = pow(int.from_bytes(encoded, "big"), exponent, modulus)
    return base64.b64encode(cipher.to_bytes(key_size, "big")).decode("ascii")


class TpassClient:
    def __init__(self, username: str, password: str, timeout: int = 25) -> None:
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        self.last_url = ""

    def login(self) -> None:
        login_url = f"{TPASS_BASE}/tpass/login?service={quote(EAMS_SERVICE, safe='')}"
        resp = self.session.get(login_url, timeout=self.timeout)
        resp.encoding = "utf-8"
        html = resp.text

        match = LT_RE.search(html)
        if not match:
            raise TpassError("统一认证页面未找到 lt 令牌，页面可能改版")
        lt_value = match.group(1)

        key_resp = self.session.post(
            f"{TPASS_BASE}/tpass/rsa",
            headers={"Referer": login_url, "X-Requested-With": "XMLHttpRequest"},
            timeout=self.timeout,
        )
        try:
            public_key = key_resp.json()["publicKey"]
        except (ValueError, KeyError) as exc:
            raise TpassError("获取 RSA 公钥失败") from exc

        modulus, exponent = parse_public_key(public_key)
        payload = {
            "ul": rsa_encrypt_b64(self.username, modulus, exponent),
            "pl": rsa_encrypt_b64(self.password, modulus, exponent),
            "lt": lt_value,
            "execution": "e1s1",
            "_eventId": "submit",
            "choosenumber": self.username,
            "rsa": "",
        }

        result = self.session.post(
            login_url,
            data=payload,
            headers={"Referer": login_url, "Origin": TPASS_BASE},
            timeout=self.timeout,
            allow_redirects=True,
        )
        result.encoding = "utf-8"
        self.last_url = result.url
        self._assert_login_ok(result.text, result.url)

    def _assert_login_ok(self, html: str, url: str) -> None:
        if "用户名或密码错误" in html or "密码错误" in html:
            raise TpassCredentialError("统一认证登录失败：用户名或密码错误")
        if "/tpass/login" in url:
            raise TpassError("统一认证登录未跳转，可能被风控或表单字段变化")
        if "jwxt.cupl.edu.cn" not in url:
            raise TpassError(f"登录后未回到教务系统，当前地址：{url}")
        if 'id="loginForm"' in html:
            raise TpassError("教务系统仍要求登录，SSO 会话未建立")

    def get(self, url: str) -> str:
        resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        resp.encoding = "utf-8"
        self.last_url = resp.url
        return resp.text

    def post(self, url: str, data: dict | None = None) -> str:
        resp = self.session.post(url, data=data, timeout=self.timeout, allow_redirects=True)
        resp.encoding = "utf-8"
        self.last_url = resp.url
        return resp.text

    def fetch_history_grades(self) -> str:
        """「所有学期成绩」接口，含总评成绩 / 绩点等列。"""
        return self.post(
            f"{EAMS_BASE}/eams/teach/grade/course/person!historyCourseGrade.action?projectType=MAJOR"
        )

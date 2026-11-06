"""EAMS 会话管理：加盐登录、会话校验、成绩页抓取。

登录契约（来自阶段 0 探测，勿凭猜测修改）：
  1. GET /eams/homeExt.action → 302 → /eams/localLogin.action
  2. 从页面脚本提取本次随机 salt：CryptoJS.SHA1('<salt>' + password)
  3. password = sha1(salt + 明文密码).hexdigest()
  4. POST /eams/localLogin.action，字段 username / password
  5. 成败只能按页面文本判定，HTTP 状态码恒为 200
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import GRADE_URL_CANDIDATES
from tpass_auth import TpassClient, TpassCredentialError, TpassError

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SALT_RE = re.compile(r"CryptoJS\.SHA1\(\s*['\"]([^'\"]+)['\"]\s*\+")
SSO_HOST = "ca.cupl.edu.cn"


class EamsError(RuntimeError):
    """EAMS 交互异常基类，message 面向用户，可安全写日志。"""


class CredentialError(EamsError):
    """学号或密码错误，重试无意义。"""


class RateLimitedError(EamsError):
    """被「请不要过快点击」一类的频率保护拦截。"""


class CaptchaRequiredError(EamsError):
    """出现了验证码，自动登录无法继续。"""


class SessionExpiredError(EamsError):
    """登录态无效，或业务页被统一认证接管。"""


class PageChangedError(EamsError):
    """页面结构与预期不符，需要人工复核。"""


def looks_like_login(html: str) -> bool:
    return 'id="loginForm"' in html or 'name="loginForm"' in html


def is_sso_redirect(url: str) -> bool:
    return SSO_HOST in url and "/tpass/" in url


def extract_salt(html: str) -> str | None:
    match = SALT_RE.search(html)
    return match.group(1) if match else None


def page_text(html: str, limit: int = 300) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:limit]


class EamsClient:
    CONFIRMED_GRADE_PATH = (
        "/eams/teach/grade/course/person!historyCourseGrade.action?projectType=MAJOR"
    )

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = "http://jwxt.cupl.edu.cn",
        timeout: int = 25,
        auth_mode: str = "tpass",
    ) -> None:
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_mode = auth_mode
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Connection": "keep-alive",
            }
        )
        self.last_url = ""
        self._last_post_at = 0.0

    # ---------- 基础请求 ----------

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urljoin(self.base_url + "/", path_or_url.lstrip("/"))

    def get(self, path_or_url: str) -> str:
        resp = self.session.get(self._url(path_or_url), timeout=self.timeout, allow_redirects=True)
        resp.encoding = "utf-8"
        self.last_url = resp.url
        return resp.text

    # ---------- 登录 ----------

    def login(self) -> None:
        if self.auth_mode == "tpass":
            log.info("使用统一认证 tpass 登录")
            try:
                tpass = TpassClient(self.username, self.password, self.timeout)
                tpass.login()
            except TpassCredentialError as exc:
                raise CredentialError(str(exc)) from exc
            except TpassError as exc:
                raise EamsError(str(exc)) from exc
            self.session = tpass.session
            self.last_url = tpass.last_url
            log.info("统一认证登录成功")
            return

        log.info("读取登录页")
        login_html = self.get("/eams/homeExt.action")

        salt = extract_salt(login_html)
        if not salt:
            raise PageChangedError("登录页未找到 salt，页面结构可能已变化")

        digest = hashlib.sha1((salt + self.password).encode("utf-8")).hexdigest()

        # 取页后至少停留 3 秒再提交：服务端对毫秒级提交会返回「请不要过快点击」。
        # 真实浏览器里人工输入天然有数秒间隔，脚本必须显式复现。
        time.sleep(3)
        self._last_post_at = time.monotonic()

        referer = self.last_url or self._url("/eams/localLogin.action")
        log.info("提交登录")
        resp = self.session.post(
            self._url("/eams/localLogin.action"),
            data={"username": self.username, "password": digest},
            headers={
                "Referer": referer,
                "Origin": self.base_url,
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=self.timeout,
            allow_redirects=True,
        )
        resp.encoding = "utf-8"
        self.last_url = resp.url
        self._assert_login_ok(resp.text, resp.url)

    def _assert_login_ok(self, html: str, url: str) -> None:
        text = page_text(html, 500)
        if "请不要过快点击" in html:
            raise RateLimitedError("被登录频率保护拦截（请不要过快点击），需退避后重试")
        if "密码错误" in text or "用户名或密码错误" in text:
            raise CredentialError("登录失败：学号或密码错误")
        if re.search(r'name="captcha"', html, re.I) or "验证码" in text:
            raise CaptchaRequiredError("登录需要验证码，请人工处理或降低查询频率")
        if looks_like_login(html):
            raise EamsError("登录未成功：页面仍停留在登录表单")
        if is_sso_redirect(url):
            raise SessionExpiredError("登录被统一认证接管，需要走 tpass 流程")
        log.info("登录成功")

    # ---------- 成绩页 ----------

    def discover_grade_paths(self) -> list[str]:
        """登录后从首页菜单里找成绩入口，弥补硬编码候选的不足。"""
        try:
            html = self.get("/eams/home.action")
        except requests.RequestException:
            return []
        if looks_like_login(html):
            return []
        soup = BeautifulSoup(html, "html.parser")
        found: list[str] = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            href = a["href"]
            if ("成绩" in text or "grade" in href.lower()) and "javascript" not in href.lower():
                if href not in found:
                    found.append(href)
        if found:
            log.info("首页发现 %d 个成绩相关入口", len(found))
        return found[:5]

    def post(self, path_or_url: str, data: dict | None = None) -> str:
        resp = self.session.post(
            self._url(path_or_url), data=data, timeout=self.timeout, allow_redirects=True
        )
        resp.encoding = "utf-8"
        self.last_url = resp.url
        return resp.text

    @staticmethod
    def _has_grade_table(html: str) -> bool:
        return "gridtable" in html or ("成绩" in html and "<table" in html.lower())

    def fetch_grades_html(self, grade_url: str = "") -> str:
        # 1) 显式配置优先
        if grade_url:
            html = self.get(grade_url)
            if self._has_grade_table(html):
                log.info("命中成绩页：%s（%d 字符）", grade_url, len(html))
                return html
            if is_sso_redirect(self.last_url):
                raise SessionExpiredError("成绩页被统一认证接管，登录态无效")
            raise PageChangedError("配置的成绩页未包含成绩表格，请检查 CUPL_GRADE_URL")

        # 2) 阶段 0 已验证的「所有学期成绩」接口
        html = self.post(self.CONFIRMED_GRADE_PATH)
        if self._has_grade_table(html):
            log.info("命中成绩页：historyCourseGrade（%d 字符）", len(html))
            return html
        if looks_like_login(html) or is_sso_redirect(self.last_url):
            raise SessionExpiredError("登录态失效，成绩接口被重定向")

        # 3) 回退：首页菜单发现 + 候选路径
        candidates = self.discover_grade_paths() or []
        for fallback in GRADE_URL_CANDIDATES:
            if fallback not in candidates:
                candidates.append(fallback)

        for path in candidates:
            try:
                html = self.get(path)
            except requests.RequestException as exc:
                log.warning("成绩页请求失败 %s：%s", path, type(exc).__name__)
                continue
            if is_sso_redirect(self.last_url) or looks_like_login(html):
                continue
            if self._has_grade_table(html):
                log.info("命中成绩页：%s（%d 字符）", path, len(html))
                return html

        raise PageChangedError("未能定位成绩表格，页面可能改版")

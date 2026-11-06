from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eams_client import (  # noqa: E402
    CaptchaRequiredError,
    CredentialError,
    EamsClient,
    RateLimitedError,
    SessionExpiredError,
    extract_salt,
    is_sso_redirect,
    looks_like_login,
)
from tpass_auth import TpassError, parse_public_key, rsa_encrypt_b64  # noqa: E402

# 探测阶段从 /tpass/rsa 实际拿到的公钥
REAL_PUBLIC_KEY = (
    "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALSw62r7IV2X7AzSkk5SnttwPGQd/eJDSL/pQ9ciaZbNr1euKK5N"
    "KRlbiDBvxlQdz5zEyrYSfqYRjo/RYvLqbv8CAwEAAQ=="
)

LOGIN_PAGE = """
<form id="loginForm" name="loginForm" action="/eams/localLogin.action" method="post">
<script>
form['password'].value = CryptoJS.SHA1('7d5fc536-0528-43b0-a932-d8541ce58651-' + form['password'].value);
</script>
</form>
"""


class SaltTest(unittest.TestCase):
    def test_extract_salt_keeps_trailing_dash(self) -> None:
        salt = extract_salt(LOGIN_PAGE)
        self.assertEqual(salt, "7d5fc536-0528-43b0-a932-d8541ce58651-")

    def test_extract_salt_returns_none_on_changed_page(self) -> None:
        self.assertIsNone(extract_salt("<html>nothing</html>"))


class DetectTest(unittest.TestCase):
    def test_looks_like_login(self) -> None:
        self.assertTrue(looks_like_login(LOGIN_PAGE))
        self.assertFalse(looks_like_login("<html>home</html>"))

    def test_sso_redirect(self) -> None:
        self.assertTrue(
            is_sso_redirect(
                "https://ca.cupl.edu.cn/tpass/login?service=http%3A%2F%2Fjwxt.cupl.edu.cn%2Feams"
            )
        )
        self.assertFalse(is_sso_redirect("http://jwxt.cupl.edu.cn/eams/homeExt.action"))


class LoginVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EamsClient("202400000", "secret")

    def test_rate_limited(self) -> None:
        with self.assertRaises(RateLimitedError):
            self.client._assert_login_ok("<span>请不要过快点击</span>", "http://x/eams/localLogin.action")

    def test_wrong_password(self) -> None:
        with self.assertRaises(CredentialError):
            self.client._assert_login_ok("<div>密码错误</div>", "http://x/eams/localLogin.action")

    def test_captcha(self) -> None:
        with self.assertRaises(CaptchaRequiredError):
            self.client._assert_login_ok('<input name="captcha">', "http://x/eams/localLogin.action")

    def test_sso_redirect_after_login(self) -> None:
        with self.assertRaises(SessionExpiredError):
            self.client._assert_login_ok("ok", "https://ca.cupl.edu.cn/tpass/login?service=x")

    def test_success(self) -> None:
        self.client._assert_login_ok("<div>欢迎</div>", "http://jwxt.cupl.edu.cn/eams/homeExt.action")


class RsaTest(unittest.TestCase):
    def test_parse_public_key(self) -> None:
        modulus, exponent = parse_public_key(REAL_PUBLIC_KEY)
        self.assertEqual(exponent, 65537)
        self.assertEqual(modulus.bit_length(), 512)

    def test_encrypt_output_length(self) -> None:
        modulus, exponent = parse_public_key(REAL_PUBLIC_KEY)
        cipher = rsa_encrypt_b64("test-password", modulus, exponent)
        self.assertEqual(len(cipher), 88)  # 64 字节的 Base64

    def test_reject_overlong_plaintext(self) -> None:
        modulus, exponent = parse_public_key(REAL_PUBLIC_KEY)
        with self.assertRaises(TpassError):
            rsa_encrypt_b64("x" * 200, modulus, exponent)


if __name__ == "__main__":
    unittest.main()

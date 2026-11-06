from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402

_NO_ENV_FILE = Path("__no_such_env_file__")


def load(**env: str) -> config.Settings:
    base = {"CUPL_USERNAME": "2026000000", "CUPL_PASSWORD": "pw"}
    base.update(env)
    with mock.patch.dict(os.environ, base, clear=True):
        return config.load_settings(env_file=_NO_ENV_FILE)


class ChannelTest(unittest.TestCase):
    def test_default_channel_is_desktop(self) -> None:
        settings = load()
        self.assertEqual(settings.channel, "desktop")
        self.assertTrue(settings.notify_enabled)

    def test_desktop_channel_does_not_require_webhook(self) -> None:
        self.assertTrue(load(NOTIFY_CHANNEL="desktop").notify_enabled)

    def test_chinese_alias(self) -> None:
        self.assertEqual(load(NOTIFY_CHANNEL="电脑通知").channel, "desktop")

    def test_none_channel_disables_notification(self) -> None:
        self.assertFalse(load(NOTIFY_CHANNEL="none").notify_enabled)

    def test_webhook_channel_without_url_is_rejected(self) -> None:
        with self.assertRaises(config.ConfigError):
            load(NOTIFY_CHANNEL="dingtalk")


class StrongAlertTest(unittest.TestCase):
    def test_strong_alert_defaults_on(self) -> None:
        self.assertTrue(load().strong_alert)

    def test_strong_alert_can_be_disabled(self) -> None:
        self.assertFalse(load(NOTIFY_STRONG="0").strong_alert)
        self.assertFalse(load(NOTIFY_STRONG="false").strong_alert)


if __name__ == "__main__":
    unittest.main()

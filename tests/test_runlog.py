from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import runlog  # noqa: E402


class RunLogTest(unittest.TestCase):
    def test_append_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "runs.jsonl"
            runlog.append({"at": "1"}, path=path)
            self.assertTrue(path.is_file())

    def test_read_returns_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.jsonl"
            runlog.append({"at": "1", "ok": True}, path=path)
            runlog.append({"at": "2", "ok": False}, path=path)
            runs = runlog.read(10, path=path)
            self.assertEqual([item["at"] for item in runs], ["2", "1"])

    def test_read_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.jsonl"
            for index in range(5):
                runlog.append({"at": str(index)}, path=path)
            self.assertEqual(len(runlog.read(2, path=path)), 2)

    def test_read_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(runlog.read(path=Path(tmp) / "none.jsonl"), [])

    def test_read_skips_broken_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.jsonl"
            path.write_text('{not json}\n{"at": "ok"}\n', encoding="utf-8")
            runs = runlog.read(path=path)
            self.assertEqual([item["at"] for item in runs], ["ok"])

    def test_chinese_is_not_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.jsonl"
            runlog.append({"new": ["民法学原理四"]}, path=path)
            self.assertIn("民法学原理四", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

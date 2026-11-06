from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import state  # noqa: E402
from grade import CourseGrade  # noqa: E402


class StateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.g1 = CourseGrade("法学基本理论", "2", "98", "6.3", "2024-2025-2")
        self.g2 = CourseGrade("中国法律思想史", "2", "91", "5.1", "2025-2026-1")

    def test_first_run_never_alerts(self) -> None:
        snapshot = state._empty()
        self.assertEqual(state.diff(snapshot, [self.g1, self.g2]), [])

    def test_only_new_grades_are_reported(self) -> None:
        snapshot = state.update(state._empty(), [self.g1])
        self.assertEqual(state.diff(snapshot, [self.g1]), [])
        self.assertEqual(state.diff(snapshot, [self.g1, self.g2]), [self.g2])

    def test_idempotent_after_update(self) -> None:
        snapshot = state.update(state._empty(), [self.g1, self.g2])
        again = state.update(snapshot, [self.g1, self.g2])
        self.assertEqual(state.diff(again, [self.g1, self.g2]), [])
        self.assertEqual(again["keys"], snapshot["keys"])

    def test_save_load_roundtrip_keeps_failure_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grades.json"
            snapshot = state.update(state._empty(), [self.g1])
            snapshot["consecutive_failures"] = 3
            state.save(path, snapshot)

            loaded = state.load(path)
            self.assertEqual(loaded["keys"], snapshot["keys"])
            self.assertEqual(loaded["consecutive_failures"], 3)

    def test_corrupted_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grades.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(state.StateError):
                state.load(path)

    def test_reset_removes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grades.json"
            state.save(path, state.update(state._empty(), [self.g1]))
            state.reset(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

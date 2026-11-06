from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from grade import (  # noqa: E402
    CourseGrade,
    GradeParseError,
    courses_of_term,
    current_term,
    make_key,
    parse_grades,
    term_label,
)

SAMPLE = """
<table class="gridtable">
  <tr>
    <th>学年度-学期</th><th>课程号</th><th>课程序号</th><th>课程名称</th>
    <th>课程英文名称</th><th>课程属性</th><th>学分</th><th>过程性成绩1</th>
    <th>过程性成绩2</th><th>期末成绩</th><th>总评成绩</th><th>最终</th>
    <th>绩点</th><th>最高分</th><th>最低分</th><th>平均分</th><th>课堂成绩排名</th>
  </tr>
  <tr>
    <td>2024-2025-2</td><td>401090032</td><td>401090032.01</td><td>法学基本理论</td>
    <td>Essential theory</td><td>限选</td><td>2</td><td>100</td><td>98</td><td>--</td>
    <td>98</td><td>98</td><td>6.3</td><td>99</td><td>96</td><td>97.73</td><td>4</td>
  </tr>
  <tr>
    <td>2024-2025-2</td><td>426100612</td><td>426100612.01</td><td>法学前沿问题研究（一）</td>
    <td>Frontiers</td><td>限选</td><td>2</td><td>97</td><td>--</td><td>--</td>
    <td>--</td><td>--</td><td>--</td><td>100</td><td>92</td><td>96.12</td><td>12</td>
  </tr>
</table>
"""


class ParseGradesTest(unittest.TestCase):
    def test_maps_columns_by_header_not_position(self) -> None:
        grades = parse_grades(SAMPLE)
        self.assertEqual(len(grades), 1)
        item = grades[0]
        self.assertEqual(item.course_name, "法学基本理论")
        self.assertEqual(item.score, "98")
        self.assertEqual(item.credit, "2")
        self.assertEqual(item.gpa, "6.3")
        self.assertEqual(item.term, "2024-2025-2")

    def test_skips_course_without_final_score(self) -> None:
        # 第二条总评成绩为 --，属于「尚未出分」，不得计入新增
        names = [g.course_name for g in parse_grades(SAMPLE)]
        self.assertNotIn("法学前沿问题研究（一）", names)

    def test_raises_when_no_table(self) -> None:
        with self.assertRaises(GradeParseError):
            parse_grades("<div>登录成功但没有任何表格</div>")

    def test_raises_when_header_missing(self) -> None:
        with self.assertRaises(GradeParseError):
            parse_grades("<table><tr><td>学号</td><td>姓名</td></tr></table>")

    def test_key_is_stable_and_term_scoped(self) -> None:
        self.assertEqual(make_key("法学基本理论", "2024-2025-2"), make_key("法学基本理论", "2024-2025-2"))
        self.assertNotEqual(make_key("法学基本理论", "2024-2025-2"), make_key("法学基本理论", "2025-2026-1"))


class TermTest(unittest.TestCase):
    def test_first_semester(self) -> None:
        self.assertEqual(current_term(date(2026, 11, 6)), "2026-2027-1")
        self.assertEqual(current_term(date(2026, 8, 1)), "2026-2027-1")
        self.assertEqual(current_term(date(2027, 1, 15)), "2026-2027-1")

    def test_second_semester(self) -> None:
        self.assertEqual(current_term(date(2027, 3, 1)), "2026-2027-2")
        self.assertEqual(current_term(date(2027, 7, 31)), "2026-2027-2")

    def test_label(self) -> None:
        self.assertEqual(term_label("2026-2027-1"), "2026-2027学年第一学期")
        self.assertEqual(term_label("2026-2027-2"), "2026-2027学年第二学期")
        self.assertEqual(term_label("other"), "other")

    def test_courses_of_term_only_returns_names(self) -> None:
        grades = [
            CourseGrade("A 科目", "2", "90", "4.0", "2026-2027-1"),
            CourseGrade("B 科目", "2", "80", "3.0", "2025-2026-2"),
            CourseGrade("C 科目", "2", "85", "3.5", "2026-2027-1"),
        ]
        self.assertEqual(courses_of_term(grades, "2026-2027-1"), ["A 科目", "C 科目"])
        self.assertEqual(courses_of_term(grades, "2027-2028-1"), [])


if __name__ == "__main__":
    unittest.main()

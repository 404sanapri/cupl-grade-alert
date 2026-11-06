"""成绩页面解析。

不硬编码列顺序：先按表头关键词定位列，再按行解析。
结构不符时抛 GradeParseError，让上层发「监控失效」告警，绝不静默返回空列表。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from bs4 import BeautifulSoup

_SKIP_NAME_HINTS = ("合计", "平均", "总学分", "备注", "说明")
_EMPTY_SCORES = {"", "-", "--", "—", "/", "未出", "暂无"}

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "course": ("课程名称", "课程名", "课程", "科目", "教学班"),
    "credit": ("学分",),
    "score": ("总评成绩", "总评", "成绩", "分数", "最终成绩", "最终"),
    "gpa": ("学分绩点", "绩点", "GPA"),
    "term": ("学年度-学期", "学年学期", "开课学期", "学期", "学年"),
}

# 包含式匹配时容易误中的同族列，必须排除
_NOISE_HINTS: dict[str, tuple[str, ...]] = {
    "course": ("课程号", "课程序号", "课程英文", "课程属性"),
    "score": ("过程性", "期末", "最高", "最低", "平均", "排名", "选课人数"),
}


class GradeParseError(RuntimeError):
    """成绩表格缺失或结构异常。"""


class CourseGrade:
    """一条成绩记录。key 为稳定比对键，不含分数。"""

    __slots__ = ("key", "course_name", "credit", "score", "gpa", "term")

    def __init__(
        self,
        course_name: str,
        credit: str = "",
        score: str = "",
        gpa: str = "",
        term: str = "",
    ) -> None:
        self.course_name = course_name.strip()
        self.credit = credit.strip()
        self.score = score.strip()
        self.gpa = gpa.strip()
        self.term = term.strip()
        self.key = make_key(self.course_name, self.term)

    def __repr__(self) -> str:
        return f"CourseGrade({self.course_name!r}, score={self.score!r}, term={self.term!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CourseGrade) and self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def as_row(self) -> str:
        parts = [self.course_name]
        if self.credit:
            parts.append(f"{self.credit} 学分")
        if self.score:
            parts.append(f"{self.score} 分")
        if self.gpa:
            parts.append(f"绩点 {self.gpa}")
        if self.term:
            parts.append(self.term)
        return " · ".join(parts)


def make_key(course_name: str, term: str) -> str:
    raw = f"{term}|{course_name}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _cell_texts(row) -> list[str]:
    cells = row.find_all(["td", "th"])
    return [c.get_text(" ", strip=True) for c in cells]


def _match_columns(header: list[str]) -> dict[str, int]:
    """先精确匹配，再退化为包含匹配，避免「课程号」抢走「课程名称」。"""
    mapping: dict[str, int] = {}
    normalized = [_norm(h) for h in header]

    for field, aliases in _HEADER_ALIASES.items():
        normalized_aliases = [_norm(alias) for alias in aliases]
        noise = _NOISE_HINTS.get(field, ())

        for alias in normalized_aliases:
            for idx, name in enumerate(normalized):
                if name and name == alias:
                    mapping[field] = idx
                    break
            if field in mapping:
                break
        if field in mapping:
            continue

        for alias in normalized_aliases:
            for idx, name in enumerate(normalized):
                if not name or alias not in name:
                    continue
                if any(hint in name for hint in noise):
                    continue
                mapping[field] = idx
                break
            if field in mapping:
                break

    return mapping


def _find_header(rows) -> tuple[int, dict[str, int]] | None:
    best: tuple[int, dict[str, int]] | None = None
    for idx, row in enumerate(rows[:5]):
        mapping = _match_columns(_cell_texts(row))
        if "course" in mapping and "score" in mapping:
            return idx, mapping
        if "course" in mapping and best is None:
            best = (idx, mapping)
    return best


def _pick(cells: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(cells):
        return ""
    return cells[idx].strip()


def parse_grades(html: str, default_term: str = "") -> list[CourseGrade]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise GradeParseError("页面中没有任何表格，可能未进入成绩页或页面已改版")

    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        found = _find_header(rows)
        if not found:
            continue
        header_idx, mapping = found
        grades: list[CourseGrade] = []
        for row in rows[header_idx + 1 :]:
            cells = _cell_texts(row)
            if not cells:
                continue
            name = _pick(cells, mapping.get("course"))
            if not name or any(hint in name for hint in _SKIP_NAME_HINTS):
                continue
            score = _pick(cells, mapping.get("score"))
            if score in _EMPTY_SCORES:
                # 课程已列出但尚未出分，不计入「新增成绩」
                continue
            grades.append(
                CourseGrade(
                    course_name=name,
                    credit=_pick(cells, mapping.get("credit")),
                    score=score,
                    gpa=_pick(cells, mapping.get("gpa")),
                    term=_pick(cells, mapping.get("term")) or default_term,
                )
            )
        if grades:
            return grades

    raise GradeParseError("找到表格但未匹配到「课程 + 成绩」列，解析规则需更新")


def current_term(today: date | None = None) -> str:
    """按日期推算当前学期，格式与教务系统一致：2026-2027-1。

    8 月至次年 1 月为第一学期，2 月至 7 月为第二学期。
    """
    today = today or date.today()
    year, month = today.year, today.month
    if month >= 8:
        start, semester = year, 1
    elif month == 1:
        start, semester = year - 1, 1
    else:
        start, semester = year - 1, 2
    return f"{start}-{start + 1}-{semester}"


def term_label(term: str) -> str:
    """把 2026-2027-1 转成 2026-2027学年第一学期。"""
    parts = term.split("-")
    if len(parts) == 3 and parts[2] in {"1", "2"}:
        ordinal = "第一" if parts[2] == "1" else "第二"
        return f"{parts[0]}-{parts[1]}学年{ordinal}学期"
    return term


def courses_of_term(grades: list[CourseGrade], term: str) -> list[str]:
    """返回该学期已出分的科目名（去重、排序），不携带任何分数信息。"""
    return sorted({item.course_name for item in grades if item.term == term})

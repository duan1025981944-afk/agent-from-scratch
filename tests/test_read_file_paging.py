"""read_file 分段读的测试：大文件一次读不完时，整行整行地给，并告诉模型怎么接着读。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.payload import MAX_TOOL_RESULT_CHARS
from agent.tools import workspace
from agent.tools.read_file import ReadFileTool


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """工作区和禁区都指到临时目录（相当于在项目自己的目录里启动）。"""
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())
    monkeypatch.setattr(workspace, "NO_WRITE_DIRS", (tmp_path.resolve() / "data",))
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def test_small_file_is_returned_as_is(sandbox):
    """装得下的文件原样返回，不加任何说明——和以前的行为一样。

    负对照：把"start == 1 and end == total 就原样返回"那一句去掉 → 末尾多一句说明，本测试红。
    """
    (sandbox / "a.txt").write_text("one\ntwo\n", encoding="utf-8")

    assert run(ReadFileTool().execute("a.txt")) == "one\ntwo\n"


def test_big_file_is_cut_at_a_line_and_says_how_to_continue(sandbox):
    """装不下的文件只给开头一段：整行整行地给，不超过单条上限，并告诉模型接着读的 offset。

    负对照：把循环里"装不下就 break"那一句去掉 → 返回全文、超过上限，本测试红。
    """
    (sandbox / "big.txt").write_text("".join(f"第 {i} 行\n" for i in range(1, 3001)), encoding="utf-8")

    result = run(ReadFileTool().execute("big.txt"))

    assert len(result) <= MAX_TOOL_RESULT_CHARS, "不能再被发送前的截断切一刀"
    assert result.startswith("第 1 行\n")
    assert "文件共 3000 行" in result and "offset=" in result


def test_offset_and_limit_pick_exact_lines(sandbox):
    """offset 从 1 数；limit 是行数。

    负对照：把 lines[start - 1:...] 写成 lines[start:...] → 多跳一行，本测试红。
    """
    (sandbox / "a.txt").write_text("".join(f"L{i}\n" for i in range(1, 11)), encoding="utf-8")

    result = run(ReadFileTool().execute("a.txt", offset=3, limit=2))

    assert result.startswith("L3\nL4\n")
    assert "第 3–4 行" in result and "offset=5" in result


def test_reading_can_be_continued_to_the_end(sandbox):
    """照着"接着读"的 offset 一段段读下去，能把文件一行不落地读完。

    负对照：把说明里的 offset={end + 1} 写成 {end} → 每段开头重复一行，本测试红。
    """
    original = "".join(f"第 {i} 行的内容\n" for i in range(1, 2001))
    (sandbox / "big.txt").write_text(original, encoding="utf-8")

    pieces, offset = [], 1
    for _ in range(50):
        result = run(ReadFileTool().execute("big.txt", offset=offset))
        body, _, note = result.rpartition("\n\n…（")
        pieces.append(body + "\n")
        if "已到文件末尾" in note:
            break
        offset = int(note.split("offset=")[1].rstrip("）"))

    assert "".join(pieces) == original


def test_offset_past_the_end_is_an_error(sandbox):
    """offset 超过文件行数：报错并告诉模型文件一共几行。

    负对照：把 offset 的范围检查去掉 → 返回空内容，本测试红。
    """
    (sandbox / "a.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = run(ReadFileTool().execute("a.txt", offset=10))

    assert "共 2 行" in result

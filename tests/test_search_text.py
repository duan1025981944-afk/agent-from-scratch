"""search_text 工具的测试：在文件内容里找一段文字。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.tools import workspace
from agent.tools.search_text import SearchTextTool


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """工作区和禁区都指到临时目录（相当于在项目自己的目录里启动）。"""
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())
    monkeypatch.setattr(workspace, "NO_WRITE_DIRS", (tmp_path.resolve() / "data",))
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def test_search_reports_path_and_line_number(sandbox):
    """每处一行：相对路径:行号: 那一行。

    负对照：把 enumerate(..., 1) 的 1 去掉 → 行号从 0 数，本测试红。
    """
    (sandbox / "pkg").mkdir()
    (sandbox / "pkg" / "m.py").write_text("a = 1\ndef build():\n    pass\n", encoding="utf-8")

    result = run(SearchTextTool().execute("def build"))

    assert result == "pkg/m.py:2: def build():"


def test_search_skips_junk_and_protected_dirs(sandbox):
    """.git、__pycache__ 和 Agent 自己的 data/ 不搜。

    负对照：把 _walk 里筛目录的那几个条件去掉 → 多出三处，本测试红。
    """
    for folder in [".git", "__pycache__", "data", "src"]:
        (sandbox / folder).mkdir()
        (sandbox / folder / "x.txt").write_text("needle\n", encoding="utf-8")

    result = run(SearchTextTool().execute("needle"))

    assert result == "src/x.txt:1: needle"


def test_search_says_so_when_nothing_matches(sandbox):
    """没找到要回一句人话，不能是空字符串（空的会让模型以为工具坏了）。

    负对照：把"没有找到"那段删掉 → 返回空字符串，本测试红。
    """
    (sandbox / "a.txt").write_text("hello\n", encoding="utf-8")

    result = run(SearchTextTool().execute("nothing-like-this"))

    assert "没有找到" in result


def test_search_caps_the_number_of_matches(sandbox):
    """太多处时只列前 50 处，并说明一共多少处。

    负对照：把 len(hits) < MAX_MATCHES 的判断去掉 → 列出全部 80 行，本测试红。
    """
    (sandbox / "big.txt").write_text("needle\n" * 80, encoding="utf-8")

    lines = run(SearchTextTool().execute("needle")).splitlines()

    assert len(lines) == 51
    assert "共 80 处" in lines[-1]

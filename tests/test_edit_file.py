"""edit_file 工具的测试：只改文件里的一小段。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.tools import workspace
from agent.tools.edit_file import EditFileTool


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """工作区和禁区都指到临时目录（相当于在项目自己的目录里启动）。"""
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())
    monkeypatch.setattr(workspace, "NO_WRITE_DIRS", (tmp_path.resolve() / "data",))
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def test_edit_replaces_only_the_given_piece(sandbox):
    """只换 old_text 那一段，前后内容一个字不动。

    负对照：把 text.replace(old, new, 1) 写成 new（整个文件只剩新内容）→ 本测试红。
    """
    f = sandbox / "a.py"
    f.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

    result = run(EditFileTool().execute("a.py", "y = 2", "y = 20"))

    assert "已修改" in result
    assert f.read_text(encoding="utf-8") == "x = 1\ny = 20\nz = 3\n"


def test_edit_refuses_when_text_is_not_found(sandbox):
    """old_text 对不上原文：不改文件，并告诉模型先去看原文。

    负对照：把 count == 0 那段删掉 → 回执变成"已修改"，本测试红。
    """
    f = sandbox / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")

    result = run(EditFileTool().execute("a.py", "x = 2", "x = 3"))

    assert "没找到" in result and "read_file" in result
    assert f.read_text(encoding="utf-8") == "x = 1\n"


def test_edit_refuses_when_text_is_ambiguous(sandbox):
    """old_text 出现了不止一次：不知道改哪处，就一处都不改。

    负对照：把 count > 1 那段删掉 → 悄悄改掉第一处，本测试红。
    """
    f = sandbox / "a.py"
    f.write_text("print(1)\nprint(1)\n", encoding="utf-8")

    result = run(EditFileTool().execute("a.py", "print(1)", "print(2)"))

    assert "2 次" in result
    assert f.read_text(encoding="utf-8") == "print(1)\nprint(1)\n"


def test_edit_cannot_touch_protected_dir(sandbox):
    """★ 不变量 6 对新工具同样成立：edit_file 也不能改 Agent 自己的 data/。

    负对照：把 edit_file 里的 resolve_for_write 换成 resolve → 记忆被改掉，本测试红。
    """
    memory = sandbox / "data" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("- 项目代号 蓝鲸-7\n", encoding="utf-8")

    result = run(EditFileTool().execute("data/memory/MEMORY.md", "蓝鲸-7", "白鲸-9"))

    assert "不能写入" in result
    assert "蓝鲸-7" in memory.read_text(encoding="utf-8")


def test_edit_keeps_windows_line_endings(sandbox):
    """文件原来是 \\r\\n 换行的，改完还是 \\r\\n——不然整个文件每一行都算被改过。

    负对照：把写回前"还原成 \\r\\n"那两行删掉 → 文件变成 \\n 换行，本测试红。
    """
    f = sandbox / "a.txt"
    f.write_bytes("one\r\ntwo\r\nthree\r\n".encode("utf-8"))

    run(EditFileTool().execute("a.txt", "two", "TWO"))

    assert f.read_bytes() == "one\r\nTWO\r\nthree\r\n".encode("utf-8")

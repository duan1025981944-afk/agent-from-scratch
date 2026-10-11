"""data/ 禁区的测试。对应第 24 讲（下）。

禁区规则：**写入类工具不能碰 Agent 自己的 data/，读取类工具可以。**
禁区是一个固定的完整路径（workspace.NO_WRITE_DIRS），不跟着工作区走。
理由见 agent/tools/workspace.py 文件头"围栏里还有一个禁区"那一节。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.tools import workspace
from agent.tools.list_dir import ListDirTool
from agent.tools.read_file import ReadFileTool
from agent.tools.workspace import OutsideWorkspace, ProtectedPath
from agent.tools.write_file import WriteFileTool


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把工作区和禁区都指到临时目录，并造出一份 data/ 现场。

    相当于"在项目自己的目录里启动"：Agent 的 data/ 就在工作区里面。

    monkeypatch 会在测试结束后自动把 _root 还原，
    所以不会影响别的测试（它们也各自 set_root 自己的目录）。
    """
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())
    monkeypatch.setattr(workspace, "NO_WRITE_DIRS", (tmp_path.resolve() / "data",))

    memory = tmp_path / "data" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("- 用户的项目代号是 蓝鲸-7\n", encoding="utf-8")

    archive = tmp_path / "data" / "sessions" / "20260917-120000.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text('{"role": "user", "content": "你好"}\n', encoding="utf-8")

    return tmp_path


def test_write_file_cannot_touch_memory(sandbox):
    """★ 不变量 6：主 Agent 不能改长期记忆。

    这是本讲的核心。第 24 讲（上）实测过：没有这道检查时，
    write_file 会返回"已覆盖……"并且真的改掉文件。

    负对照：write_file 里把 resolve_for_write 改回 resolve → 变成成功回执。
    """
    tool = WriteFileTool()
    result = asyncio.run(tool.execute("data/memory/MEMORY.md", "- 用户的项目代号是 白鲸-9"))

    assert "不能写入" in result
    # 文件内容原封不动——光看回执不够，要确认磁盘没被动过
    memory = sandbox / "data" / "memory" / "MEMORY.md"
    assert "蓝鲸-7" in memory.read_text(encoding="utf-8")


def test_write_file_cannot_touch_session_archive(sandbox):
    """存档同样受保护：不变量 1 说"存档存全文"，那就不能让模型自己改。

    负对照：把 sandbox 里的禁区改成 tmp_path / "data" / "memory" → 存档这条红。
    """
    tool = WriteFileTool()
    result = asyncio.run(tool.execute("data/sessions/20260917-120000.jsonl", "{}"))

    assert "不能写入" in result


def test_dotdot_tricks_are_caught(sandbox):
    """绕路的写法也拦得住——因为禁区检查在路径展开**之后**做。

    负对照：把禁区检查挪到 resolve() 之前（先拿原始字符串比对）→ 绝对路径那条漏掉，本测试红。
    """
    绕路写法 = [
        "data/../data/memory/MEMORY.md",
        "./data/memory/../memory/MEMORY.md",
        str(sandbox / "data" / "memory" / "MEMORY.md"),   # 绝对路径
    ]
    for path in 绕路写法:
        with pytest.raises(ProtectedPath):
            workspace.resolve_for_write(path)


def test_normal_places_still_writable(sandbox):
    """禁区只挡 data/，别的地方照常能写——别把工具废掉了。

    负对照：把 sandbox 里的禁区改成 tmp_path 本身 → 整个工作区都被挡，这条红。
    """
    tool = WriteFileTool()
    result = asyncio.run(tool.execute("notes.md", "随手记一点东西"))

    assert "已创建" in result
    assert (sandbox / "notes.md").exists()


def test_reading_data_is_still_allowed(sandbox):
    """读禁区是允许的：让模型翻自己的旧记录有用，风险也小得多。

    负对照：让 read_file 也改用 resolve_for_write → 这条红。
    """
    read = asyncio.run(ReadFileTool().execute("data/memory/MEMORY.md"))
    assert "蓝鲸-7" in read

    listing = asyncio.run(ListDirTool().execute("data/memory"))
    assert "MEMORY.md" in listing


def test_protected_and_outside_are_different_errors(sandbox):
    """两种拒绝原因不同，异常也要不同——模型据此决定是换路径还是别重试。

    负对照：让 resolve_for_write 抛 OutsideWorkspace 而不是 ProtectedPath → 这条红。
    """
    with pytest.raises(ProtectedPath):
        workspace.resolve_for_write("data/memory/MEMORY.md")

    with pytest.raises(OutsideWorkspace):
        workspace.resolve_for_write("../../etc/passwd")

    # ProtectedPath 不是 OutsideWorkspace 的子类，不能被一起 except 掉当成同一件事
    assert not issubclass(ProtectedPath, OutsideWorkspace)


def test_other_projects_data_dir_is_writable(tmp_path, monkeypatch):
    """在别的项目里干活时，那个项目自己的 data/ 不是禁区。

    负对照：把禁区改回"工作区下的 data" → 被拒绝，本测试红。
    """
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())     # 工作区 = 一个和 Agent 无关的文件夹

    result = asyncio.run(WriteFileTool().execute("data/report.csv", "a,b\n1,2\n"))

    assert "已创建" in result
    assert (tmp_path / "data" / "report.csv").exists()


def test_agent_data_is_protected_when_workspace_is_a_parent(tmp_path, monkeypatch):
    """工作区是 Agent 目录的上一级时，Agent 的 data/ 也在工作区里——照样不能写。

    旧规则（"工作区下的 data"）在这里是个洞：它保护的是 上级/data，真存档没人管。

    负对照：把 resolve_for_write 里的 is_relative_to(protected) 改成 is_relative_to(get_root() / "data")
    → 不抛 ProtectedPath，本测试红。
    """
    home = tmp_path / "agent_home"
    (home / "data" / "memory").mkdir(parents=True)
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())
    monkeypatch.setattr(workspace, "NO_WRITE_DIRS", (home.resolve() / "data",))

    with pytest.raises(ProtectedPath):
        workspace.resolve_for_write("agent_home/data/memory/MEMORY.md")

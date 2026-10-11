"""桥接层的测试。对应第 40 讲 40.5：启动时连上 MCP server、连不上的跳过、查重名。

这几个测试会真的起 server 子进程（用 mcp_servers/toy：它不需要数据库），每个大约 1 秒。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest

from agent.mcp_bridge import connect_mcp_servers, merge_tools
from agent.tools.echo import EchoTool
from config.schema import MCPServerConfig

TOY = str(Path(__file__).resolve().parent.parent / "mcp_servers" / "toy" / "server.py")


def test_tools_still_work_after_connect_returns(tmp_path):
    """连上以后，点菜卡在 connect_mcp_servers 返回之后照样能用：连接交给了大盒子，没有提前关。

    负对照：删掉 _connect_one 里 await stack.enter_async_context(server_stack.pop_all()) 那一行
    → 小盒子出 async with 时就把连接关了，点菜失败，本测试红。
    """
    async def run():
        async with AsyncExitStack() as stack:
            tools = await connect_mcp_servers(stack, [MCPServerConfig("toy", sys.executable, [TOY])], tmp_path)
            return sorted(tools), await tools["mcp_toy_add"].execute(a=2, b=3)

    names, result = asyncio.run(run())
    assert names == ["mcp_toy_add", "mcp_toy_send_email"]
    assert result == "5"


def test_broken_server_is_skipped_loudly(tmp_path, capsys):
    """起不来的 server 跳过，其余照常；跳过时把 server 的遗言（它的 stderr）打印出来。

    负对照：去掉 connect_mcp_servers 里的 try/except → 异常一路冒出去，本测试红。
    """
    broken = tmp_path / "broken.py"
    # reconfigure：Windows 上子进程的 stderr 写文件时默认不是 UTF-8，不设的话中文读回来是乱码
    broken.write_text(
        'import sys\nsys.stderr.reconfigure(encoding="utf-8")\nsys.exit("我起不来了：缺一个文件")\n',
        encoding="utf-8",
    )
    servers = [
        MCPServerConfig("broken", sys.executable, [str(broken)]),
        MCPServerConfig("toy", sys.executable, [TOY]),
    ]

    async def run():
        async with AsyncExitStack() as stack:
            return sorted(await connect_mcp_servers(stack, servers, tmp_path))

    names = asyncio.run(run())
    out = capsys.readouterr().out
    assert names == ["mcp_toy_add", "mcp_toy_send_email"]
    assert "broken 起不来，已跳过" in out
    assert "我起不来了：缺一个文件" in out


def test_merge_rejects_name_clash():
    """MCP 工具和自带工具撞名：当场报错，不默默覆盖。

    负对照：删掉 merge_tools 里的查重，直接 return dict(sorted({**builtin, **mcp}.items())) → 不报错，本测试红。
    """
    with pytest.raises(ValueError, match="echo"):
        merge_tools({"echo": EchoTool()}, {"echo": EchoTool()})



def test_log_dir_does_not_depend_on_where_you_start():
    """server 日志放在 Agent 自己的 data/ 下，不会撒到你干活的那个项目里。

    负对照：把 LOG_DIR 改回 Path("data") / "mcp_logs" → 是相对路径，本测试红。
    """
    from agent.mcp_bridge import LOG_DIR

    assert LOG_DIR.is_absolute()

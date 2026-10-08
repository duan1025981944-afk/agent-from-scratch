"""点菜时出事了：超时、server 中途死掉。对应第 40 讲 40.6。

用一个专门的"脆弱 server"：ping 马上回，slow 要睡 5 秒，die 当场退出。
它写在临时目录里，每个测试现起一个，大约 1–3 秒。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
import asyncio
import sys
import time
from contextlib import AsyncExitStack

from mcp import ClientSession

import agent.mcp_bridge as bridge
from agent.mcp_bridge import connect_mcp_servers
from config.schema import MCPServerConfig

FRAGILE = '''
import os, sys, time
sys.stderr.reconfigure(encoding="utf-8")
from mcp.server.mcpserver import MCPServer

server = MCPServer("fragile")

@server.tool()
def ping() -> str:
    """马上回一个 pong。"""
    return "pong"

@server.tool()
def slow() -> str:
    """睡 5 秒才回，用来试超时。"""
    time.sleep(5)
    return "终于好了"

@server.tool()
def die() -> str:
    """当场退出，模拟 server 中途死掉。"""
    os._exit(1)

server.run()
'''


def fragile_server(tmp_path) -> MCPServerConfig:
    script = tmp_path / "fragile.py"
    script.write_text(FRAGILE, encoding="utf-8")
    return MCPServerConfig("fragile", sys.executable, [str(script)])


def test_timeout_says_not_sure(tmp_path, monkeypatch):
    """超时：交回一张纸条，说"不确定有没有执行"，而不是让异常冒出去（38.5）。

    只把"点菜"这一步的超时缩成 1 秒，握手、要菜单还是照常的 30 秒。
    以前是把 bridge.TIMEOUT 整个改成 1，可 TIMEOUT 管着每一条请求，握手也算；
    握手本身就要 0.6–0.8 秒（沙箱实测：server 得先启动 Python、import mcp，才能回话）。
    冷启动稍慢一点，就先在握手超时，server 被跳过，测试报 KeyError，
    测到的根本不是我们想测的"点菜超时"。

    负对照：删掉 MCPTool._explain 里 REQUEST_TIMEOUT 那个分支 → 纸条变成"回了一个错误"，没有"不确定"，本测试红。
    """
    real_call_tool = ClientSession.call_tool

    async def call_tool_waits_1s(self, name, arguments=None, read_timeout_seconds=None, **kwargs):
        # SDK 的 call_tool 可以单独给这一次请求定超时；这里一律改成 1 秒，不想真等 30 秒
        return await real_call_tool(self, name, arguments, read_timeout_seconds=1, **kwargs)

    monkeypatch.setattr(ClientSession, "call_tool", call_tool_waits_1s)

    async def run():
        async with AsyncExitStack() as stack:
            tools = await connect_mcp_servers(stack, [fragile_server(tmp_path)], tmp_path)
            return await tools["mcp_fragile_slow"].execute()

    result = asyncio.run(run())
    assert result.is_error
    assert "不确定" in result


def test_dead_server_is_reported_and_later_calls_fail_fast(tmp_path, monkeypatch):
    """server 中途死掉：这一次和之后每一次点菜，都马上交回"已经断开"的纸条，不会干等到超时。

    负对照：删掉 MCPTool._explain 里 CONNECTION_CLOSED 那个分支 → 纸条里没有"已经断开"，本测试红。
    """
    monkeypatch.setattr(bridge, "TIMEOUT", 10)         # 万一干等，也只等 10 秒

    async def run():
        async with AsyncExitStack() as stack:
            tools = await connect_mcp_servers(stack, [fragile_server(tmp_path)], tmp_path)
            first = await tools["mcp_fragile_die"].execute()
            t0 = time.perf_counter()
            second = await tools["mcp_fragile_ping"].execute()
            return first, second, time.perf_counter() - t0

    first, second, seconds = asyncio.run(run())
    assert first.is_error and "已经断开" in first
    assert second.is_error and "已经断开" in second
    assert seconds < 3

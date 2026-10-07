"""MCP 工具：把 MCP server 菜单上的一道菜，包成 mini_nanobot 的一个工具。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
桥接层的第 2 步（第 40 讲 40.2）。一个通用的工具类 MCPTool，像一个模具：
连上 server、要来菜单以后，菜单上有几道菜，就用它造几个工具对象。

    菜单上的一道菜（MCP）          造出来的工具对象（mini_nanobot）
    name = "get_order"        →   name = "mcp_xingchen_db_get_order"
    description               →   description（原样）
    input_schema              →   parameters（原样：两边都是 JSON Schema）
    （没有）                   →   execute()：把单子转给 server

同一个 server 造出来的工具对象，共用同一个会话（ClientSession）。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不连 server：会话由 agent/mcp_bridge.py 连好了再传进来（40.5）
- 它不让 loader 自动登记：auto_discover = False（要会话和菜单才造得出来，40.2）
- 它不自动重连：server 中途断了，就照实告诉模型（40.6）

──────────────────────────────────────────────────────────────
名字为什么要带上 server 名（40.2）
──────────────────────────────────────────────────────────────
不同 server 可能有同名的菜；server 的菜也可能和自带工具撞名
（官方的 filesystem server 就有一道 write_file）。
带上 mcp_<server 名>_ 前缀就撞不上了。点菜时再用回原名（original_name）。

──────────────────────────────────────────────────────────────
为什么叫 mcp_tool.py，不叫 mcp.py
──────────────────────────────────────────────────────────────
下面 import 的第三方包就叫 mcp。自己的文件也叫 mcp.py 的话，一旦这个目录
进了 sys.path（比如直接运行这个文件），import mcp 找到的就是它自己，
报一个很难看懂的 "circular import"（沙箱里试过）。换个名字，省得踩坑。

──────────────────────────────────────────────────────────────
点菜时出事了（40.6）
──────────────────────────────────────────────────────────────
一律变成写给模型的纸条（ToolResult.error），不往外抛：
    超时              SDK 抛 MCPError，code = REQUEST_TIMEOUT（-32001）
                      → 告诉模型"不确定有没有执行"，而不是"失败了"（38.5）
    server 断开了      SDK 抛 MCPError，code = CONNECTION_CLOSED（-32000）
                      断开以后再点菜，也是马上抛这个，不会干等
                      → 告诉模型"这个 server 的工具暂时用不了"
    server 回了错误    其他 MCPError（JSON-RPC 的 error）→ 把 server 的原话交给模型（36.6）

为什么不自动重连：重连能做（nanobot 会重连一次再重试，mcp.py 第 522 行），
但"再点一次"可能让同一件事做两遍（38.5）；server 要是因为这次的参数崩的，
重连再点还会崩。mini_nanobot 先照实说，让模型和用户决定。
"""
from __future__ import annotations

import re
from typing import Any

from mcp import ClientSession, MCPError
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT
from mcp.types import Tool as MCPToolDef

from agent.tools.base import Tool, ToolResult

# 模型那边对工具名的规矩：只能用字母、数字、下划线、减号。
# 长度上限各家不一样：DeepSeek 文档写的是 128；nanobot 一律截到 64，好兼容更严的服务商。我们也截到 64。
_ILLEGAL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
MAX_NAME_LENGTH = 64


def wrapped_name(server_name: str, tool_name: str) -> str:
    """给模型看的工具名：mcp_<server 名>_<菜名>。

    不合规矩的字符换成下划线，超长的截到 64 个字符。
    名字不合规矩的话，服务商可能直接拒收整个请求（连带所有工具），所以这里宁可改名。

    例子
        >>> wrapped_name("xingchen_db", "get_order")
        'mcp_xingchen_db_get_order'
        >>> wrapped_name("my.server", "read file")
        'mcp_my_server_read_file'
    """
    return _ILLEGAL_CHARS.sub("_", f"mcp_{server_name}_{tool_name}")[:MAX_NAME_LENGTH]


class MCPTool(Tool):
    """MCP server 菜单上的一道菜，包成 mini_nanobot 的一个工具。

    谁会产生它
        agent/mcp_bridge.py：连上一个 server、list_tools() 之后，每道菜造一个（40.5）。
        scripts/mcp_tools_demo.py 也手动造过，用来单独验证它（40.2）。

    谁会调它
        和自带工具一样：runner 用 await tool.execute(**模型给的参数) 调。

    和自带工具的区别
        自带工具的 name / description / parameters 是写死的类属性；
        MCPTool 的这三样是造对象时从菜单上抄来的，每个对象各不相同。
        execute 自己不干活，只是把单子转给 server。
    """

    # loader 别来空手造我：我要等连上 server、拿到菜单以后才造得出来（40.2）
    auto_discover = False

    def __init__(self, session: ClientSession, server_name: str, tool_def: MCPToolDef) -> None:
        """照着菜单上的一道菜，造一个工具对象。

        传入什么
            session:     通往 server 的会话，已经握过手。同一个 server 的工具共用一个
            server_name: 这个 server 的名字，比如 "xingchen_db"
            tool_def:    session.list_tools() 拿回来的一道菜
        """
        self.session = session
        self.server_name = server_name
        self.original_name = tool_def.name                         # 菜单上的原名：点菜时用它
        self.name = wrapped_name(server_name, tool_def.name)       # 给模型看的名字：带上 server 名
        self.description = tool_def.description or tool_def.name   # 菜单上没写说明，就拿菜名顶上
        self.parameters = tool_def.input_schema                    # 原样搬：两边都是 JSON Schema

    async def execute(self, **kwargs: Any) -> str:
        """把模型给的参数原样转给 server，再把 server 的回复变成一段文字。

        传入什么
            模型给的参数，被 runner 拆成关键字参数传进来。这里不检查：
            server 会按菜单验证（第 37 讲），没通过就回 isError，照样变成纸条。

        返回什么
            成功：server 回的文字
            server 说没做成（isError）：ToolResult.error(server 的原话)
            超时、断开、server 回了错误：ToolResult.error(写给模型的说明)（40.6）
        """
        try:
            # ★ 点菜要用原名。用带前缀的 self.name，server 会说 Unknown tool（负对照 M2）
            result = await self.session.call_tool(self.original_name, kwargs)
        except MCPError as exc:
            return ToolResult.error(self._explain(exc))

        # 回复里是一块一块的内容（content）。只认文字块，拼成一段交给模型
        parts = []
        for block in result.content:
            if block.type == "text":
                parts.append(block.text)
            else:
                parts.append(f"（server 回了一块 {block.type} 类型的内容，这里只支持文字）")
        text = "\n".join(parts) or "（server 没有返回任何内容）"

        # is_error（线路上叫 isError）= server 说"这道菜没做成"。
        # 那段话本来就是写给模型看的纸条（第 37 讲），原样交过去
        if result.is_error:
            return ToolResult.error(text)
        return text

    def _explain(self, exc: MCPError) -> str:
        """把点菜时的 MCPError 翻译成写给模型的话。"""
        if exc.code == REQUEST_TIMEOUT:
            # 38.5：超时不等于失败。server 可能已经做完了，只是没来得及回
            return (
                f"调用超时：{self.server_name} 一直没有回复，已经不等了。"
                "不确定它那边有没有执行完。如果这个工具会改东西（比如发邮件、下单），"
                "先跟用户确认结果，别直接重试。"
            )
        if exc.code == CONNECTION_CLOSED:
            return (
                f"{self.server_name} 已经断开（server 进程可能退出了），它的工具暂时都用不了，"
                "这次调用也不确定有没有执行。请告诉用户：重启 Agent 才能重新连上。"
            )
        return f"{self.server_name} 回了一个错误：{exc.message}"

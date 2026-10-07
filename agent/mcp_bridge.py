"""桥接层：照着 config.json 连上 MCP server，把菜单上的菜造成点菜卡（第 40 讲）。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
main.py 启动时调一次 connect_mcp_servers()。对每个 server：
    ① 先在它自己的小盒子里试着连：起进程、握手、要菜单、造点菜卡
    ② 连上了：把小盒子整个放进 main.py 的大盒子，连接一直活到退出（40.3）
    ③ 连不上：小盒子当场收拾干净；打印原因和 server 日志的最后几行；跳过它，接着连下一个

然后 merge_tools() 把造好的卡和自带工具放在一起，查一遍重名。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不是工具：点菜卡是 agent/tools/mcp_tool.py 里的 MCPTool
- 它不读配置：由 config/loader.py 的 load_mcp_servers() 读好了传进来
- 它不管点菜时的超时、server 中途死掉：那是 MCPTool.execute 的事（40.6）

──────────────────────────────────────────────────────────────
起不来的 server 跳过，而不是让整个 Agent 起不来（40.5）
──────────────────────────────────────────────────────────────
server 是外面的东西：库没建、网断了、npx 下载失败……都可能让它起不来。
不能因为一个挂掉，就让所有工具都用不了。但跳过时要大声：打印原因和 server 的日志。

配置本身写错（缺 command、工具重名）不一样：那是我们自己的错，改一下就好，
所以照样当场报错（code-review 技能第 2 条：启动期的错误要大声）。
"""
from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.tools.base import Tool
from agent.tools.mcp_tool import MCPTool
from config.schema import MCPServerConfig

# 每个 server 的 stderr 写进 data/mcp_logs/<名字>.log。
# 不交给终端：Windows 上会丢（40.1 的 K4）；也省得 server 的日志混进对话里。
# data/ 在 .gitignore 里，日志不会被提交。
LOG_DIR = Path("data") / "mcp_logs"

# 每条请求最多等几秒：握手、要菜单、点菜都算（会话级的默认值）。
# nanobot 每个 server 的默认值也是 30 秒（nanobot/config/schema.py 第 373 行）。
TIMEOUT = 30


async def connect_mcp_servers(
    stack: AsyncExitStack,
    servers: list[MCPServerConfig],
    log_dir: Path = LOG_DIR,
) -> dict[str, Tool]:
    """连上所有配置好的 server，返回 {工具名: 点菜卡}。连不上的跳过。

    传入什么
        stack:   main.py 的大盒子。连上的 server 都放进去，退出时由它倒着关
        servers: load_mcp_servers() 的结果
        log_dir: server 日志放哪

    会抛什么
        ValueError：两个工具撞名。这是配置问题（给某个 server 换个名字就好），所以当场报错
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    tools: dict[str, Tool] = {}
    for server in servers:
        log_path = log_dir / f"{server.name}.log"
        try:
            server_tools = await _connect_one(stack, server, log_path)
        except Exception as exc:
            reason = _innermost(exc)
            print(f"[MCP] {server.name} 起不来，已跳过：{type(reason).__name__}: {reason}")
            for line in _tail(log_path):
                print(f"      | {line}")          # server 自己的遗言（它的 stderr）
            continue

        for tool in server_tools:
            if tool.name in tools:
                raise ValueError(
                    f"MCP 工具名撞了：{tool.name}。"
                    f"{server.name} 和前面某个 server 拼出了同一个名字，给其中一个 server 换个名字。"
                )
            tools[tool.name] = tool
        print(f"[MCP] {server.name}：连上了，{len(server_tools)} 个工具")
    return tools


async def _connect_one(stack: AsyncExitStack, server: MCPServerConfig, log_path: Path) -> list[MCPTool]:
    """连一个 server：先在它自己的小盒子里连，全连上了，再把小盒子交给大盒子。"""
    async with AsyncExitStack() as server_stack:
        errlog = server_stack.enter_context(log_path.open("w", encoding="utf-8"))
        params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
        read, write = await server_stack.enter_async_context(stdio_client(params, errlog=errlog))
        session = await server_stack.enter_async_context(
            ClientSession(read, write, read_timeout_seconds=TIMEOUT)
        )
        await session.initialize()
        page = await session.list_tools()
        tools = [MCPTool(session, server.name, tool_def) for tool_def in page.tools]

        # 走到这里说明全连上了。pop_all()：把小盒子里的东西整个挪进一个新盒子交出去，
        # 小盒子自己变空，出 async with 时什么都不关；这些连接改由大盒子在退出时关。
        # 要是上面任何一步出错，就走不到这里：小盒子出 async with 时把这个 server 收拾干净
        await stack.enter_async_context(server_stack.pop_all())
    return tools


def merge_tools(builtin: dict[str, Tool], mcp: dict[str, Tool]) -> dict[str, Tool]:
    """自带工具和 MCP 工具合成一张表，按名字排好序（和 loader 一样，顺序稳定好命中提示缓存）。

    会抛什么
        ValueError：撞名。和 loader 一样不默默覆盖——覆盖了会"明明有工具却调不到"

    例子
        >>> from agent.tools.echo import EchoTool
        >>> list(merge_tools({"echo": EchoTool()}, {}))
        ['echo']
    """
    clash = builtin.keys() & mcp.keys()
    if clash:
        raise ValueError(f"MCP 工具和自带工具撞名了：{sorted(clash)}")
    return dict(sorted({**builtin, **mcp}.items()))


def _innermost(exc: BaseException) -> BaseException:
    """把 ExceptionGroup 一层层剥开，找到最里面那个真正的异常（40.1 的 K3：外面包了两层）。"""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _tail(path: Path, lines: int = 5) -> list[str]:
    """server 日志的最后几行。读不到就返回空列表：报错的路上别再炸一次。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.strip().splitlines()[-lines:]

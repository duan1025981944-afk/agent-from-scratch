"""连接的寿命：B（启动时连上，一直开着）和 C（每次点菜现连现断），实测对比。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 40 讲 40.3 的验证脚本。
    B：用 AsyncExitStack 把所有 server 连上、造好全部工具，之后点菜只走现成的连接
    C：每点一次菜，都从头连一次 server、点完就断（mcp_sdk.py 那样一口气）
两种做法都点 3 次 get_order，比一比各花多少时间。

AsyncExitStack 是什么：一个盒子，可以往里放任意多个 async with。
盒子关上时，里面的东西倒着一个个关掉（后开的先关）。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不属于 Agent：一次性的观察工具。真正接进 main.py 的是 agent/mcp_bridge.py（40.5）
- SERVERS 是写死的：当时先假装它是从配置里读出来的（配置见 40.4）

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python -m scripts.mcp_lifetime_demo

要 import agent，所以必须用 -m（理由同 check_tokens.py）。
"""
import asyncio
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.tools.mcp_tool import MCPTool

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path(tempfile.gettempdir())   # 每个 server 的 stderr 写进这里的 mcp_<名字>.log（K4）

# 假装这是从配置里读出来的：有几个 server，写代码时并不知道
SERVERS = {
    "toy": ROOT / "mcp_servers" / "toy" / "server.py",
    "xingchen_db": ROOT / "mcp_servers" / "xingchen_db" / "server.py",
}
CALLS = 3   # 每种做法都点 3 次 get_order


async def connect(stack: AsyncExitStack, name: str, script: Path) -> ClientSession:
    """连上一个 server。日志文件、管子、会话都放进盒子，由盒子负责最后关掉。"""
    # enter_context：放一个普通的 with（打开的文件）进盒子
    errlog = stack.enter_context((LOG_DIR / f"mcp_{name}.log").open("w", encoding="utf-8"))
    params = StdioServerParameters(command=sys.executable, args=[str(script)])
    # enter_async_context：放一个 async with 进盒子。拿回来的东西，和 "async with ... as" 拿到的一样
    read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    # callback：盒子关上时顺便执行一下。用它看看谁先被关
    stack.callback(print, f"  [打烊] 关掉 {name}")
    return session


async def plan_b() -> None:
    """B：启动时把所有 server 连上、造好全部工具；之后点菜只走现成的连接。"""
    print("== B：启动时连上，一直开着 ==")
    t0 = time.perf_counter()
    async with AsyncExitStack() as stack:
        tools = {}
        for name, script in SERVERS.items():          # 有几个 server 就连几个，代码不用改
            session = await connect(stack, name, script)
            for tool_def in (await session.list_tools()).tools:
                tool = MCPTool(session, name, tool_def)
                tools[tool.name] = tool
        print(f"  开门：连上 {len(SERVERS)} 个 server、造好 {len(tools)} 个工具，用了 {time.perf_counter() - t0:.1f} 秒")
        print("  工具：", ", ".join(tools))

        t1 = time.perf_counter()
        for _ in range(CALLS):
            await tools["mcp_xingchen_db_get_order"].execute(order_id="XC1024")
        print(f"  点 {CALLS} 次 get_order：一共 {time.perf_counter() - t1:.2f} 秒")
        print("  toy 的 add(a=2, b=3)：", await tools["mcp_toy_add"].execute(a=2, b=3))
    # 走到这里，盒子已经关上：上面会先打印 xingchen_db、再打印 toy（后开的先关）
    print(f"  B 从开门到打烊：{time.perf_counter() - t0:.1f} 秒")


async def plan_c() -> None:
    """C：每次点菜都从头连一次 server，点完就断。"""
    print("\n== C：每次点菜现连现断 ==")
    t0 = time.perf_counter()
    for i in range(CALLS):
        t = time.perf_counter()
        async with AsyncExitStack() as stack:
            session = await connect(stack, "xingchen_db", SERVERS["xingchen_db"])
            await session.call_tool("get_order", {"order_id": "XC1024"})
        print(f"  第 {i + 1} 次点菜：{time.perf_counter() - t:.1f} 秒")
    print(f"  C 点 {CALLS} 次 get_order：一共 {time.perf_counter() - t0:.1f} 秒")


async def main() -> None:
    await plan_b()
    await plan_c()


if __name__ == "__main__":
    asyncio.run(main())

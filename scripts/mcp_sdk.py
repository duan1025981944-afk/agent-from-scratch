"""用 SDK 的客户端连一个 MCP server：握手、要菜单、点一道菜。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 40 讲。和 mcp_raw.py 做同样的四件事，只是这次不亲手 write / readline，
全交给 SDK 的客户端：
    ① 把 mcp_servers/<SERVER_NAME>/server.py 当成子进程启动
    ② 握手
    ③ 要菜单
    ④ 点菜
桥接层（agent/mcp_bridge.py，40.5）用的就是这里的几行，先单独拿出来看清楚。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不属于 Agent：和 scripts/ 下其他脚本一样，是一次性的观察工具
- 它不处理异常：server 起不来就让它炸，炸了正好看（实验 K3）

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python scripts/mcp_sdk.py

──────────────────────────────────────────────────────────────
和 mcp_raw.py 的对照
──────────────────────────────────────────────────────────────
    mcp_raw.py                          这个文件
    Popen([...]) 的参数                  StdioServerParameters(command, args)
    Popen + 帮手线程 + 队列              stdio_client(params)        管子
    send() / receive() + 手写单号        ClientSession(read, write)  会话
    手写 initialize 那两条消息           await session.initialize()
    手写 tools/list                      await session.list_tools()
    手写 tools/call                      await session.call_tool(...)
    stdin.close() + wait()               走出两层 async with
    stderr=PIPE，收工时读一遍            errlog=日志文件，收工时读回来（K4）

──────────────────────────────────────────────────────────────
server 的 stderr（K4）
──────────────────────────────────────────────────────────────
stdio_client 默认把 server 的 stderr 接到我们自己的 stderr（终端）上。
沙箱（Linux）里这样能看到；你的 Windows 上，K2、K3 却一个字都没显示。
所以这里改成交给它一个日志文件（errlog=），不管成功还是炸了，最后都读回来打印。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 实验开关
SERVER_NAME = "xingchen_db"
TOOL_NAME = "get_order"
ARGUMENTS = {"order_id": "XC1024"}

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_servers" / SERVER_NAME / "server.py"

# K4：server 的 stderr 写进这个文件。放在系统临时目录，不弄脏仓库；每次运行先清空。
ERRLOG = Path(tempfile.gettempdir()) / "mcp_sdk_stderr.log"


async def talk(errlog: TextIO) -> None:
    """管子 + 会话：握手、要菜单、点菜。server 的 stderr 写进 errlog。"""
    # ① 怎么把 server 跑起来：命令 + 参数，和 mcp_raw.py 里 Popen 的那个列表是同一回事。
    #    sys.executable = 正在跑这个脚本的 python（.venv 里那个），不依赖全局有没有 python。
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])

    # ② 管子：stdio_client 启动子进程，交回两根"流"——read 收 server 说的话，write 往 server 说话。
    #    进了这个 async with，server 就跑起来了；出了它，server 就被收掉。
    #    errlog：server 的 stderr 往哪写（K4）。不传的话，默认是我们自己的 stderr，也就是终端。
    async with stdio_client(params, errlog=errlog) as (read, write):

        # ③ 会话：ClientSession 在两根流上说 JSON-RPC——编单号、对单号、等回复，都归它管。
        async with ClientSession(read, write) as session:

            # 握手：initialize 请求 + notifications/initialized 通知，一行搞定
            init = await session.initialize()
            print("server：", init.server_info.name, "| 协议版本：", init.protocol_version)

            # 要菜单：tools/list。拿回来的是 Python 对象，不是 dict
            page = await session.list_tools()
            for tool in page.tools:
                # ★ 线路上叫 inputSchema，Python 里叫 input_schema（SDK v2 一律用下划线的写法）
                print("菜单上：", tool.name, "| 参数：", list(tool.input_schema["properties"]))

            # 点菜：tools/call
            result = await session.call_tool(TOOL_NAME, ARGUMENTS)
            print("\nis_error：", result.is_error)     # ★ 线路上叫 isError
            for block in result.content:
                print(block.text)

    # 走到这里：两层 async with 都出来了，会话关了，server 子进程也收掉了


async def main() -> None:
    with ERRLOG.open("w", encoding="utf-8") as errlog:
        try:
            await talk(errlog)
        finally:
            # 不管成功还是炸了，都把 server 写的 stderr 读回来看一眼。
            # errors="replace"：server 万一没按 UTF-8 写，也别让这一步再炸一次。
            text = ERRLOG.read_text(encoding="utf-8", errors="replace").strip()
            print(f"\n[server 的 stderr，来自 {ERRLOG}]")
            print(text or "（空）")


if __name__ == "__main__":
    asyncio.run(main())

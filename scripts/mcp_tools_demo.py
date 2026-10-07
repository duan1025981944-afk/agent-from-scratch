"""照着 xingchen_db 的菜单造出 MCPTool 工具对象，再像 runner 那样调用它们。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 40 讲 40.2 的验证脚本。做三件事：
    ① 连上 xingchen_db（和 mcp_sdk.py 一样的两层 async with）
    ② 菜单上有几道菜，就用 MCPTool 造几个工具对象
    ③ 像 runner 那样 await tool.execute(**参数)，看拿回来的是什么

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不属于 Agent：一次性的观察工具。真正接进 main.py 的是 agent/mcp_bridge.py（40.5）
- 它不调模型：参数是写死的，只验证"工具对象"这一段

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python -m scripts.mcp_tools_demo

**必须用 -m**，理由和 check_tokens.py 一样：要从项目根目录 import agent。
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.tools.mcp_tool import MCPTool

# 实验开关：改成 True = 故意犯错，点菜时用带前缀的名字（负对照 M2）
CALL_WITH_WRAPPED_NAME = False

SERVER_NAME = "xingchen_db"
ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_servers" / SERVER_NAME / "server.py"
ERRLOG = Path(tempfile.gettempdir()) / "mcp_tools_demo_stderr.log"   # server 的 stderr（K4）


def short(text: str, limit: int = 120) -> str:
    """长结果只看个开头，换行压成空格。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + " ……"


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    with ERRLOG.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                page = await session.list_tools()

                # ★ 一个模具：菜单上有几道菜，就造几个工具对象。它们共用同一个 session
                tools = {}
                print("== 造出来的工具 ==")
                for tool_def in page.tools:
                    tool = MCPTool(session, SERVER_NAME, tool_def)
                    tools[tool.name] = tool
                    same = tool.to_schema()["function"]["parameters"] == tool_def.input_schema
                    print(f"{tool.name:28s} 原名 {tool.original_name:12s} parameters 和菜单一样：{same}")

                get_order = tools["mcp_xingchen_db_get_order"]
                if CALL_WITH_WRAPPED_NAME:
                    get_order.original_name = get_order.name   # 负对照：模拟"忘了换回原名"

                # 像 runner 一样调用：模型给的参数拆成关键字参数传进去
                for order_id in ("XC1024", "XC9999"):
                    result = await get_order.execute(order_id=order_id)
                    print(f"\n== get_order.execute(order_id={order_id!r}) ==")
                    print("is_error：", getattr(result, "is_error", False))   # runner 也是这么取的
                    print(short(str(result)))


if __name__ == "__main__":
    asyncio.run(main())

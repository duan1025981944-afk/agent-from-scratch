"""偷看两样东西：盒子（AsyncExitStack）里记了什么，会话（ClientSession）里有什么。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 40 讲 40.3 的观察脚本。连上 xingchen_db，然后：
    ① 打印盒子里的"打烊清单"：每放进去一样东西，清单上就多一行"打烊时怎么关它"
    ② 打印会话里的东西：握手时记下的 server 信息，还有它的"单号本"

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不属于 Agent：一次性的观察工具
- 它偷看了几个下划线开头的内部属性（stack._exit_callbacks、session._dispatcher）。
  只为看清楚，正式代码里别这么用：SDK 升级时它们随时会变

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python -m scripts.mcp_peek
"""
import asyncio
from contextlib import AsyncExitStack
from types import MethodType

from scripts.mcp_lifetime_demo import SERVERS, connect   # 借用 40.3 写好的 connect()


def describe(callback) -> str:
    """把清单上的一行，翻译成"打烊时要调用谁的哪个方法"。"""
    if isinstance(callback, MethodType):                   # 放进来的 with / async with
        return f"{type(callback.__self__).__name__}.{callback.__func__.__name__}()"
    wrapped = getattr(callback, "__wrapped__", None)       # stack.callback(...) 放进来的
    return f"callback：{wrapped.__name__}(...)" if wrapped else repr(callback)


def peek_ids(session, label: str) -> None:
    """看一眼单号本：下一个单号快到几了、还有几张单在等回复。"""
    d = session._dispatcher
    print(f"  [{label}] 已经用到第 {d._next_id} 号单，还在等回复的单：{list(d._pending)}")


async def main() -> None:
    async with AsyncExitStack() as stack:
        session = await connect(stack, "xingchen_db", SERVERS["xingchen_db"])

        print("== ① 盒子里的打烊清单（从上往下 = 打烊时的顺序）==")
        for _is_sync, callback in reversed(stack._exit_callbacks):
            print("  ", describe(callback))

        print("\n== ② 会话里：握手时记下的 ==")
        print("  协议版本：", session.protocol_version)
        print("  server：  ", session.server_info.name)
        caps = session.server_capabilities
        print("  能力：    ", "、".join(n for n in ("tools", "resources", "prompts") if getattr(caps, n)))

        print("\n== ② 会话里：单号本 ==")
        peek_ids(session, "握手之后")
        await session.list_tools()
        peek_ids(session, "要过菜单之后")
        await session.call_tool("get_order", {"order_id": "XC1024"})
        peek_ids(session, "点过一道菜之后")

        # 点一道要跑 2 秒的菜（39.8 的 A2：没写终止条件的递归查询），趁它还没回，看一眼
        slow_sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n"
        task = asyncio.create_task(session.call_tool("run_sql", {"sql": slow_sql}))
        await asyncio.sleep(0.5)
        peek_ids(session, "慢菜点了、还没回")
        await task
        peek_ids(session, "慢菜回来以后")

        print("\n== 出了 async with，盒子开始照清单收拾 ==")


if __name__ == "__main__":
    asyncio.run(main())

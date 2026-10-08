"""玩具 MCP server：一个会做加法、还会"发邮件"的小厨房。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 36–38 讲的道具。一个独立的小程序：启动后守着 stdin，
收到一行 JSON 就处理一行，往 stdout 回一行 JSON。
    第 36 讲：看线路上传的是什么
    第 37 讲：看菜单是怎么从函数身上生成的
    第 38 讲：看慢工具、超时和退菜

菜单上有两道菜：
    add          普通的加法，马上出菜
    send_email   模拟发邮件，要 5 秒；故意写成停不下来的同步函数

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不属于 Agent：不 import agent/ 里的任何东西，自己就是一个完整的程序
- 它不用你手动启动：由客户端（scripts/mcp_raw.py、scripts/mcp_cancel.py）当成子进程拉起来
- send_email 不会真的发邮件：它只是往同目录的 outbox.txt 里记一行

──────────────────────────────────────────────────────────────
日志的规矩（第 36 讲插曲三）
──────────────────────────────────────────────────────────────
stdout 是线路，只许走 MCP 消息。日志一律写到 stderr，而且用 UTF-8：
    print("……", file=sys.stderr)
不要用裸 print：它先进缓冲区，退出时才写出去，那时 SDK 已经把 fd 1
接回了线路；而且 Windows 上它用的是系统编码（中文系统是 GBK）。

──────────────────────────────────────────────────────────────
写工具的规矩（第 37、38 讲）
──────────────────────────────────────────────────────────────
1. 每个参数都写类型注解。它既印进菜单，又当门卫检查参数。
   不写的话，菜单上写的是 string，门卫却什么都放行。
2. 工具函数的 docstring 会整段原样发给模型，里面只写给模型看的话；
   给人看的备注写成 # 注释。（模块开头这段说明不会上菜单。）
3. 预料得到的失败，raise ToolError("错在哪、怎么改")：原话会交给模型，
   所以别往里写秘密。其他异常一律按崩溃处理，模型只看到
   "Error executing tool <工具名>"，详情只在 stderr。
4. 会等很久的工具，写成 async def，等待的地方用 await：退菜单来了，
   它能停在 await 那里。普通函数（def）由 SDK 放进另一个线程里跑，
   退菜单来了也停不下来，只能等它做完，结果再被扔掉。
"""
import sys
import time
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

# MCP 规定 stderr 里写 UTF-8。Windows 上 Python 默认用系统编码（中文系统是 GBK），
# 客户端按 UTF-8 读就会报 UnicodeDecodeError。开头改一次，后面所有日志都对。
sys.stderr.reconfigure(encoding="utf-8")

# 厨房的名字。握手时会报给对方（回复里的 serverInfo.name）。
server = MCPServer("toy")


# @server.tool() = 把下面这个函数登记进菜单。
# 菜单上的每一格，都是 SDK 从函数身上抄的（第 37 讲）：
#     name          ← 函数名 add
#     description   ← docstring，整段原样照抄
#     inputSchema   ← 参数的类型注解；Field(description=...) 是每个参数的说明
#     outputSchema  ← 返回值的类型注解 -> int
@server.tool()
def add(
    # Annotated[int, ...] = 类型还是 int，另外再附一点信息（这里附的是参数说明）
    a: Annotated[int, Field(description="第一个加数，整数，例如 2")],
    b: Annotated[int, Field(description="第二个加数，整数，例如 3")],
) -> int:
    """把两个整数加起来。"""
    return a + b


# 假装的"发件箱"：send_email 每发出一封，就在这个文件里记一行（第 38 讲）。
OUTBOX = Path(__file__).resolve().parent / "outbox.txt"


# 第 38 讲的练习对象：一道又慢、又停不下来的菜。
# 故意写成普通函数（def）+ time.sleep：退菜单来了，它照样把邮件"发"出去。
# 想看能停下来的版本，改成 async def + await asyncio.sleep(5)（记得 import asyncio）。
@server.tool()
def send_email(to: Annotated[str, Field(description="收件人的名字，例如 老板")]) -> str:
    """给某人发一封邮件（模拟的，不会真发）。大约要 5 秒。"""
    time.sleep(5)                                   # 假装在连邮件服务器，很慢
    with OUTBOX.open("a", encoding="utf-8") as f:   # 发出去了：在发件箱里记一行
        f.write(f"已发给 {to}\n")
    return f"已发给 {to}"


if __name__ == "__main__":
    # 开门营业：守着 stdin，来一行处理一行，直到 stdin 被关掉。
    server.run()

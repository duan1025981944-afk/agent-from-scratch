"""手写一个最小的 MCP 客户端，只为看清 server 和客户端之间到底传了什么。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一次性的观察工具（和 scripts/ 下其他脚本一样，不属于 Agent）。第 36–39 讲用。

它做四件事，每一步都把"发出去的那一行"和"收回来的那一行"原样打印：
    ① 把 mcp_servers/<SERVER_NAME>/server.py 当成子进程启动（默认是 toy）
    ② 握手：initialize，再发一个通知 notifications/initialized
    ③ 要菜单：tools/list
    ④ 点菜：tools/call

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不用 SDK 的客户端：一切都是亲手 write / readline，好让你看见每一个字
- 它不处理任何异常：出错就让它炸，炸了正好看
- 它不是阶段七的桥接层：桥接层会用 SDK，这里只为看清楚

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python scripts/mcp_raw.py

──────────────────────────────────────────────────────────────
怎么看输出（第 37 讲插曲）
──────────────────────────────────────────────────────────────
    >>> 行                我到底发了什么
    <<< 行                server 回了什么（也就是将来模型会看到的）
    [server 的 stderr]    server 里面发生了什么
结果和预期对不上时，按这个顺序查。

──────────────────────────────────────────────────────────────
超时（第 38 讲）
──────────────────────────────────────────────────────────────
读出菜口的活交给一个帮手（线程 pump），服务员自己看着表从传菜台（队列）取：
每一条回复最多等 TIMEOUT 秒。超时了就不等，关掉 server（proc.kill()）后退出。
"""
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

# 实验开关。做完一个就改回来，再做下一个。
SERVER_NAME = "toy"             # 第 39 讲：改成 "xingchen_db"，连星辰科技数据库 server
SKIP_HANDSHAKE = False          # 第 36 讲：改成 True，不打招呼，直接要菜单、点菜
TOOL_NAME = "add"               # 第 36 讲：改成 "mul"，点一道菜单上没有的菜
ARGUMENTS = {"a": 2, "b": 3}    # 第 37 讲：点菜时交给厨房的参数，比如改成 {"a": 2, "b": "3"}
TIMEOUT = 5                     # 第 38 讲：每一条回复最多等几秒

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_servers" / SERVER_NAME / "server.py"


def send(proc: subprocess.Popen, message: dict) -> None:
    """往 server 的 stdin 写一条消息，顺便打印出来。"""
    line = json.dumps(message, ensure_ascii=False)
    print(f"\n>>> {line}")
    proc.stdin.write(line + "\n")   # ★ 一条消息占一行：换行符就是"这条说完了"
    proc.stdin.flush()              # ★ 不 flush 的话字还堆在缓冲区里，server 收不到


def pump(proc: subprocess.Popen, plates: queue.Queue) -> None:
    """帮手（另一个线程）干的活：守在出菜口闭着眼等，读到一行就放上传菜台。

    出菜口关了（EOF）时，readline() 返回空字符串，也照样放上台子，
    好让服务员知道"厨房没了"。然后帮手下班。
    """
    while True:
        line = proc.stdout.readline()   # 闭着眼等的活，现在交给帮手来干
        plates.put(line)                # 读到的一行放上传菜台（EOF 时放的是空字符串）
        if not line:
            return


def receive(proc: subprocess.Popen, plates: queue.Queue) -> dict:
    """从传菜台取一行，原样打印。最多等 TIMEOUT 秒；读到空的，就去后门看 server 喊了什么。"""
    try:
        # ★ 第 38 讲：看着表等。TIMEOUT 秒内台子上来了东西就拿走，否则抛 queue.Empty。
        line = plates.get(timeout=TIMEOUT)
    except queue.Empty:
        # 表响了，台子还是空的。厨房是慢还是挂了？分不清，也不再等了。
        print(f"<<< （等了 {TIMEOUT} 秒还没有回复：不等了）")
        # ★ 拔掉厨房的电源。不拔的话，挂住的 server 会一直留在后台，
        #   而且它是 CREATE_NO_WINDOW 启动的，你在屏幕上根本看不见它。
        proc.kill()
        proc.wait(timeout=10)
        print("\n[server 的 stderr]")
        print(proc.stderr.read().strip() or "（空）")
        sys.exit(1)
    if not line:
        # 空字符串 = 出菜口已经关了（EOF），厨房不在了。
        # 死因不会写在出菜口，只会写在后门：把 stderr 全倒出来，然后停下。
        proc.wait(timeout=10)
        print(f"<<< （什么都没读到：server 已经退出，退出码 {proc.returncode}）")
        print("\n[server 的 stderr]")
        print(proc.stderr.read().strip() or "（空）")
        sys.exit(1)
    print(f"<<< {line.strip()}")
    return json.loads(line)


def main() -> None:
    # ① 启动 server。
    #    sys.executable = 正在跑这个脚本的那个 python，也就是 .venv 里那个。
    #    这样既不依赖全局有没有 python，也保证 server 能 import 到 mcp。
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,    # 下单口：我们往这里写，server 从它的 stdin 读到
        stdout=subprocess.PIPE,   # 出菜口：server 往它的 stdout 写，我们从这里读到
        stderr=subprocess.PIPE,   # 后门：server 的日志走这里，不掺进正事
        text=True,
        encoding="utf-8",         # MCP 规定用 UTF-8。不写的话 Windows 可能按 GBK 解码，中文会乱
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),   # 学 SDK：server 不挂到当前终端上（插曲二）
    )

    # ★ 第 38 讲：请一个帮手（线程）守在出菜口，读到的每一行都放上传菜台（队列）。
    #   daemon=True：主程序结束时帮手跟着下班，不会拖住程序不让退出。
    plates = queue.Queue()
    threading.Thread(target=pump, args=(proc, plates), daemon=True).start()

    # ② 握手：先报上自己是谁、说哪一版的 MCP
    if not SKIP_HANDSHAKE:
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",                        # 我说哪一版的 MCP
                "capabilities": {},                                     # 客户端这边会什么（这里什么都不声明）
                "clientInfo": {"name": "hand-made", "version": "0.1"},  # 我是谁
            },
        })
        receive(proc, plates)
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})   # 没有 id = 通知，不用回

    # ③ 要菜单
    send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    receive(proc, plates)

    # ④ 点菜
    send(proc, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": TOOL_NAME, "arguments": ARGUMENTS},
    })
    reply = receive(proc, plates)

    # 第 39 讲：把结果里的文字再原样打一遍。线路上那一行里，换行都被转义成了 \n，不好读。
    for block in reply.get("result", {}).get("content", []):
        print(f"\n[content 里的文字]\n{block.get('text', '')}")

    # 收工：关掉 stdin 就等于告诉 server"没有下一条了"，它会自己退出
    proc.stdin.close()
    proc.wait(timeout=10)
    print("\n[server 的 stderr]", proc.stderr.read().strip() or "（空）")


if __name__ == "__main__":
    main()

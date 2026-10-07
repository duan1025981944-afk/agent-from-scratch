"""退菜实验：点一道慢菜，中途把它退掉，看厨房还做不做。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 38 讲的观察工具。和 mcp_raw.py 一样亲手 write / readline，不用 SDK。
每一行前面都标上"开始后第几秒"，好看清先后。

按时间顺序做七件事：
    ① 启动 mcp_servers/toy/server.py，握手
    ② 点一道慢菜 send_email（单号 3），不等它
    ③ 马上点一道快菜 add（单号 4）
    ④ 1 秒后发退菜单 notifications/cancelled，退掉单号 3
    ⑤ 再点一道快菜 add（单号 5）
    ⑥ 再等 8 秒，看单号 3 会不会回来
    ⑦ 看发件箱 outbox.txt：邮件到底发出去没有

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python scripts/mcp_cancel.py
"""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

# 实验开关
SEND_CANCEL = True   # 改成 False：不发退菜单（对照组）

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_servers" / "toy" / "server.py"
OUTBOX = ROOT / "mcp_servers" / "toy" / "outbox.txt"   # 和 server.py 里的 OUTBOX 是同一个文件

T0 = time.time()   # 开始的时刻，用来算"第几秒"


def stamp() -> str:
    """现在是开始后的第几秒，比如 " 3.5s"。"""
    return f"{time.time() - T0:4.1f}s"


def send(proc: subprocess.Popen, message: dict) -> None:
    """往 server 的 stdin 写一条消息，顺便打印出来（和 mcp_raw.py 一样）。"""
    line = json.dumps(message, ensure_ascii=False)
    print(f"{stamp()} >>> {line}")
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def pump(proc: subprocess.Popen, plates: queue.Queue) -> None:
    """帮手：守在出菜口，读到一行就放上传菜台（和 mcp_raw.py 一样）。"""
    while True:
        line = proc.stdout.readline()
        plates.put(line)
        if not line:
            return


def watch(plates: queue.Queue, seconds: float) -> None:
    """盯着传菜台看 seconds 秒：来了一行就打印；时间到了还没来，就说一声。"""
    try:
        line = plates.get(timeout=seconds)
    except queue.Empty:
        print(f"{stamp()} <<< （{seconds} 秒内没有新回复）")
        return
    print(f"{stamp()} <<< {line.strip()}")


def main() -> None:
    OUTBOX.unlink(missing_ok=True)   # 先清空发件箱，免得看到上一次的结果

    # ① 启动，握手
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    plates = queue.Queue()
    threading.Thread(target=pump, args=(proc, plates), daemon=True).start()
    send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "hand-made", "version": "0.1"},
        },
    })
    watch(plates, 10)
    send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    # ② 点一道慢菜，不等它
    send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "send_email", "arguments": {"to": "老板"}}})
    # ③ 马上点一道快菜
    send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "add", "arguments": {"a": 2, "b": 3}}})
    watch(plates, 3)

    # ④ 1 秒后发退菜单，退掉单号 3。它是通知（没有 id），厨房不会回。
    time.sleep(1)
    if SEND_CANCEL:
        send(proc, {"jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 3, "reason": "等太久了"}})

    # ⑤ 再点一道快菜：厨房还开着门吗？
    send(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "add", "arguments": {"a": 10, "b": 20}}})
    watch(plates, 3)

    # ⑥ 再等 8 秒：单号 3 还会回来吗？（慢菜要 5 秒，8 秒足够它做完）
    watch(plates, 8)

    # ⑦ 邮件到底发出去没有？看发件箱
    box = OUTBOX.read_text(encoding="utf-8").strip() if OUTBOX.exists() else "（空的，没有这个文件）"
    print(f"{stamp()} 发件箱 outbox.txt：{box}")

    # 收工
    proc.stdin.close()
    proc.wait(timeout=10)
    print("\n[server 的 stderr]", proc.stderr.read().strip() or "（空）")


if __name__ == "__main__":
    main()
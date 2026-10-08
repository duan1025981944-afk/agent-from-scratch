"""喊停实验：不用 MCP，只用 Python 自带的 asyncio，看"喊停"对两种厨师有什么区别。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 38 讲的小实验。把 mcp_cancel.py 里的 C1、C2 剥掉 MCP 那一层，
只留下最核心的一件事：等 5 秒的时候被喊停，厨师会不会停手。

    async 厨师：await asyncio.sleep(5)  = 跟前台说"5 秒后叫我"
    def 厨师：  time.sleep(5)           = 自己定闹钟，睡死 5 秒

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python scripts/cancel_demo.py
"""
import asyncio
import time


def say(start: float, text: str) -> None:
    """打印一行，前面标上"开始后第几秒"。"""
    print(f"{time.time() - start:4.1f}s  {text}")


def sync_chef(start: float) -> None:
    """普通函数：自己定闹钟，睡死 5 秒。"""
    say(start, "[def]   开始做菜：自己定个闹钟，睡 5 秒")
    time.sleep(5)                                    # 睡着了，谁叫都没用
    say(start, "[def]   闹钟响了，发邮件！")          # 喊停也拦不住这一行


async def async_chef(start: float) -> None:
    """async 函数：请前台 5 秒后叫醒自己。"""
    say(start, '[async] 开始做菜：跟前台说"5 秒后叫我"')
    try:
        await asyncio.sleep(5)                       # 叫醒的开关交给前台
    except asyncio.CancelledError:
        # 前台说的不是"时间到了"，而是"不用做了"：在 await 这里变成一个异常
        say(start, "[async] 被前台叫醒：不用做了")
        raise                                        # 照规矩放行（runner.py 里也是这么写的）
    say(start, "[async] 时间到，发邮件！")            # 被喊停的话，这一行不会执行


async def main() -> None:
    print("=== async 厨师 ===")
    start = time.time()
    task = asyncio.create_task(async_chef(start))    # 开始做菜
    await asyncio.sleep(1)
    say(start, "—— 喊停！")
    task.cancel()                                    # 喊停
    await asyncio.sleep(5)                           # 再等 5 秒，看厨师后来还干了什么

    print("\n=== def 厨师（和 SDK 一样，放进另一个线程里跑）===")
    start = time.time()
    task = asyncio.create_task(asyncio.to_thread(sync_chef, start))
    await asyncio.sleep(1)
    say(start, "—— 喊停！")
    task.cancel()
    await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())

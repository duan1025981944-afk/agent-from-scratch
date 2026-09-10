import asyncio
from config.loader import load_config
from providers.factory import create_provider  
from agent.runner import AgentRunner, AgentRunSpec
from agent.tools.loader import discover_tools
from pathlib import Path
from agent.tools import workspace
import argparse
from datetime import datetime
from session import manager

# ★ 必须在 discover_tools() 之前
root = workspace.set_root(".")      # 或从 config.json 读
TOOLS = discover_tools()

SYSTEM_PROMPT = f"""你是一个能读写文件的助手。

工作区根目录：{root}
所有文件操作都限制在这个目录及其子目录内，超出范围会被拒绝。
路径可以用相对路径（相对于工作区根目录），例如 agent/runner.py。

写文件前请先确认：write_file 会完全覆盖目标文件，不是追加或局部修改。
"""

def parse_args():
    p = argparse.ArgumentParser(description="mini_nanobot")
    p.add_argument("--session", help="恢复指定会话；不给就新建一个")
    p.add_argument("--list", action="store_true", help="列出所有历史会话后退出")
    return p.parse_args()

async def main():

    args = parse_args()

    if args.list:
        sessions = manager.list_all()
        if not sessions:
            print("还没有任何会话。")
            return
        print(f"{'会话 key':<18} {'条数':>5}  {'模型':<20} 创建时间")
        for key, meta, count in sessions:
            print(f"{key:<18} {count:>5}  "
                  f"{meta.get('model', '?'):<20} {meta.get('created_at', '?')}")
        return

    cfg = load_config()
    provider = create_provider(cfg)
    runner = AgentRunner()

    # ★ 三个分支缩成两行
    if args.session:
        session = manager.resume(args.session)
        if old := session.workspace_changed(str(root)):
            print(f"[警告] 这个会话当时的工作区是：{old}")
            print(f"       现在是：{root}")
            print(f"       历史里的相对路径可能已经失效。\n")
        print(f"[恢复会话 {session.key}，{len(session.history)} 条消息]\n")
    else:
        session = manager.create(cfg.model, str(root))
        print(f"[新会话 {session.key}]\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + session.history

    while True:
        user_input = input("你 > ").strip()
        if user_input in ("exit", "quit"):
            break
        if not user_input:
            continue

        # ★② 写盘边界：记下这一轮开始前，列表有多长
        boundary = len(messages)

        messages.append({"role": "user", "content": user_input})

        spec = AgentRunSpec(
            provider=provider,
            initial_messages=messages,
            tools=TOOLS,              # ← 新增
        )
        result = await runner.run(spec)

        messages = result.messages          # 把 runner 转完的完整历史收回来

        # ★ 只把这一轮新长出来的几条追加到磁盘
        #    放在打印之前——先落盘，再展示
        session.save_turn(messages[boundary:])   

        if result.stop_reason == "max_iterations":
            print("\n[转满圈数仍未完成]\n")
        elif result.final_content:
            print(f"\nAI > {result.final_content}\n")

if __name__ == "__main__":
    asyncio.run(main())

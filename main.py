"""命令行入口：把配置、工具、会话、循环这几样东西串成一次可交互的对话。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
**编排层**。它自己几乎不干实事，只负责决定"先做什么、后做什么"。
真正的活儿都在下面这些地方：

    读配置        config/loader.py
    造 provider   providers/factory.py
    发现工具      agent/tools/loader.py
    拼系统提示    agent/context.py
    压缩历史      agent/compaction.py
    转循环        agent/runner.py
    存档          session/manager.py

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**知道会话存在磁盘的哪里（那是 session/ 的事）
- 它**不**知道循环转几圈、工具怎么执行（那是 agent/runner.py 的事）
- 它**不**知道系统提示的内容（那在 templates/ 下）

它唯一真正"懂"的事情是**顺序**——尤其是压缩必须在记写盘边界之前。

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python main.py                      新建一个会话，开始对话
    python main.py --session 20260915-183243   恢复某个历史会话
    python main.py --list               列出所有历史会话后退出

    对话中输入 exit 或 quit 结束。
    对话中的本地命令（不发给模型、不进存档）：
        /dream         手动做一次"睡前整理"：把流水账并进长期记忆
        /memory        看当前的长期记忆
        /memory-log    看最近几次整理各改了什么（程序算的 diff，不是模型自述）
        /memory-undo   记忆被改坏了，退回上一版

──────────────────────────────────────────────────────────────
一轮对话的完整顺序（这是本文件的核心）
──────────────────────────────────────────────────────────────
    ① compact_if_needed()        超预算才压缩，一轮最多一次（要花钱）
    ② boundary = len(messages)   记下写盘边界 —— 必须在 ① 之后！
    ③ append 用户输入
    ④ runner.run()               转循环：调模型 → 执行工具 → 再调 → 直到给出答复
    ⑤ save_turn(messages[②:])    只把这一轮新长出来的落盘
    ⑥ print                      先落盘再打印，崩了不至于丢数据

    为什么 ② 要在 ① 之后：压缩会让 messages 变短，边界必须按压缩后的长度算，
    否则 ⑤ 会从错误的位置开始切，把旧消息重复写进存档。

──────────────────────────────────────────────────────────────
退出时还有一件事：睡前整理（第 27 讲）
──────────────────────────────────────────────────────────────
敲 exit 之后不是直接走人，先把流水账并进长期记忆（agent/memory.py 的 dream）。

**为什么放在退出时**：nanobot 的主触发是"会话闲置 15 分钟就整理一次"
（配置项 idleCompactAfterMinutes，默认 15）。我们是命令行程序，没有常驻进程
去数闲置时间，"用户敲了 exit"就是最接近的那个时刻——这段对话到此为止了。

**为什么不能只靠压缩触发**：压缩的线是 190,000 token，而整个项目所有 .py
加起来才 76,000。正常聊天几乎永远够不着，也就是说记忆几乎永远不会被写。
这个洞是第 26 讲做完之后才发现的。

Ctrl+C 退出不会整理——那些事实还在流水账里，下次退出时一起整理。
游标记着进度，不会漏也不会重。

更完整的说明见 docs/设计文档.md。
"""
import argparse
import asyncio

from agent.compaction import compact_if_needed, restore_history
from agent.context import build_system_prompt
from agent.memory import (
    correct_fact,          # ← 加
    diff_memory,
    dream,
    list_snapshots,
    read_memory,
    restore_snapshot,
    trace_fact,            # ← 加
)
from agent.runner import AgentRunner, AgentRunSpec
from agent.tools import workspace
from agent.tools.loader import discover_tools
from config.loader import load_config
from providers.factory import create_provider
from session import manager

# ── 模块级初始化：程序一启动就做，做一次 ──
#
# 顺序有要求：workspace.set_root() 必须在 discover_tools() 之前。
# 因为文件类工具（read_file / write_file / list_dir）在执行时要调
# workspace.resolve() 做越界检查，而 resolve() 需要根目录已经设好。
#
# set_root(".") = 把"启动时所在的目录"当作工作区。
# 也就是说，你在哪个目录敲 python main.py，Agent 就只能读写那个目录底下的文件。
root = workspace.set_root(".")

# 扫描 agent/tools/ 下所有模块，找出全部 Tool 子类。
# 返回 {工具名: 工具实例}，形如 {"echo": EchoTool(), "read_file": ReadFileTool(), ...}
# 加一个新工具 = 在 agent/tools/ 下新建一个 .py 文件，这里不用改。
TOOLS = discover_tools()

def current_system_prompt() -> str:
    """现算一条系统提示：模板 + 运行时 + 当前的长期记忆。

    谁会用它
        main()：开场时算一次，以及 /dream 整理完记忆之后**重算一次**。

    传入什么 / 返回什么
        不用传。返回拼好的系统提示（str）。

    为什么不是模块级算一次就完了（第 27 讲改的）
        第 24 讲时记忆只读不写，启动时读一次足够。现在 /dream 能在会话中途
        改掉 MEMORY.md——如果还用启动时那份，就会出现**记忆已经更新了，
        但模型看到的还是旧的**，而且直到重启都是旧的。

        改成函数之后，重算一次就是一句调用。

    为什么不干脆每轮都重算
        系统提示是提示缓存的前缀，改一个字后面全部失效（见 agent/context.py）。
        记忆几轮才变一次，每轮重算等于白白丢缓存。**变了才重算**是更好的权衡。

        nanobot 是每次请求都重拼的（AgentContext.build_messages），
        它那样做是因为要带上会话摘要、当前项目路径等每轮都可能不同的东西；
        我们的系统提示里没有这类内容。

    例子（要读 templates/ 和 data/，见 tests/test_memory_injection.py）
        没有记忆时  ->  模板 + 运行时，不含"# 长期记忆"
        有记忆时    ->  末尾多一段"# 长期记忆"
    """
    return build_system_prompt(str(root), read_memory())


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    谁会用它
        只有本文件的 main()。

    传入什么
        什么都不用传，它自己读 sys.argv。

    返回什么
        一个 Namespace 对象，带两个属性：

            args.session   str | None   --session 后面跟的会话 key；没给就是 None
            args.list      bool         给了 --list 就是 True

    例子
        python main.py                     -> session=None,  list=False
        python main.py --list              -> session=None,  list=True
        python main.py --session 2026...   -> session="2026...", list=False
    """
    p = argparse.ArgumentParser(description="mini_nanobot")
    p.add_argument("--session", help="恢复指定会话；不给就新建一个")
    p.add_argument("--list", action="store_true", help="列出所有历史会话后退出")
    return p.parse_args()


def print_session_list() -> None:
    """打印所有历史会话，供 --list 使用。

    谁会用它
        只有本文件的 main()，在 --list 分支里。

    传入什么
        无。它自己去问 session/manager.py 要数据。

    返回什么
        无。直接往终端打印。

    输出长什么样
        会话 key             条数  模型                 创建时间
        20260915-183243       38  deepseek-flash       2026-09-15T18:32:43
        20260913-104035      126  deepseek-flash       2026-09-13T10:40:35

        "条数"只数真正的消息，不含文件头和摘要便条（见 session/messages.py
        的 count_messages）。
    """
    sessions = manager.list_all()
    if not sessions:
        print("还没有任何会话。")
        return

    print(f"{'会话 key':<18} {'条数':>5}  {'模型':<20} 创建时间")
    for key, meta, count in sessions:
        print(
            f"{key:<18} {count:>5}  "
            f"{meta.get('model', '?'):<20} {meta.get('created_at', '?')}"
        )


def open_session(requested_key: str | None, model: str) -> manager.Session:
    """打开一个会话：给了 key 就恢复，没给就新建。

    谁会用它
        只有本文件的 main()。

    传入什么
        requested_key: --session 后面那个 key，形如 "20260915-183243"；
                       None 表示新建。
        model:         当前配置用的模型名，形如 "deepseek-flash"。
                       新建会话时会写进文件头，以后 --list 能看到。

    返回什么
        一个 Session 对象（定义在 session/manager.py）。要用到的三样：

            session.key       会话标识，也是文件名
            session.history   存档里的**全文**消息列表
            session.summary   最后一条摘要便条，没压缩过就是 None

    附带做了什么
        - 打印一行"[新会话 xxx]"或"[恢复会话 xxx，N 条消息]"
        - 恢复时如果发现当时的工作区和现在不一样，额外打一段警告——
          因为历史里的相对路径（比如 agent/runner.py）可能已经指不到东西了

    注意
        恢复时返回的 session.history 是**存档全文**，不是能直接用的上下文。
        要变成能发给模型的那份，还得过一道 restore_history()，见 main()。
    """
    if requested_key:
        session = manager.resume(requested_key)

        # 海象运算符 := ：既赋值给 old，又判断它是不是真值。
        # workspace_changed() 变了就返回"当时的工作区"，没变返回 None。
        if old := session.workspace_changed(str(root)):
            print(f"[警告] 这个会话当时的工作区是：{old}")
            print(f"       现在是：{root}")
            print("       历史里的相对路径可能已经失效。\n")

        print(f"[恢复会话 {session.key}，{len(session.history)} 条消息]\n")
        return session

    session = manager.create(model, str(root))
    print(f"[新会话 {session.key}]\n")
    return session


async def run_local_command(cmd: str, provider, session_key: str = "") -> bool:
    """处理 / 开头的本地命令，返回"记忆有没有被改动"。

    谁会用它
        main() 的主循环。

    传入什么
        cmd:      用户敲的整行，形如 "/dream"、"/memory-undo"。
        provider: /dream 要用它调模型。

    返回什么
        bool —— 记忆文件变了没有。变了的话调用方要重拼系统提示。

    为什么这些命令要在主循环里**拦掉**，不发给模型
        它们是给程序看的，不是给模型看的。发过去只会让模型困惑，
        还白白占一轮上下文、花一次钱。

        代价是"/"开头的正常提问会被误拦（比如问"/dream 是什么意思"）。
        认不出的命令会提示一句可用列表，用户改个说法就行——
        比"发给模型然后得到一个莫名其妙的回答"好。

    命令一览
        /dream         整理记忆
        /memory        看当前记忆
        /memory-log    看最近几次整理改了什么
        /memory-undo   退回上一版

    例子（要调模型/碰文件，不写成 doctest）
        "/memory-log" 没有快照时  ->  打印"还没有任何快照"，返回 False
    """
    if cmd == "/dream":
        outcome = await dream(provider)
        print(f"\n  {outcome.note}")
        if outcome.diff:
            # ★ 这段 diff 是程序比对前后两版算出来的，不是模型自己说的。
            # 模型漏抄一条时它自己不知道，只有逐行比对才看得见。
            print("  这次实际改动：")
            for line in outcome.diff.splitlines():
                print(f"    {line}")
        print()
        return outcome.processed > 0

    if cmd == "/memory":
        current = read_memory()
        print(f"\n{current or '（还没有任何长期记忆）'}\n")
        return False

    if cmd == "/memory-log":
        snapshots = list_snapshots()
        if not snapshots:
            print("\n  还没有任何快照。（整理记忆时会自动存）\n")
            return False

        print(f"\n  最近 {len(snapshots)} 份快照，从新到旧：\n")
        # 逐份和"它后面那一份"比，就能看出每一次整理改了什么。
        # 最新那份要和**当前的 MEMORY.md** 比——它之后可能又整理过。
        版本 = [read_memory()] + [
            p.read_text(encoding="utf-8-sig") for p in snapshots
        ]
        for i, path in enumerate(snapshots):
            改动 = diff_memory(版本[i + 1], 版本[i])
            print(f"  [{i}] {path.stem}")
            for line in (改动 or "（无改动）").splitlines():
                print(f"        {line}")
            print()
        print("  用 /memory-undo 退回 [0] 那一版。\n")
        return False

    if cmd == "/memory-undo":
        ok, note = restore_snapshot()
        print(f"\n  {note}\n")
        return ok
    
    if cmd.startswith("/correct"):
        # 纠正 = 记一条 correction 事实 + 立刻整理。
        #
        # 为什么要"立刻"整理，而不是等下次 /dream：
        # 你敲 /correct 的那一刻，说明你**刚刚发现记忆是错的**。
        # 让它带着错的内容再活一会儿没有任何好处，而且你很可能转头就忘了
        # 还有一条纠正挂在流水账上没生效。一次模型调用换一个"说了就算"。
        正确的说法 = cmd[len("/correct"):].strip()
        if not 正确的说法:
            print(
                "\n  用法：/correct 正确的说法"
                "\n  最好把错的也一起说出来，模型才知道该删哪条，例如："
                "\n    /correct 项目的向量库是 Chroma，不是 Pinecone\n"
            )
            return False

        编号 = correct_fact(正确的说法, session=session_key)
        print(f"\n  已记下纠正（流水账第 {编号} 条），正在整理进记忆……")

        outcome = await dream(provider)
        print(f"  {outcome.note}")
        if outcome.diff:
            print("  实际改动：")
            for line in outcome.diff.splitlines():
                print(f"    {line}")
        print()
        return outcome.processed > 0

    if cmd.startswith("/why"):
        关键词 = cmd[len("/why"):].strip()
        if not 关键词:
            print("\n  用法：/why 关键词        例如：/why Pinecone\n")
            return False

        命中 = trace_fact(关键词)
        if not 命中:
            print(f"\n  流水账里没有含「{关键词}」的事实。")
            print("  （记忆里的话是合并过的，换个更短的词再试试）\n")
            return False

        print(f"\n  流水账里 {len(命中)} 条含「{关键词}」，从新到旧：\n")
        for f in 命中:
            出处 = f.get("session") or "（没记出处）"
            print(f"  [{f['cursor']}] {f.get('at', '?')}  [{f.get('tag', '?')}]")
            print(f"        {f.get('content', '')}")
            print(f"        出处：{出处}")
        print(f"\n  想看原文：python main.py --session <出处>\n")
        return False

    print(
        "\n  不认识的命令。可用：/dream  /memory  /memory-log  /memory-undo"
        "\n  （要跟模型说以 / 开头的话，换个说法，比如去掉开头的斜杠）\n"
    )
    return False


async def main() -> None:
    """程序主流程：准备好一切，然后进入"读一句、答一句"的循环。

    谁会用它
        文件末尾的 asyncio.run(main())。

    传入什么 / 返回什么
        都没有。它靠 parse_args() 读命令行，靠 print() 输出。

    为什么是 async
        因为链路上有两个地方要等网络：provider.chat()（调模型）和
        tool.execute()（工具本身写成了 async）。asyncio.run() 负责起一个
        事件循环把它跑起来。

        注意 input() 是**同步阻塞**的——用户在敲字时，整个程序就停在那儿。
        这对单人 CLI 没问题；如果以后要支持"跑工具的同时还能继续打字"，
        这里就得改（nanobot 用一套"消息注入"机制解决这件事，我们跳过了）。

    流程
        见本文件顶部"一轮对话的完整顺序"。
    """
    args = parse_args()

    # --list 是个"看一眼就走"的分支，不用连模型，也不用开会话
    if args.list:
        print_session_list()
        return

    cfg = load_config()                 # 读 config.json，解析成一个 ResolvedModel
    provider = create_provider(cfg)     # 按 cfg.kind 选实现类并实例化
    runner = AgentRunner()

    session = open_session(args.session, cfg.model)

    # 组装这次运行要用的 messages。两部分：
    #   [0]  系统提示 —— 只活在内存里，**不会**写进存档（见设计文档 §3）
    #   [1:] 历史 —— 存档里是全文；如果压缩过，restore_history 会把
    #        "前 covered 条"换成一条摘要消息，还原成上次压缩后的样子
    messages = [{"role": "system", "content": current_system_prompt()}] + restore_history(
        session.history, session.summary
    )

    while True:
        user_input = input("你 > ").strip()
        if user_input in ("exit", "quit"):
            break
        if not user_input:              # 直接回车：什么都不做，重新读
            continue

        # /dream：手动做一次睡前整理。不进 messages，所以不占上下文、不写存档。
        # 这类以 / 开头的本地命令都该在这里拦掉，别让它们流到模型那边去。
        if user_input.startswith("/"):
            memory_change = await run_local_command(user_input, provider, session.key)
            if memory_change:
                # ★ 记忆变了，开场白要跟着换，否则模型这一轮看到的还是旧记忆。
                # messages[0] 永远是系统提示（不变量：它只活在内存里，不进存档）。
                messages[0] = {"role": "system", "content": current_system_prompt()}
            continue

        # ── ① 压缩（一轮最多一次，要花钱）──
        # 只有"免费手段用完还超预算"时才真的会调模型；否则原样返回。
        # covered 要传进去，压缩内部靠它算出新的累计值。
        outcome = await compact_if_needed(
            messages, provider, covered=session.covered, session=session.key
        )
        messages = outcome.messages
        if outcome.summary:             # 压过才记便条，没压不写
            session.save_summary(outcome.summary, outcome.covered)

        # ── ② 记写盘边界 ──
        # 必须在 ① 之后：压缩会让 messages 变短，边界得按压缩后的长度算。
        boundary = len(messages)

        # ── ③ 把用户这句话入链 ──
        messages.append({"role": "user", "content": user_input})

        # ── ④ 交给 runner 转循环 ──
        # runner 会一直转到模型不再要求调工具（或转满 max_iterations）。
        # 它保证返回的 messages = 传进去的 + 这一轮新长出来的尾巴，顺序不变。
        # ② 的 boundary 能用，靠的就是这条约定。
        result = await runner.run(
            AgentRunSpec(
                provider=provider,
                initial_messages=messages,
                tools=TOOLS,
            )
        )
        messages = result.messages      # 把转完的完整历史收回来

        # ── ⑤ 落盘（在打印之前）──
        # 只追加这一轮新长出来的几条。系统提示和摘要消息都在 boundary 之前，
        # 所以永远不会被写进存档——这正是我们要的。
        session.save_turn(messages[boundary:])

        # ── ⑥ 展示 ──
        if result.stop_reason == "max_iterations":
            print("\n[转满圈数仍未完成]\n")
        elif result.final_content:
            print(f"\nAI > {result.final_content}\n")

    # ── 退出前：睡前整理 ──
    # 走到这里说明用户敲了 exit/quit（Ctrl+C 不会走这条路）。
    # 整理失败不影响退出——记忆是锦上添花，用户想走就该让他走。
    outcome = await dream(provider)
    if outcome.processed or not outcome.ok:
        print(f"  [睡前整理] {outcome.note}")
        if outcome.diff:
            print("  实际改动（记错了就下次进来敲 /memory-undo）：")
            for line in outcome.diff.splitlines():
                print(f"    {line}")


if __name__ == "__main__":
    asyncio.run(main())
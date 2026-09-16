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

更完整的说明见 docs/设计文档.md。
"""
import argparse
import asyncio

from agent.compaction import compact_if_needed, restore_history
from agent.context import build_system_prompt
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

# 系统提示由 templates/ 下的文件拼成（SOUL.md + AGENTS.md + 运行时信息）。
# 在这里算一次、之后每轮复用——它不变，所以能一直命中模型的提示缓存。
# 想改 Agent 的性格或规矩，改 templates/ 里的 .md，不用碰代码（改完要重启）。
SYSTEM_PROMPT = build_system_prompt(str(root))


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
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + restore_history(
        session.history, session.summary
    )

    while True:
        user_input = input("你 > ").strip()
        if user_input in ("exit", "quit"):
            break
        if not user_input:              # 直接回车：什么都不做，重新读
            continue

        # ── ① 压缩（一轮最多一次，要花钱）──
        # 只有"免费手段用完还超预算"时才真的会调模型；否则原样返回。
        # covered 要传进去，压缩内部靠它算出新的累计值。
        outcome = await compact_if_needed(messages, provider, covered=session.covered)
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


if __name__ == "__main__":
    asyncio.run(main())

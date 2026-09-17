"""验收脚本：拿一段**真实**对话，跑一次真模型，看它提炼出什么事实。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 26 讲的验收工具。**它不是 Agent 的一部分**——和 scripts/check_tokens.py
一样放在 scripts/ 下，项目里没有任何代码 import 它。

它回答一个问题：**模型提炼出来的"值得长期记住的事实"，到底靠不靠谱？**

──────────────────────────────────────────────────────────────
为什么需要它（本脚本存在的全部理由）
──────────────────────────────────────────────────────────────
提炼是挂在压缩上的：压缩触发 -> 顺便提炼。而压缩的触发线是
CONTEXT_BUDGET = 190,000 token。

这个数有多大？**整个项目所有 .py 文件加起来才 76,478 token，只占 40%。**
也就是说让 Agent 把自己全部源码读一遍，离触发线还差一大截。
更何况工具结果在 100,000 就被清理了，读文件这条路根本攒不起来。

真要触发，得聊出 19 万 token 的**纯对话文字**——大概三十万个汉字。
为了验收一次提炼效果去聊这么多，不现实。

所以这个脚本换个做法：**不等它自然触发，直接把预算调小到几千，
拿一段真实存档喂进去，让压缩当场发生。** 走的是同一条代码路径、
同一个真模型、同一份提示词，只有触发线不一样。

──────────────────────────────────────────────────────────────
用法
──────────────────────────────────────────────────────────────
    python -m scripts.check_facts                  用最近一个会话
    python -m scripts.check_facts 20260917-161500  用指定会话
    python -m scripts.check_facts --budget 2000    把触发线调得更小

**必须用 -m**，理由和 check_tokens.py 一样：只有从项目根目录用 -m 运行，
Python 才找得到 agent 这个包。

──────────────────────────────────────────────────────────────
它保证不碰真实记忆
──────────────────────────────────────────────────────────────
脚本会把 HISTORY_FILE 临时指到 data/memory/history.check.jsonl，
跑完把结果打印出来、文件留着给你看。**真正的流水账一个字都不会动。**

要是不小心跑了很多次，直接删掉那个 .check.jsonl 就行。

──────────────────────────────────────────────────────────────
花多少钱
──────────────────────────────────────────────────────────────
一次调用，输入是被压掉的那段历史（几千 token），输出是一段摘要。
大约几分钱。

──────────────────────────────────────────────────────────────
怎么看输出
──────────────────────────────────────────────────────────────
三件事要挨个看：

    1. 挑出来的是**事实**，还是"讨论了 xxx"这种话题？
    2. 标签打得对不对？把"正在做阶段五"打成 permanent 就是打错了
    3. 有没有一条**根本不该记**、或者**记错了**的？

第 3 点最有价值：真出现了，那就是第 28、29 讲（回滚和纠正）的现成素材。
"""

import argparse
import asyncio

from agent import memory
from agent.compaction import compact_if_needed
from agent.memory import MEMORY_FILE, read_facts
from config.loader import load_config
from providers.factory import create_provider
from session import manager


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="看看模型能从真实对话里提炼出什么事实")
    p.add_argument("session", nargs="?", help="会话 key；不给就用最近一个")
    p.add_argument(
        "--budget",
        type=int,
        default=3_000,
        help="临时把压缩触发线调到这个值（默认 3000，真实值是 190000）",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    sessions = manager.list_all()
    if not sessions:
        print("还没有任何会话。先 python main.py 聊几句再来。")
        return

    # list_all 返回 [(key, meta, count), ...]，最近的在最前面
    key = args.session or sessions[0][0]
    session = manager.resume(key)
    if len(session.history) < 4:
        print(f"会话 {key} 只有 {len(session.history)} 条消息，太短了，压不出什么。")
        return

    # ★ 把流水账指到旁边一个文件，真实记忆不受影响
    memory.HISTORY_FILE = MEMORY_FILE.parent / "history.check.jsonl"
    before = len(read_facts())

    cfg = load_config()
    provider = create_provider(cfg)

    print(f"会话 {key}：{len(session.history)} 条消息")
    print(f"触发线临时调成 {args.budget:,} token（真实值 190,000）\n")

    messages = [{"role": "system", "content": "（验收脚本，系统提示从简）"}] + session.history
    out = await compact_if_needed(messages, provider, budget=args.budget, session=key)

    if out.summary is None:
        print("没有压缩发生。要么历史还是太短，要么调模型失败了。")
        return

    print("─" * 60)
    print("模型写的摘要全文：\n")
    print(out.summary)
    print("─" * 60)

    新增 = read_facts()[before:]
    if not 新增:
        print("\n一条事实都没提炼出来。可能是这段对话确实没什么值得长期记的，")
        print("也可能是模型没照格式写——把上面的摘要全文看一遍就知道是哪种。")
        return

    print(f"\n进了流水账的 {len(新增)} 条：\n")
    for f in 新增:
        print(f"  [{f['tag']:<10}] {f['content']}")

    print(f"\n（这些写在 {memory.HISTORY_FILE.name}，不是真实流水账，可以随时删）")


if __name__ == "__main__":
    asyncio.run(main())

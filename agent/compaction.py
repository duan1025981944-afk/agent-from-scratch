"""摘要压缩：把旧的对话历史交给模型压成一段话，替换掉原文。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第二道防线。当"免费手段"（agent/payload.py 的截断和清理）都用完了、
上下文还是超预算时，才轮到它出手：把一段旧历史交给模型，让它写一段摘要，
用这段摘要顶替原来那几十条消息。

    压缩前   [system, 问1, 答1, 问2, 答2, 问3, 答3, 问4, 答4]
    压缩后   [system, 摘要(代表问1~答2), 问3, 答3, 问4, 答4]
                     ^^^^^^^^^^^^^^^^  几十条变一条

──────────────────────────────────────────────────────────────
和 payload.py 那道防线的区别
──────────────────────────────────────────────────────────────
                  payload.py 的清理        本文件的压缩
    压什么         单条工具结果的内容        一整段对话历史
    要花钱吗       不要                     要，多调一次模型
    在哪执行       build_payload 里，每圈    一轮对话开始前，一轮最多一次
    会失败吗       不会                     会 —— 失败就不压，绝不丢数据

**为什么不能塞进 build_payload**：那个函数每圈都被调用，而且是同步的纯函数。
把一次模型调用塞进去，等于每圈多花一次钱、多等几秒。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**改存档。它返回一份**新的**消息列表，传进来的那份一个字不动
- 它**不**知道 jsonl 长什么样。"怎么把摘要存下来"是 session/ 的事
- 它**不**自己决定提示词内容。那在 templates/prompts/compaction.md

──────────────────────────────────────────────────────────────
压缩时顺便做的第二件事：往流水账里记事实（第 26 讲）
──────────────────────────────────────────────────────────────
压缩本来就要调一次模型、要它通读这段历史。**既然它已经读完了，就顺便
让它多写一节**："哪些事实即使换个会话也还有用"。

    调一次模型
        ├─ 五项摘要       -> 顶替原文，留在这次对话里，会话结束就没了
        └─ 事实清单       -> 写进流水账 history.jsonl，跨会话留着

为什么合成一次调用，不分两次
    nanobot 就是这么做的：Consolidator.archive 产出的那份东西，
    既当会话摘要返回，也 append 进 history.jsonl（见 summarize_transcript）。
    分两次等于多花一次钱、多读一遍同样的历史。

和 nanobot 的一处不同
    nanobot 的摘要**整份**都是 `- [标签] 事实` 这种行。我们保留了原来的
    五项散文，只在末尾加一节事实清单。因为我们的摘要还要负责"下一步""失败
    过的尝试"这类叙事，做成纯事实行会丢掉它们。

记事实失败了会怎样
    不影响压缩。压缩是正事，记忆是锦上添花——见 memory.record_facts()。

──────────────────────────────────────────────────────────────
存档和摘要的关系（重要，也最容易绕晕）
──────────────────────────────────────────────────────────────
**磁盘上永远是全文，摘要消息永远不写进去。**

磁盘上多的是一张"便条"：

    {"_summary": {"text": "……", "covered": 15, "at": "2026-09-15T16:22:10"}}

意思是"存档前 15 条已经被这段摘要代表了"。下次启动时，restore_history()
拿全文 + 便条，现场重造出 [摘要消息] + history[15:]。

好处：压缩是有损的，但原始数据一直在，随时能翻回去、随时能改压缩策略。
nanobot 也是这么做的（set_summary_checkpoint 的注释写明
"while preserving the transcript"）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.payload import CONTEXT_BUDGET, build_payload
from agent.memory import record_facts
from agent.prompts import read_template
from agent.tokens import estimate_tokens
from providers.base import Provider

# 压缩时至少保留"预算的这个比例"那么多 token 的原文。
#
# 为什么按 token 而不是按"最近 N 轮"：一轮的大小能差几千倍。一句"好的"是
# 几个 token，一轮读十个文件是上万 token。按轮数算的话，保留 2 轮可能就
# 保留了 12,000 token，压缩几乎白做（实测只降过 13%）。
#
# nanobot 的 snip_history 也是按 token 从后往前装的，不是按轮数。
KEEP_RECENT_RATIO = 0.5

# 摘要消息的开头，用来在一堆消息里认出"哪条是摘要"。
SUMMARY_PREFIX = "[以下是更早对话的摘要]"

# 摘要消息用 system 而不是 user：
# 压缩总是发生在追加用户输入之前，用 user 会造成两条 user 消息挨在一起。
# OpenAI 兼容接口能接受，但换成要求严格交替的厂商（如 Anthropic）就会报错。
# 而且摘要是"给你参考的背景"，不是用户说过的话。
SUMMARY_ROLE = "system"

# 压缩用的提示词。正文在 templates/prompts/compaction.md，改文件即生效。
#
# 为什么放文件里：提示词是最需要反复调的东西——哪一项没保住就补哪一项，
# 一天可能改五遍。混在 .py 里，每次改提示词都变成一次代码改动，
# git 历史里也分不清"改了逻辑"和"改了措辞"。
#
# 五项结构对齐 Anthropic 官方默认的摘要提示词
# （Task Overview / Current State / Important Discoveries / Next Steps / Context to Preserve）。
# 第 3 项点名"失败过的尝试"，对应学习笔记 §4.4：压缩最容易丢的就是失败路径，
# 丢了以后 Agent 会把失败过的方案再试一遍。
# 末尾那句"不要调用任何工具"来自官方文档点名的失败模式：
# 请求里带着 tools 时，模型有时会在写摘要这一步改去调工具，摘要变成空的。
#
# 在 import 时就读：文件缺了要在启动时立刻报错，而不是等聊到一半压缩时才炸。
SUMMARY_INSTRUCTION = read_template("prompts", "compaction.md")


@dataclass
class CompactionResult:
    """compact_if_needed() 的返回值：压完之后的三样东西。

    谁会产生它
        只有 compact_if_needed()。

    谁会用它
        main.py。它拿 messages 继续这一轮，拿 summary 判断要不要写便条。

    字段
        messages: 压缩后的消息列表。**没压缩时，它就是传进去的那个对象本身**
                  （用 is 判断为 True），所以调用方可以无脑 messages = out.messages。
        summary:  这一轮**新生成**的摘要文字。**None 表示这轮没压缩**——
                  可能是没超预算、可能是历史太短、也可能是调模型失败了。
                  调用方靠它判断要不要落盘：压过才写便条，没压不写。
        covered:  摘要**累计**代表了存档里前多少条消息。没压缩时原样返回
                  传进来的值，压缩后是"旧值 + 这次新盖住的条数"。

    为什么要单独定义一个类，不直接返回元组
        返回三个值的元组，调用方得记住顺序（哪个是 messages、哪个是 covered）。
        用 dataclass 之后，读代码的人看到 out.covered 就知道是什么，不用数位置。

    例子
        >>> r = CompactionResult(messages=[], summary=None, covered=0)
        >>> r.summary is None          # None = 这轮没压缩
        True
    """

    messages: list[dict[str, Any]]
    summary: str | None = None
    covered: int = 0


def summary_message(text: str) -> dict[str, Any]:
    """把一段摘要文字包装成一条能放进 messages 的消息。

    谁会用它
        - compact_if_needed()：压缩后拼进新列表
        - restore_history()：恢复会话时从便条重造

    传入什么
        text: 模型写出来的摘要正文，不带任何前缀。

    返回什么
        一条消息字典，形如：

            {"role": "system",
             "content": "[以下是更早对话的摘要]\\n\\n- 用户要求读 main.py\\n- ……"}

    为什么要有这个函数
        "摘要消息长什么样"这件事只能有一个地方知道。哪天想把 role 从
        system 改成 user、或者换个前缀，只改这一个函数就行——
        session/ 那边只存"一段文字 + 一个数字"，完全不知道这个格式。

    例子
        >>> m = summary_message("用户要求读 main.py")
        >>> m["role"]
        'system'
        >>> m["content"].startswith("[以下是更早对话的摘要]")
        True
    """
    return {"role": SUMMARY_ROLE, "content": f"{SUMMARY_PREFIX}\n\n{text}"}


def is_summary(msg: dict[str, Any]) -> bool:
    """判断一条消息是不是摘要消息。

    谁会用它
        - compact_if_needed()：数"这次新盖住了多少条存档消息"时，
          要把上一条摘要消息排除掉（它不对应任何存档记录）
        - 测试：确认压缩后永远只有一条摘要

    传入什么
        msg: 任意一条消息字典。

    返回什么
        True / False。

    例子
        >>> is_summary(summary_message("abc"))
        True
        >>> is_summary({"role": "system", "content": "你是一个助手"})
        False
        >>> is_summary({"role": "user", "content": "你好"})
        False
    """
    return msg.get("role") == SUMMARY_ROLE and str(
        msg.get("content", "")
    ).startswith(SUMMARY_PREFIX)


def restore_history(
    history: list[dict[str, Any]], record: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """恢复会话时用：存档全文 + 摘要便条 -> 上次压缩后的那份历史。

    谁会用它
        main.py，程序启动时调一次。

    传入什么
        history: 从存档读回来的**全文**消息列表（session.history）。
        record:  最后一条摘要便条（session.summary），形如

                     {"text": "……", "covered": 15, "at": "2026-09-15T16:22:10"}

                 **None 表示这个会话从没压缩过**。

    返回什么
        能直接用的历史列表：

            record 是 None  ->  原样返回 history
            record 有值     ->  [摘要消息] + history[covered:]

        注意返回的**不含 system 消息**——那是 main.py 自己加在最前面的。

    拿到之后怎么用
        main.py 里就一句：

            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + \\
                       restore_history(session.history, session.summary)

    例子
        没压缩过的会话，原样返回：

        >>> h = [{"role": "user", "content": "问题1"},
        ...      {"role": "assistant", "content": "回答1"}]
        >>> restore_history(h, None) == h
        True

        压缩过的会话，前 1 条被摘要顶替：

        >>> out = restore_history(h, {"text": "聊过问题1", "covered": 1})
        >>> is_summary(out[0])
        True
        >>> out[1:] == h[1:]
        True
    """
    if not record:
        return history
    covered = record.get("covered", 0)
    return [summary_message(record["text"])] + history[covered:]


def find_cut(messages: list[dict[str, Any]], keep_tokens: int) -> int:
    """找切点：从哪个下标开始的消息保持原文，之前的全部压成摘要。

    谁会用它
        只有 compact_if_needed()。

    传入什么
        messages:    完整的内存消息列表（含 system，可能含上一条摘要）。
        keep_tokens: 至少要保留多少 token 的原文。
                     compact_if_needed 传的是 budget * KEEP_RECENT_RATIO。

    返回什么
        一个下标 cut：

            messages[:cut]   会被压成一段摘要
            messages[cut:]   原文保留

        **返回 0 表示"压不动"**——只有一轮，或者切点已经在最前面了。

    怎么找的
        从最后一轮开始，往前一轮一轮地退，能多留一轮原文就多留一轮，
        直到尾巴的大小超过 keep_tokens 为止。

            [system] [轮1] [轮2] [轮3] [轮4]
                                    ^
                          先看轮4：一定保留
                          再看轮3：加上还不超 keep_tokens？留
                          再看轮2：加上超了？停，cut = 轮3 的起点

        **最后一轮永远保留**，哪怕它自己就超了 keep_tokens。因为模型正在
        用它作答，压掉它等于让模型忘掉刚发生的事。

    为什么只在 user 消息处切
        这是不变量 3 的保障。assistant(tool_calls) 和它对应的 tool 结果
        永远在同一条 user 消息之后，所以切在 user 处，就**不可能**把它们
        拆散。切散了会留下"有工具调用却没有结果"的孤儿，API 直接返回 400。

    例子
        只有一轮时压不动：

        >>> msgs = [{"role": "system", "content": "s"},
        ...         {"role": "user", "content": "问题1"},
        ...         {"role": "assistant", "content": "回答1"}]
        >>> find_cut(msgs, keep_tokens=10)
        0

        三轮、额度很小时，切点落在最后一轮的 user 上：

        >>> msgs = [{"role": "system", "content": "s"}]
        >>> for i in range(1, 4):
        ...     msgs += [{"role": "user", "content": f"问题{i}" * 50},
        ...              {"role": "assistant", "content": f"回答{i}" * 50}]
        >>> cut = find_cut(msgs, keep_tokens=100)
        >>> msgs[cut]["role"]
        'user'
        >>> cut > 1
        True
    """
    user_positions = [
        i for i, m in enumerate(messages) if m.get("role") == "user" and i > 0
    ]
    if not user_positions:
        return 0

    cut = user_positions[-1]                       # 最后一轮永远保留原文
    tail = estimate_tokens(messages[cut:])

    for pos in reversed(user_positions[:-1]):      # 再往前，能留就留
        tail += estimate_tokens(messages[pos:cut])
        if tail > keep_tokens:
            break
        cut = pos

    return cut if cut > 1 else 0                   # 切点在最前面 = 没东西可压


async def summarize(
    history: list[dict[str, Any]], provider: Provider
) -> str | None:
    """让模型把一段历史压成一段话。

    谁会用它
        只有 compact_if_needed()。

    传入什么
        history:  要被压掉的那段消息（compact_if_needed 里的 old）。
        provider: 模型接口。用的是**同一个** provider，不额外配便宜模型——
                  摘要质量直接决定后续对话质量，不值得省这点钱。

    返回什么
        摘要正文（字符串），或者 **None 表示失败**。
        失败有两种：调模型抛异常，或者模型回了空内容。

    为什么失败返回 None 而不是抛异常
        压缩失败不该让整轮对话跪掉。调用方看到 None 就跳过压缩、照常继续，
        最多这一轮贵一点。**绝不丢数据**是这里的第一原则。

    为什么不传 tools
        Anthropic 官方文档点名过这个失败模式：请求里带着工具定义时，
        模型有时会在"写摘要"这一步改去调工具，结果摘要是空的。
        不给工具，它就只能用文字回答。

    做了什么（三步）
        1. 把要压的历史过一遍 build_payload —— 里面可能有超长的工具结果，
           不截断的话光是"读一遍历史"就可能超窗口
        2. 末尾追加一条 user 消息，内容是 SUMMARY_INSTRUCTION（来自
           templates/prompts/compaction.md）
        3. 调模型，取 content
    """
    request = build_payload(history) + [
        {"role": "user", "content": SUMMARY_INSTRUCTION}
    ]
    try:
        reply = await provider.chat(request)      # 不给 tools：这次只要文字
    except Exception as exc:
        print(f"  [压缩失败，本轮跳过：{exc}]")
        return None
    return reply.content or None


async def compact_if_needed(
    messages: list[dict[str, Any]],
    provider: Provider,
    budget: int = CONTEXT_BUDGET,
    covered: int = 0,
    session: str = "",
) -> CompactionResult:
    """总入口：超预算就压缩，否则原样返回。

    谁会用它
        main.py，每轮对话开始前调一次（**在记写盘边界之前**）。

    传入什么
        messages: 完整的内存消息列表。**本函数不修改它**，压缩时返回新列表。
        provider: 模型接口，写摘要时要用。
        budget:   预算。默认 CONTEXT_BUDGET（190,000）。
                  测试里会传很小的值来逼出压缩行为。
        covered:  **之前**的摘要已经代表了存档前多少条。
        session:  当前会话 key。只用来标记流水账里每条事实的出处（第 26 讲），
                  压缩本身不需要它。不传照常工作，只是事实的"出处"是空的。
                  新会话传 0；恢复的会话传 session.covered。
                  漏传的话，第二次压缩算出来的 covered 会偏小，
                  下次恢复时会把已经被摘要代表过的消息又读一遍。

    返回什么
        一个 CompactionResult。三种情形：

            没超预算      -> messages 原样（is 判断为 True），summary=None
            超了但压不动   -> 同上。历史太短（只有一轮）时会这样
            压了          -> messages 是新列表，summary 有值，covered 变大

    拿到之后怎么用
        main.py 里的标准三句：

            out = await compact_if_needed(messages, provider, covered=session.covered)
            messages = out.messages
            if out.summary:                                   # 压过才记便条
                session.save_summary(out.summary, out.covered)

        **然后才能记 boundary。** 压缩会让 messages 变短，边界必须按压缩后
        的长度算，否则 save_turn 会从错误的位置开始切。

    触发条件为什么要过一遍 build_payload
        第一行是这样写的：

            if estimate_tokens(build_payload(messages)) <= budget:

        量的是**加工之后**的大小，不是原始大小。也就是说问的是
        "免费手段（截断、清理）全用完之后，还超不超？"

        所以摘要真正会触发的场景是「对话文字本身太多」，而不是
        「工具结果太大」——后者第一道防线就解决了。

    covered 怎么算（最容易写错的地方）
        压第二次时，被压掉的那段 old 里**开头就是上一条摘要消息**，
        而它不对应任何存档记录。所以数的时候要把它排除：

            newly = sum(1 for m in old if not is_summary(m))

        写成固定的 covered + cut - 1 的话，第一次对、第二次多算 1，
        恢复时就会漏掉一条存档消息、啃掉一条用户提问。
        test_covered_is_correct_after_a_second_compaction 守着这一点。
    """
    if estimate_tokens(build_payload(messages)) <= budget:
        return CompactionResult(messages, covered=covered)      # 没超，什么都不做

    cut = find_cut(messages, keep_tokens=int(budget * KEEP_RECENT_RATIO))
    if cut == 0:
        return CompactionResult(messages, covered=covered)      # 历史太短，压不动

    # system 永远留在第 0 位；old 被压掉；recent 保持原文
    system, old, recent = messages[:1], messages[1:cut], messages[cut:]

    before = estimate_tokens(build_payload(messages))   # 和 runner 打印的是同一把尺子
    summary = await summarize(old, provider)
    if summary is None:
        return CompactionResult(messages, covered=covered)      # 失败就照常继续，绝不丢数据

    compacted = system + [summary_message(summary)] + recent
    after = estimate_tokens(build_payload(compacted))

    # ★ 顺便把摘要里那节"值得长期记住的事实"记进流水账（第 26 讲）。
    # 放在这里而不是放在 summarize() 里：summarize 只管"要一段文字"，
    # 至于这段文字有什么用，是调用方的事。
    written = record_facts(summary, session=session)
    if written:
        print(f"  [记入长期记忆流水账 {written} 条]")

    # 这次新盖住的是 old 里"真的来自存档"的那些——上一条摘要消息不算
    newly = sum(1 for m in old if not is_summary(m))
    print(f"  [已压缩 {newly} 条历史：约 {before:,} → {after:,} token]")
    return CompactionResult(compacted, summary=summary, covered=covered + newly)
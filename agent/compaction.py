"""摘要压缩：把旧的对话历史交给模型压成一段话，替换掉原文。

和第 13 讲的工具结果替换的区别：

              工具结果替换            摘要压缩
    压什么     单条工具结果的内容       一整段对话历史
    要花钱吗   不要                    要，多调一次模型
    在哪执行   build_payload 里         一轮对话开始前
    失败了     不会失败                 不压缩，照常继续

为什么不能放进 build_payload：那个函数每圈都被调用，而且是同步的纯函数。
把一次模型调用塞进去，等于每圈多花一次钱、多等几秒。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.payload import CLEAR_TRIGGER, CONTEXT_BUDGET, build_payload
from agent.prompts import read_template
from agent.tokens import estimate_tokens
from providers.base import Provider

# 最近这么多 token 的对话保持原文。摘要是有损的，模型正在用的内容不能损。
# 用 token 而不是"轮数"：一轮可能只有两句话，也可能读了十个文件、上万 token。
KEEP_RECENT_RATIO = 0.5          # 预算的一半留给原文

# 摘要消息的开头，用来认出哪条是摘要
SUMMARY_PREFIX = "[以下是更早对话的摘要]"

# 摘要消息用 system 而不是 user：
# 压缩总是发生在追加用户输入之前，用 user 会造成两条 user 消息挨在一起。
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
    """压缩的结果。summary 为 None 表示这一轮没有压缩。"""

    messages: list[dict[str, Any]]
    summary: str | None = None
    covered: int = 0          # 摘要总共代表了存档里前多少条消息


def summary_message(text: str) -> dict[str, Any]:
    """把一段摘要文字包成一条消息。"""
    return {"role": SUMMARY_ROLE, "content": f"{SUMMARY_PREFIX}\n\n{text}"}


def is_summary(msg: dict[str, Any]) -> bool:
    return msg.get("role") == SUMMARY_ROLE and str(
        msg.get("content", "")
    ).startswith(SUMMARY_PREFIX)


def restore_history(
    history: list[dict[str, Any]], record: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """恢复会话时用：把存档全文 + 摘要记录，还原成上次压缩后的那份历史。

    存档里永远是全文，摘要记录只说明"前 covered 条由这段摘要代表"。
    """
    if not record:
        return history
    covered = record.get("covered", 0)
    return [summary_message(record["text"])] + history[covered:]


def find_cut(messages: list[dict[str, Any]], keep_tokens: int) -> int:
    """找切点：这个下标之前的消息会被压掉，从这里开始保持原文。

    从最后一轮往前退，能多留一轮原文就多留一轮，直到尾巴超过 keep_tokens。
    只在 user 消息处切，保证 assistant(tool_calls) 和它的 tool 结果不被拆散。
    没东西可压时返回 0。
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
    """让模型把一段历史压成一段话。失败返回 None。"""
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
) -> CompactionResult:
    """超预算就压缩，否则原样返回。

    covered：之前的摘要已经代表了存档前多少条，用来算出新的累计值。
    """
    if estimate_tokens(build_payload(messages)) <= budget:
        return CompactionResult(messages, covered=covered)      # 没超，什么都不做

    cut = find_cut(messages, keep_tokens=int(budget * KEEP_RECENT_RATIO))
    if cut == 0:
        return CompactionResult(messages, covered=covered)      # 历史太短，压不动

    system, old, recent = messages[:1], messages[1:cut], messages[cut:]

    before = estimate_tokens(build_payload(messages))   # 和 runner 打印的是同一把尺子
    summary = await summarize(old, provider)
    if summary is None:
        return CompactionResult(messages, covered=covered)      # 失败就照常继续，绝不丢数据

    compacted = system + [summary_message(summary)] + recent
    after = estimate_tokens(build_payload(compacted))

    # 这次新盖住的是 old 里"真的来自存档"的那些——上一条摘要消息不算
    newly = sum(1 for m in old if not is_summary(m))
    print(f"  [已压缩 {newly} 条历史：约 {before:,} → {after:,} token]")
    return CompactionResult(compacted, summary=summary, covered=covered + newly)
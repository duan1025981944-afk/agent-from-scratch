"""摘要压缩的测试。对应第 17 讲。"""
from __future__ import annotations

import asyncio
import copy

from agent.compaction import (
    is_summary,
    SUMMARY_PREFIX,
    compact_if_needed,
    find_cut,
)
from agent.tokens import estimate_tokens
from providers.base import ModelReply, Provider

SYSTEM = {"role": "system", "content": "你是助手"}


class StubProvider(Provider):
    """假模型：记录被调用几次，按 reply 返回固定内容或抛错。"""

    def __init__(self, reply: str | None = "摘要内容", fail: bool = False):
        self.reply, self.fail, self.calls = reply, fail, 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("模型炸了")
        return ModelReply(content=self.reply)


def turn(n: int, tool_content: str = "结果") -> list[dict]:
    """造一轮带工具调用的对话。"""
    cid = f"call_{n}"
    return [
        {"role": "user", "content": f"第 {n} 个问题"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": cid, "content": tool_content},
        {"role": "assistant", "content": f"第 {n} 个回答"},
    ]


def build(n_turns: int, tool_content: str = "结果") -> list[dict]:
    messages = [SYSTEM]
    for i in range(1, n_turns + 1):
        messages += turn(i, tool_content)
    return messages


# ───────────────────────────── 切点 ─────────────────────────────

def test_cut_lands_on_a_user_message():
    """只在轮次边界切，才不会拆散 tool_call 和它的结果。"""
    messages = build(5)
    cut = find_cut(messages, keep_tokens=200)
    assert messages[cut]["role"] == "user"


def test_cut_keeps_recent_turns_within_the_token_limit():
    """切点之后保留的原文，不超过给定的 token 额度。"""
    messages = build(5, tool_content="x" * 600)
    cut = find_cut(messages, keep_tokens=500)
    assert estimate_tokens(messages[cut:]) <= 500


def test_one_huge_turn_still_gets_compacted():
    """一轮就撑爆额度时，仍然要能把它之前的历史压掉（旧的按轮数算做不到这点）。"""
    messages = build(2) + turn(3, "x" * 20_000)     # 最后一轮极大
    cut = find_cut(messages, keep_tokens=500)
    assert cut > 1                                  # 压得动
    assert messages[cut]["role"] == "user"


def test_no_cut_when_there_is_only_one_turn():
    """只有一轮时最后一轮要保留，没东西可压。"""
    assert find_cut(build(1), keep_tokens=10) == 0


# ─────────────────────────── 压缩行为 ───────────────────────────

def test_under_budget_does_not_call_the_model():
    """没超预算时一次模型都不能调——调了就是白花钱。"""
    provider = StubProvider()
    messages = build(3)

    assert asyncio.run(compact_if_needed(messages, provider, budget=10_000)).messages is messages
    assert provider.calls == 0


def test_over_budget_replaces_old_history_with_a_summary():
    """超预算时：system 保留、旧历史换成一条摘要、最近几轮保持原文。"""
    messages = build(5, tool_content="x" * 2000)
    result = asyncio.run(
        compact_if_needed(messages, StubProvider("压缩后的内容"), budget=500)
    ).messages

    assert result[0] == SYSTEM
    assert result[1]["content"].startswith(SUMMARY_PREFIX)
    assert "压缩后的内容" in result[1]["content"]
    assert result[2:] == messages[find_cut(messages, keep_tokens=250):]   # 最近几轮一字未改
    assert len(result) < len(messages)


def test_original_list_is_untouched():
    """不变量 1：压缩产出新列表，传进来的存档一个字不动。"""
    messages = build(5, tool_content="x" * 2000)
    snapshot = copy.deepcopy(messages)

    asyncio.run(compact_if_needed(messages, StubProvider(), budget=500))

    assert messages == snapshot


def test_no_orphan_tool_messages_after_compaction():
    """不变量 3：压缩后不能剩下没有调用记录的 tool 消息。"""
    messages = build(5, tool_content="x" * 2000)
    result = asyncio.run(compact_if_needed(messages, StubProvider(), budget=500)).messages

    open_ids: set[str] = set()
    for m in result:
        for call in m.get("tool_calls") or []:
            open_ids.add(call["id"])
        if m["role"] == "tool":
            assert m["tool_call_id"] in open_ids


def test_failed_summary_keeps_everything():
    """压缩失败就照常继续，绝不丢数据。"""
    messages = build(5, tool_content="x" * 2000)
    outcome = asyncio.run(compact_if_needed(messages, StubProvider(fail=True), budget=500))
    assert outcome.messages is messages
    assert outcome.summary is None


def test_a_single_oversized_turn_does_not_block_compaction():
    """只有两轮、但最后一轮极大时，也要能把第一轮压掉。

    旧的"至少留最近 2 轮"规则在这里会返回 0，一条都压不掉，
    而现实里一轮读十个文件就能撑爆预算。
    """
    messages = build(1) + turn(2, "x" * 20_000)
    cut = find_cut(messages, keep_tokens=500)

    assert cut > 1
    assert messages[cut]["role"] == "user"


def test_covered_is_correct_after_a_second_compaction():
    """压第二次时，messages 里已经躺着一条摘要消息（下标 1），它不对应任何存档记录。

    正确的换算是 covered_new = covered_old + (cut - p)，
    p = cut 之前的非存档消息条数（system，可能再加一条摘要）。
    写成固定的 covered + cut - 1，第二次就会多算 1 条，恢复时会漏掉一条存档消息。
    """
    archive = []
    for i in range(1, 6):
        archive += turn(i, "x" * 3000)

    # 第一次压缩：messages = [system] + archive
    first = asyncio.run(
        compact_if_needed([SYSTEM] + archive, StubProvider("摘要一"), budget=500)
    )
    assert first.summary is not None

    # 第二次压缩：输入就是第一次的产物（下标 1 是摘要消息）
    grown = first.messages + turn(6, "y" * 3000) + turn(7, "z" * 3000)
    second = asyncio.run(
        compact_if_needed(grown, StubProvider("摘要二"), budget=500, covered=first.covered)
    )
    assert second.summary is not None

    # 用 covered 去切存档，切出来的第一条必须还是 user——轮次边界没被啃掉
    full_archive = archive + turn(6, "y" * 3000) + turn(7, "z" * 3000)
    assert full_archive[second.covered]["role"] == "user"

    # 而且第二次盖住的条数 = 第一次 + 这次新盖的，不多不少
    assert second.covered > first.covered
    assert second.messages[1]["content"].startswith(SUMMARY_PREFIX)
    assert sum(1 for m in second.messages if is_summary(m)) == 1   # 永远只有一条摘要
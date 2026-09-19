"""纠正与溯源的测试。对应第 29 讲。

两件事：
    纠正  记一条 [correction]，让下一次整理去推翻错的那条
    溯源  从流水账查出"这句话是哪次对话说的"

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.memory import (
    append_fact,
    correct_fact,
    dream,
    read_facts,
    trace_fact,
)
from providers.base import ModelReply, Provider


class StubProvider(Provider):
    """假模型：返回固定的一版新记忆，并记下它收到的提示词。"""

    def __init__(self, reply: str = "## 项目\n- 向量库用 Chroma"):
        self.reply = reply
        self.last_prompt = ""

    async def chat(self, messages, tools=None):
        self.last_prompt = messages[-1]["content"]
        return ModelReply(content=self.reply)


@pytest.fixture
def 现场(tmp_path):
    """一套记忆现场，全在临时目录里。"""

    class 记忆:
        memory = tmp_path / "MEMORY.md"
        history = tmp_path / "history.jsonl"
        cursor = tmp_path / ".dream_cursor"
        snapshots = tmp_path / "snapshots"

        def 整理(self, provider):
            return asyncio.run(
                dream(
                    provider,
                    memory_path=self.memory,
                    history_path=self.history,
                    cursor_path=self.cursor,
                    snapshot_dir=self.snapshots,
                )
            )

        def 总账(self) -> str:
            return self.memory.read_text(encoding="utf-8") if self.memory.exists() else ""

    return 记忆()


# ── 纠正 ─────────────────────────────────────────────────────


def test_correction_is_just_a_tagged_fact(现场):
    """纠正不是新机制 —— 它就是一条标签为 correction 的普通流水。

    负对照：把 correct_fact 里的 tag 改成 "durable" → 标签对不上，这条红。
    """
    correct_fact("向量库是 Chroma，不是 Pinecone", session="20260918-172000",
                 path=现场.history)

    事实 = read_facts(path=现场.history)
    assert len(事实) == 1
    assert 事实[0]["tag"] == "correction"
    assert 事实[0]["session"] == "20260918-172000"        # 出处也记下了


def test_correction_never_rewrites_the_journal(现场):
    """★ 不变量 7 仍然成立：纠正是**追加**一条，不是回头改那条错的。

    错了就再记一条冲正，让整理去合并 —— 会计的做法。
    直接涂改的话，以后谁都不知道为什么改过。

    负对照：让 correct_fact 去改写旧记录而不是追加 → 条数变成 1，这条红。
    """
    append_fact("向量库用的是 Pinecone", tag="durable", path=现场.history)
    correct_fact("向量库是 Chroma，不是 Pinecone", path=现场.history)

    事实 = read_facts(path=现场.history)
    assert len(事实) == 2                                  # 两条都在
    assert 事实[0]["content"] == "向量库用的是 Pinecone"    # 错的那条原样留着


def test_empty_correction_is_rejected(现场):
    """空的纠正没有意义，当场拒绝（由 append_fact 把关）。

    负对照：把 append_fact 里的空值检查去掉 → 不抛异常，这条红。
    """
    for 空的 in ["", "   "]:
        with pytest.raises(ValueError):
            correct_fact(空的, path=现场.history)


def test_correction_reaches_dream_with_its_tag(现场):
    """整理时，纠正要带着 [correction] 标签进提示词 —— 模型靠它知道该删哪条。

    负对照：把 build_dream_prompt 里拼标签那段去掉 → 提示词里没有标签，这条红。
    """
    现场.memory.write_text("## 项目\n- 向量库用的是 Pinecone\n", encoding="utf-8")
    correct_fact("向量库是 Chroma，不是 Pinecone", path=现场.history)

    provider = StubProvider()
    现场.整理(provider)

    assert "[correction] 向量库是 Chroma，不是 Pinecone" in provider.last_prompt
    assert "向量库用的是 Pinecone" in provider.last_prompt      # 旧总账也在，才比得出来


def test_correction_takes_effect_after_dream(现场):
    """★ 本讲的目的：纠正之后，错的那条从总账里消失了。

    这里的模型是假的（真模型的表现见手动验收），测的是**管道通不通**：
    纠正 → 进流水账 → 进提示词 → 新总账覆盖旧的。

    负对照：把 dream 里写 MEMORY.md 那行去掉 → 总账没变，这条红。
    """
    现场.memory.write_text("## 项目\n- 向量库用的是 Pinecone\n", encoding="utf-8")
    correct_fact("向量库是 Chroma，不是 Pinecone", path=现场.history)

    result = 现场.整理(StubProvider("## 项目\n- 向量库用 Chroma"))

    assert result.ok
    assert "Chroma" in 现场.总账()
    assert "Pinecone" not in 现场.总账()
    # 程序算的 diff 要把这次的增删都报出来
    assert "- - 向量库用的是 Pinecone" in result.diff
    assert "+ - 向量库用 Chroma" in result.diff


# ── 溯源 ─────────────────────────────────────────────────────


def test_trace_finds_the_originating_session(现场):
    """★ 从一句可疑的话，查回它是哪次对话说的。

    靠的是第 25 讲埋的 session 字段 —— 当时写的理由就是"第 29 讲要用"。

    负对照：把 trace_fact 里的匹配条件改成精确相等 → 找不到，这条红。
    """
    append_fact("向量库用的是 Pinecone", tag="durable",
                session="20260918-172000", path=现场.history)
    append_fact("用户用中文", tag="permanent",
                session="20260918-180000", path=现场.history)

    命中 = trace_fact("Pinecone", path=现场.history)

    assert len(命中) == 1
    assert 命中[0]["session"] == "20260918-172000"


def test_trace_is_case_insensitive(现场):
    """你多半是从记忆里随手复制一个词过来的，大小写不该卡人。

    负对照：把 trace_fact 里的两个 .lower() 去掉 → 三种写法只有一种命中，这条红。
    """
    append_fact("向量库用的是 Pinecone", tag="durable", path=现场.history)

    for 写法 in ["Pinecone", "pinecone", "PINECONE"]:
        assert len(trace_fact(写法, path=现场.history)) == 1


def test_trace_returns_newest_first(现场):
    """从新到旧 —— 最近记的那条最可能是你要查的。

    尤其是纠正：你刚敲完 /correct 再 /why，那条纠正该排在最前面。

    负对照：把 trace_fact 里的 reverse=True 去掉 → 顺序反了，这条红。
    """
    append_fact("向量库用的是 Pinecone", tag="durable", path=现场.history)
    correct_fact("向量库是 Chroma，不是 Pinecone", path=现场.history)

    命中 = trace_fact("Pinecone", path=现场.history)

    assert [f["tag"] for f in 命中] == ["correction", "durable"]


def test_trace_with_no_match_is_empty_not_an_error(现场):
    """查不到不是错误 —— 总账是合并过的，词对不上很正常。

    负对照：让 trace_fact 在查不到时抛异常 → 这条红。
    """
    append_fact("用户用中文", tag="permanent", path=现场.history)

    assert trace_fact("根本没提过的词", path=现场.history) == []


def test_blank_keyword_matches_nothing(现场):
    """空关键词不该把整本流水账都倒出来。

    负对照：把 `if not 词: return []` 去掉 → 空串匹配所有，这条红。
    """
    append_fact("用户用中文", tag="permanent", path=现场.history)

    for 空的 in ["", "   "]:
        assert trace_fact(空的, path=现场.history) == []

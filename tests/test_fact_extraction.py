"""从摘要里提炼事实、写进流水账的测试。对应第 26 讲。

规矩：**压缩时顺便提炼**，但提炼失败绝不连累压缩。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent import memory
from agent.compaction import compact_if_needed
from agent.memory import parse_facts, read_facts, record_facts
from providers.base import ModelReply, Provider


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """把流水账整体指到临时文件，**保证测试碰不到真实记忆**。

    这就是 append_fact/record_facts 的 path 默认值写成 None、
    在函数里才取 HISTORY_FILE 的原因：写成 `path=HISTORY_FILE` 的话，
    默认值在 import 那一刻就定死了，这里怎么换都没用。
    """
    fake = tmp_path / "memory" / "history.jsonl"
    monkeypatch.setattr(memory, "HISTORY_FILE", fake)
    return fake


# ── 认字：parse_facts ────────────────────────────────────────


def test_only_fact_lines_are_picked_up():
    """摘要里的散文不该进流水账，只有 [标签] 那种行才算。

    负对照：把 _FACT_LINE 里的方括号部分改成可选 → 散文行也被认成事实，这条红。
    """
    summary = """1. 任务目标：把记忆系统做完
2. 当前状态：第 25 讲已完成，66 个测试全绿
3. 重要发现：补丁打不上的原因是文件末尾少了换行

值得长期记住的事实
- [permanent] 用户用 Windows，命令行是 PowerShell
- [durable] 项目的流水账在 data/memory/history.jsonl
"""
    assert parse_facts(summary) == [
        ("permanent", "用户用 Windows，命令行是 PowerShell"),
        ("durable", "项目的流水账在 data/memory/history.jsonl"),
    ]


def test_loose_formats_are_tolerated():
    """模型不会每次都严格照格式写，认得出就别丢。

    负对照：把 _FACT_LINE 里的【】和编号前缀去掉 → 后三种写法认不出，这条红。
    """
    text = "\n".join([
        "- [permanent] 破折号",
        "* [durable] 星号",
        "[ephemeral] 没有前缀",
        "3. 【durable】编号加中文方括号",
    ])

    assert [c for _, c in parse_facts(text)] == ["破折号", "星号", "没有前缀", "编号加中文方括号"]


def test_skip_tag_and_empty_content_are_dropped():
    """[skip] 是"只供审计别存"（nanobot 的约定）；"（无）"是模型在说没有。

    负对照：把 `if tag == "skip"` 那行去掉 → skip 那条被记下来，这条红。
    """
    text = "\n".join([
        "- [skip] 这条只供审计",
        "- [ephemeral] （无）",
        "- [durable] 无",
        "- [permanent] 这条才是真的",
    ])

    assert parse_facts(text) == [("permanent", "这条才是真的")]


def test_duplicate_facts_are_written_once():
    """同一条事实模型有时会写两遍（正文里一遍、清单里一遍）。

    负对照：把 seen 那套去重去掉 → 返回 2 条，这条红。
    """
    text = "- [durable] 项目用 Chroma\n- [durable] 项目用 Chroma"

    assert len(parse_facts(text)) == 1


def test_unknown_tag_is_not_treated_as_a_fact():
    """模型自创标签（比如 [important]）时，这一行不算事实行。

    负对照：把 `tag not in VALID_TAGS` 那个判断去掉 → 多出一条，这条红。
    """
    assert parse_facts("- [important] 自创的标签") == []


# ── 落盘：record_facts ───────────────────────────────────────


def test_record_facts_writes_tag_and_session(journal):
    """写进流水账时，标签和出处都要留下来——第 27、29 讲都要用。

    负对照：把 record_facts 里的 tag=tag 去掉 → 全变成 durable，这条红。
    """
    summary = "- [permanent] 用户用 Windows\n- [correction] 模型名是 deepseek-flash"

    assert record_facts(summary, session="20260917-181500") == 2

    facts = read_facts(path=journal)
    assert [(f["tag"], f["content"]) for f in facts] == [
        ("permanent", "用户用 Windows"),
        ("correction", "模型名是 deepseek-flash"),
    ]
    assert all(f["session"] == "20260917-181500" for f in facts)


def test_nothing_worth_remembering_is_not_an_error(journal):
    """有的对话就是没有值得长期记住的东西，这是正常结果。

    负对照：让 record_facts 在没有事实时抛异常 → 这条红。
    """
    assert record_facts("1. 任务目标：闲聊\n2. 当前状态：没什么进展") == 0
    assert not journal.exists()      # 连文件都不该被建出来


# ── 接线：压缩时顺便提炼 ─────────────────────────────────────


class FakeProvider(Provider):
    """假的模型：不管问什么，都返回事先准备好的那段摘要。

    和 tests/test_compaction.py 的 StubProvider 是同一个套路——
    压缩要调模型，测试里不能真调（慢、花钱、结果还不稳定）。
    """

    def __init__(self, reply: str):
        self.reply = reply

    async def chat(self, messages, tools=None):
        return ModelReply(content=self.reply)


def _long_history(n: int = 60) -> list[dict]:
    """造一段够长的历史，长到一定会触发压缩。"""
    msgs = [{"role": "system", "content": "系统提示"}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"第{i}个问题。" + "填充内容。" * 2000})
        msgs.append({"role": "assistant", "content": f"第{i}个回答。" + "填充内容。" * 2000})
    return msgs


def test_compaction_also_records_facts(journal):
    """★ 本讲的核心：压缩成功之后，事实自动进了流水账。

    负对照：把 compact_if_needed 里那句 record_facts 去掉 → 流水账是空的，这条红。
    """
    summary = "1. 任务目标：测试\n\n值得长期记住的事实\n- [permanent] 用户用 Windows"
    out = asyncio.run(
        compact_if_needed(
            _long_history(), FakeProvider(summary), session="20260917-181500"
        )
    )

    assert out.summary is not None                    # 确实压了
    assert [f["content"] for f in read_facts(path=journal)] == ["用户用 Windows"]


def test_failed_compaction_records_nothing(journal):
    """压缩失败（模型返回空）时不该往流水账里写任何东西。

    负对照：把 record_facts 挪到 `if summary is None` 判断之前 → 流水账里多出东西，这条红。
    """
    out = asyncio.run(
        compact_if_needed(_long_history(), FakeProvider(""), session="20260917-181500")
    )

    assert out.summary is None                        # 没压成
    assert not journal.exists()


def test_journal_failure_does_not_break_compaction(journal, monkeypatch):
    """★ 记忆是锦上添花，压缩才是正事：写流水账炸了，压缩照样算成功。

    负对照：把 record_facts 里的 try/except 去掉 → 异常冒到上层，压缩失败，这条红。
    """
    def 写盘就炸(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr(memory, "append_fact", 写盘就炸)

    summary = "1. 任务目标：测试\n- [permanent] 用户用 Windows"
    out = asyncio.run(
        compact_if_needed(_long_history(), FakeProvider(summary), session="x")
    )

    assert out.summary is not None                    # 压缩仍然成功
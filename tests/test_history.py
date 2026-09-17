"""流水账 history.jsonl 的测试。对应第 25 讲。

规矩：**只往后加，永不改写**；编号自增，靠它记住"整理到哪了"。
实现在 agent/memory.py 下半部分。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import json

import pytest

from agent.memory import append_fact, read_facts


@pytest.fixture
def journal(tmp_path):
    """一个干净的流水账文件路径（还不存在，第一次 append 时才创建）。"""
    return tmp_path / "memory" / "history.jsonl"


def test_cursor_starts_at_one_and_increments(journal):
    """编号从 1 开始，一条一号，连续往上加。

    负对照：把 _next_cursor 的 default 改成 1 → 第一条变成 2 号，这条红。
    """
    assert append_fact("用户的项目代号是 蓝鲸-7", path=journal) == 1
    assert append_fact("用户在 USM 读研", path=journal) == 2
    assert append_fact("用户用 Windows", path=journal) == 3


def test_read_facts_since_returns_only_new_ones(journal):
    """Dream 的用法：拿着"上次整理到第 2 条"，只要 3 号往后的。

    负对照：把 read_facts 里的 `> since` 改成 `>= since` → 多出 2 号，这条红。
    """
    for text in ["一", "二", "三", "四"]:
        append_fact(text, path=journal)

    assert [f["content"] for f in read_facts(path=journal)] == ["一", "二", "三", "四"]
    assert [f["content"] for f in read_facts(since=2, path=journal)] == ["三", "四"]
    assert read_facts(since=99, path=journal) == []


def test_append_never_rewrites_existing_lines(journal):
    """★ 不变量：只追加，永不改写。老记录的每一个字节都不能变。

    负对照：把 open(path, "a") 改成 "w" → 前面的记录没了，这条红。
    """
    append_fact("第一条", path=journal)
    第一行 = journal.read_text(encoding="utf-8").splitlines()[0]

    append_fact("第二条", path=journal)
    append_fact("第三条", path=journal)

    行 = journal.read_text(encoding="utf-8").splitlines()
    assert len(行) == 3
    assert 行[0] == 第一行            # 逐字节相同


def test_session_key_is_recorded_for_traceability(journal):
    """每条事实要记住来自哪次会话——第 29 讲"从结论追回原始对话"靠它。

    负对照：把 record 里的 "session" 字段删掉 → KeyError，这条红。
    """
    append_fact("用户的项目代号是 蓝鲸-7", session="20260917-161500", path=journal)

    fact = read_facts(path=journal)[0]
    assert fact["session"] == "20260917-161500"


def test_empty_content_is_rejected(journal):
    """空记录没意义，还会占掉 Dream 的一次整理名额，当场拒绝。

    负对照：把 `if not text: raise` 去掉 → 不抛异常，这条红。
    """
    for 空的 in ["", "   ", "\n\t "]:
        with pytest.raises(ValueError):
            append_fact(空的, path=journal)

    assert not journal.exists()       # 连文件都不该被建出来


def test_overlong_content_is_truncated(journal):
    """保险丝：模型偶尔会把整段输入当摘要吐回来，截断兜住它。

    负对照：把截断那两行去掉 → 长度是 10000，这条红。
    """
    from agent.memory import MAX_CONTENT_CHARS

    append_fact("啊" * 10_000, path=journal)

    content = read_facts(path=journal)[0]["content"]
    assert len(content) == MAX_CONTENT_CHARS
    assert content.endswith("…")


def test_broken_line_does_not_break_numbering(journal):
    """坏行跳过之后，新编号仍然比所有旧编号大，不会撞号。

    这就是"取最大值 + 1"而不是"条数 + 1"的原因：
    条数会因为跳过坏行而变少，最大值不会。

    负对照：把 _next_cursor 改成 `len(facts) + 1` → 新编号是 3，和已有的 3 撞号，这条红。
    """
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps({"cursor": 1, "content": "一"}, ensure_ascii=False) + "\n"
        + "这一行坏掉了\n"
        + json.dumps({"cursor": 3, "content": "三"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning):
        assert append_fact("四", path=journal) == 4


def test_bogus_cursors_are_ignored(journal):
    """外部工具手改过文件时，编号可能是 true / -1 / "5" 这种。

    让它们进来会把 Dream 的游标算歪。特别注意 true：
    Python 里 isinstance(True, int) 是 True，不专门挡就会被当成 1 号。

    负对照：把 _valid_cursor 里的 isinstance(value, bool) 判断去掉 → true 被当成编号，这条红。
    """
    journal.parent.mkdir(parents=True)
    journal.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False)
            for r in [
                {"cursor": True, "content": "布尔"},
                {"cursor": -1, "content": "负数"},
                {"cursor": "5", "content": "字符串"},
                {"cursor": 2, "content": "正常的"},
            ]
        ) + "\n",
        encoding="utf-8",
    )

    assert [f["content"] for f in read_facts(path=journal)] == ["正常的"]
    assert append_fact("新的", path=journal) == 3
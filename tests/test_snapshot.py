"""快照、diff 与回滚的测试。对应第 28 讲。

规矩：**改记忆之前先拍快照**（不变量 9）；**diff 由程序算，不问模型**。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent import memory
from agent.memory import (
    KEEP_SNAPSHOTS,
    append_fact,
    diff_memory,
    dream,
    list_snapshots,
    restore_snapshot,
    snapshot_memory,
)
from providers.base import ModelReply, Provider


class StubProvider(Provider):
    """假模型：返回固定的一版新记忆。"""

    def __init__(self, reply: str = "## 新的\n- 内容"):
        self.reply = reply

    async def chat(self, messages, tools=None):
        return ModelReply(content=self.reply)


@pytest.fixture
def 现场(tmp_path):
    """一套完整的记忆现场：总账、流水账、游标、快照目录，全在临时目录里。"""

    class 记忆:
        memory = tmp_path / "MEMORY.md"
        history = tmp_path / "history.jsonl"
        cursor = tmp_path / ".dream_cursor"
        snapshots = tmp_path / "snapshots"

        def 写总账(self, text: str) -> None:
            self.memory.write_text(text, encoding="utf-8")

        def 总账(self) -> str:
            return self.memory.read_text(encoding="utf-8") if self.memory.exists() else ""

        def 拍一张(self):
            return snapshot_memory(self.memory, self.snapshots)

        def 快照列表(self):
            return list_snapshots(self.snapshots)

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

        def 记流水(self, content: str) -> None:
            append_fact(content, path=self.history)

    return 记忆()


# ── 快照：拍、列、清理 ───────────────────────────────────────


def test_no_memory_yet_means_no_snapshot(现场):
    """第一次整理时还没有记忆，没什么可存的 —— 返回 None，不是报错。

    负对照：把 snapshot_memory 里的 `if not memory_path.exists()` 去掉
    → FileNotFoundError，这条红。
    """
    assert 现场.拍一张() is None
    assert 现场.快照列表() == []


def test_snapshot_copies_current_content(现场):
    """快照里存的是**拍照那一刻**的内容，之后总账再怎么变都不影响它。

    负对照：把 write_text 那行改成写空字符串 → 内容对不上，这条红。
    """
    现场.写总账("## 项目\n- 提示词外置在 templates/")
    快照 = 现场.拍一张()

    现场.写总账("完全不一样的内容")            # 总账被改了

    assert "提示词外置在 templates/" in 快照.read_text(encoding="utf-8")


def test_newest_snapshot_comes_first(现场):
    """列出来的顺序是从新到旧 —— /memory-undo 要的"上一版"就是第 0 份。

    负对照：把 list_snapshots 里的 `reverse=True` 去掉 → 顺序反了，这条红。
    """
    现场.写总账("第一版")
    第一份 = 现场.拍一张()
    现场.写总账("第二版")
    第二份 = 现场.拍一张()          # 紧接着再拍一张，中间不到一毫秒也要能区分

    列表 = 现场.快照列表()

    assert 列表 == [第二份, 第一份]
    assert 第一份 != 第二份          # 名字没撞车（时间戳精确到毫秒）


def test_colliding_snapshots_get_later_names(现场, monkeypatch):
    """两次拍照落在同一微秒时，后一张的名字必须排在前一张**后面**。

    顺序不能乱：list_snapshots 靠文件名排序分新旧，排反了"退回上一版"
    就会退到错的那一版。

    微秒精度下自然撞车几乎不会发生（实测相邻两次写盘至少隔 19 微秒），
    所以这里**把时钟冻住**，强行造出撞车来测那段代码。

    负对照：把 _free_snapshot_path 里的 `微秒 += 1` 改成 `-= 1`
    → 后拍的排到前面去了，这条红。
    """
    from datetime import datetime as 真时钟

    冻住 = 真时钟(2026, 9, 18, 16, 53, 7, 500000)
    monkeypatch.setattr(memory, "datetime", type("假时钟", (), {"now": lambda: 冻住}))

    现场.写总账("第一版")
    第一份 = 现场.拍一张()
    现场.写总账("第二版")
    第二份 = 现场.拍一张()

    assert 第一份 != 第二份                      # 没有互相覆盖
    assert 第二份.name > 第一份.name             # 后拍的排在后面
    assert 现场.快照列表() == [第二份, 第一份]    # 所以"最新的那份"认得出来


def test_old_snapshots_are_pruned(现场):
    """只留最近 KEEP_SNAPSHOTS 份，更早的删掉 —— 不然一天攒几十个文件。

    负对照：把 snapshot_memory 里清理那几行去掉 → 剩 KEEP_SNAPSHOTS+3 份，这条红。
    """
    现场.snapshots.mkdir(parents=True)
    for i in range(KEEP_SNAPSHOTS + 3):           # 先手工塞满
        (现场.snapshots / f"MEMORY-20260101-{i:06d}.md").write_text("x", encoding="utf-8")

    现场.写总账("再来一版")
    现场.拍一张()                                  # 这一次会顺便清理

    assert len(现场.快照列表()) == KEEP_SNAPSHOTS


# ── diff：程序算，不问模型 ───────────────────────────────────


def test_diff_shows_both_added_and_removed_lines():
    """★ 本讲的核心：**被悄悄漏抄的那一行，diff 要报出来。**

    第 28 讲的投毒现场里，模型在并入新事实时把"提示词外置"那条丢了，
    而屏幕上只说"整理了 1 条事实"——不算 diff 的话，这件事根本看不见。

    负对照：把 diff_memory 里的 `("+ ", "- ")` 改成只留 `"+ "`
    → 被删的行不报，这条红。
    """
    旧 = "- 从零复刻 nanobot\n- 提示词外置在 templates/"
    新 = "- 从零复刻 nanobot\n- 向量库用 Pinecone"

    改动 = diff_memory(旧, 新)

    assert "+ - 向量库用 Pinecone" in 改动          # 多了什么
    assert "- - 提示词外置在 templates/" in 改动    # ★ 少了什么
    assert "从零复刻 nanobot" not in 改动           # 没变的行不出现


def test_no_change_means_empty_diff():
    """一个字没改时返回空串，调用方据此决定要不要打印。

    负对照：把最后的 join 改成无条件返回整段 compare 结果 → 非空，这条红。
    """
    assert diff_memory("## 用户\n- 用中文", "## 用户\n- 用中文") == ""


def test_dream_reports_the_real_diff(现场):
    """★ 整理完之后，DreamResult 里带着**程序算出来的**实际改动。

    这是本讲和第 27 讲的分界：第 27 讲只说"整理了 N 条"，
    第 28 讲要能回答"到底改了哪几行"。

    负对照：把 dream 里 `diff=diff_memory(旧总账, 新总账)` 去掉
    → diff 是空的，这条红。
    """
    现场.写总账("## 项目\n- 提示词外置在 templates/")
    现场.记流水("向量库用 Pinecone")

    result = 现场.整理(StubProvider("## 项目\n- 向量库用 Pinecone"))

    assert "+ - 向量库用 Pinecone" in result.diff
    assert "- - 提示词外置在 templates/" in result.diff      # 漏抄的那条被抓住了


# ── 回滚：不变量 9 ───────────────────────────────────────────


def test_dream_snapshots_before_overwriting(现场):
    """★ 不变量 9：Dream 覆盖记忆之前，必须先留一份快照。

    负对照：把 dream 里 snapshot_memory 那行去掉 → 一份快照都没有，这条红。
    """
    现场.写总账("## 项目\n- 这是整理前的内容")
    现场.记流水("新事实")

    现场.整理(StubProvider())

    快照 = 现场.快照列表()
    assert len(快照) == 1
    assert "这是整理前的内容" in 快照[0].read_text(encoding="utf-8")


def test_failed_dream_leaves_no_snapshot(现场):
    """整理失败时连快照都不该留 —— 文件根本没被改过，存它干什么。

    负对照：把 snapshot_memory 挪到 dream 函数开头 → 失败也留快照，这条红。
    """
    现场.写总账("## 项目\n- 原内容")
    现场.记流水("新事实")

    result = 现场.整理(StubProvider(reply=""))       # 模型返回空 = 失败

    assert not result.ok
    assert 现场.快照列表() == []


def test_undo_restores_the_previous_version(现场):
    """★ 投毒之后能退回去 —— 这是整讲的目的。

    负对照：把 restore_snapshot 里的 write_text 那行去掉 → 内容没变回来，这条红。
    """
    现场.写总账("## 项目\n- 提示词外置在 templates/")
    现场.记流水("向量库用 Pinecone")
    现场.整理(StubProvider("## 项目\n- 向量库用 Pinecone"))   # 被投毒了

    assert "Pinecone" in 现场.总账()

    ok, note = restore_snapshot(0, 现场.memory, 现场.snapshots)

    assert ok
    assert "提示词外置在 templates/" in 现场.总账()          # 退回去了
    assert "Pinecone" not in 现场.总账()


def test_undo_is_itself_undoable(现场):
    """★ 撤销也要能被撤销：回滚之前，当前这版也要存成快照。

    不然回滚就变成了另一个不可逆操作——方向反过来的同一个坑。

    负对照：把 restore_snapshot 里那句 snapshot_memory 去掉 → 退不回来，这条红。
    """
    现场.写总账("第一版")
    现场.记流水("新事实")
    现场.整理(StubProvider("第二版"))
    assert 现场.总账().strip() == "第二版"

    restore_snapshot(0, 现场.memory, 现场.snapshots)         # 退回第一版
    assert 现场.总账().strip() == "第一版"

    restore_snapshot(0, 现场.memory, 现场.snapshots)         # 再退一次 = 回到第二版
    assert 现场.总账().strip() == "第二版"


def test_undo_without_snapshots_says_so(现场):
    """一份快照都没有时，说清楚为什么，别静默失败。

    负对照：让它在没有快照时返回 (True, ...) → 这条红。
    """
    ok, note = restore_snapshot(0, 现场.memory, 现场.snapshots)

    assert not ok
    assert "还没有任何快照" in note


def test_undo_beyond_range_says_how_many_there_are(现场):
    """要第 5 份但只有 1 份时，告诉用户到底有几份。

    负对照：把 `if which >= len(snapshots)` 那段去掉 → IndexError，这条红。
    """
    现场.写总账("一版")
    现场.拍一张()

    ok, note = restore_snapshot(5, 现场.memory, 现场.snapshots)

    assert not ok
    assert "只有 1 份" in note
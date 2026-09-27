"""记忆系统的四道加固。对应第 35 讲（阶段五返工）。

这一批测试都不是"新功能"，而是**把已经撞出来的坑钉住**：

    追加粘连   断电留下的半行，不能再连累下一条记录
    游标越位   顺序乱过时，游标不能推过头把中间几条永久跳过
    缩水覆盖   模型回一句"好的。"，不能拿它换掉整本记忆
    负数序号   /memory-undo -1 不能静默退到最旧的一版

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
跑负对照之前记得清 __pycache__，否则可能跑的是旧字节码。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent.memory import (
    MIN_KEEP_RATIO,
    append_fact,
    dream,
    read_dream_cursor,
    read_facts,
    restore_snapshot,
)
from providers.base import ModelReply, Provider
from storage.jsonl import append_record, iter_records


class StubProvider(Provider):
    """假模型：返回一段固定的"新记忆"。"""

    def __init__(self, reply: str = "## 用户\n- 用中文\n- 用 Windows\n- 偏好条目式"):
        self.reply = reply

    async def chat(self, messages, tools=None):
        return ModelReply(content=self.reply)


@pytest.fixture
def store(tmp_path):
    """一套记忆现场，全在临时目录里。"""

    class Store:
        memory = tmp_path / "MEMORY.md"
        history = tmp_path / "history.jsonl"
        cursor = tmp_path / ".dream_cursor"
        snapshots = tmp_path / "snapshots"

        def run_dream(self, provider):
            return asyncio.run(
                dream(
                    provider,
                    memory_path=self.memory,
                    history_path=self.history,
                    cursor_path=self.cursor,
                    snapshot_dir=self.snapshots,
                )
            )

        def text(self) -> str:
            return self.memory.read_text(encoding="utf-8") if self.memory.exists() else ""

        def snapshot_count(self) -> int:
            return len(list(self.snapshots.glob("MEMORY-*.md"))) if self.snapshots.exists() else 0

    return Store()


# ── 一、断电留下的半行，不能连累下一条 ───────────────────────


def test_append_after_a_half_line_keeps_the_new_record(store):
    """★ 上一次写到一半断电，这一次追加的记录必须还能读回来。

    以前是直接 open("a") 就写，新记录会焊在没有换行的半行后面，
    两条粘成一行，下次读的时候一起被当成坏行扔掉。

    负对照：把 append_record 里补换行那段去掉 → 读回来 0 条，这条红。
    """
    store.history.write_text(
        '{"cursor": 1, "tag": "durable", "content": "断电前那条", "session": ""}\n'
        '{"cursor": 2, "tag": "durable", "conte',          # ← 写到一半断电
        encoding="utf-8",
    )

    append_fact("断电之后的第一条", path=store.history)

    with pytest.warns(UserWarning):                        # 半行本身仍然被跳过
        contents = [f["content"] for f in read_facts(path=store.history)]

    assert "断电之后的第一条" in contents                   # ★ 新的这条活下来了
    assert "断电前那条" in contents                         # 半行之前的也没受影响


def test_append_record_does_not_touch_a_healthy_file(store):
    """文件末尾本来就好好的时候，不许多补空行（不然文件里全是空行）。

    负对照：把 unterminated 判断改成无条件 True → 多出一个空行，这条红。
    """
    for i in (1, 2):
        append_record(store.history, {"cursor": i, "content": f"第 {i} 条"})

    raw = store.history.read_text(encoding="utf-8")
    assert raw.count("\n\n") == 0
    assert len(list(iter_records(store.history))) == 2


# ── 二、游标不能推过头 ───────────────────────────────────────


def test_read_facts_is_sorted_by_cursor(store):
    """流水账顺序乱过时，读出来也要按编号从小到大——文档一直是这么写的。

    负对照：把 read_facts 里的 facts.sort(...) 去掉 → 顺序是 1、3、2，这条红。
    """
    for c in (1, 3, 2):
        append_record(store.history, {"cursor": c, "tag": "durable",
                                      "content": f"第 {c} 条", "session": ""})

    assert [f["cursor"] for f in read_facts(path=store.history)] == [1, 2, 3]


def test_cursor_advances_to_the_largest_in_the_batch(store, monkeypatch):
    """★ 游标推到这批里**最大**的编号，不是"最后一条"的编号。

    推过头的后果是静默的：被跳过的那几条还躺在流水账里，
    但 read_facts(since=游标) 再也不会返回它们，永远不会被整理。

    上面那条测试已经保证 read_facts 会排序，所以这里**故意把排序绕过去**
    （直接换掉 read_facts），单独验 dream 自己的这道保险。两道都在，
    才经得起以后谁把其中一道改掉。

    负对照：把 dream 里的 max(...) 改回 batch[-1]["cursor"] → 游标是 2，这条红。
    """
    乱序 = [{"cursor": c, "tag": "durable", "content": f"第 {c} 条", "session": ""}
            for c in (1, 3, 2)]
    monkeypatch.setattr("agent.memory.read_facts", lambda since=0, path=None: 乱序)

    result = store.run_dream(StubProvider())

    assert result.ok and result.processed == 3
    assert read_dream_cursor(store.cursor) == 3            # 不是 2


# ── 三、缩水闸门 ─────────────────────────────────────────────


def test_a_tiny_reply_does_not_replace_the_whole_memory(store):
    """★ 模型回一句"好的。"，整本记忆不能就这么没了。

    这是实测撞出来的：以前代码只判空，三个字照样覆盖，游标还往前推，
    屏幕上还报"整理了 N 条事实"。

    负对照：把 dream 里 MIN_KEEP_RATIO 那段去掉 → 记忆变成"好的。"，这条红。
    """
    store.memory.write_text(
        "## 用户\n- Himeko，Windows + PowerShell\n- 偏好条目式\n\n"
        "## 项目\n- 向量库用 Chroma\n- 提示词外置在 templates/\n",
        encoding="utf-8",
    )
    append_fact("新事实", path=store.history)

    result = store.run_dream(StubProvider("好的。"))

    assert not result.ok
    assert "缩水" in result.note
    assert "提示词外置在 templates/" in store.text()       # 老内容一个字没少
    assert read_dream_cursor(store.cursor) == 0             # 游标没动，下次还能再整理
    assert store.snapshot_count() == 0                      # 文件没改，快照也不用存


def test_a_normal_shrink_still_goes_through(store):
    """合并、删过期本来就会让记忆变短，正常幅度的缩水不能被误伤。

    负对照：把 MIN_KEEP_RATIO 调到 0.9 → 这次正常整理被拦下，这条红。
    """
    store.memory.write_text("## 项目\n- 甲\n- 乙\n- 丙\n- 丁\n", encoding="utf-8")
    append_fact("甲乙丙丁其实是一回事", path=store.history)

    result = store.run_dream(StubProvider("## 项目\n- 甲乙丙丁"))

    assert result.ok
    assert store.text().strip() == "## 项目\n- 甲乙丙丁"
    # 顺带确认这个例子确实是在闸门之内，不是碰巧擦边过的
    assert len("## 项目\n- 甲乙丙丁") > len("## 项目\n- 甲\n- 乙\n- 丙\n- 丁") * MIN_KEEP_RATIO


def test_unchanged_memory_skips_the_write_but_moves_the_cursor(store):
    """模型认为不用改时：不写盘、不拍快照，但游标要推进。

    不推进的话，同一批事实每次退出都要重整理一遍，白花钱。

    负对照：把 `if new_memory == old_memory` 那段去掉 → 多出一份快照，这条红。
    """
    store.memory.write_text("## 用户\n- 用中文\n", encoding="utf-8")
    append_fact("用户用中文", path=store.history)

    result = store.run_dream(StubProvider("## 用户\n- 用中文"))

    assert result.ok and result.processed == 1
    assert store.snapshot_count() == 0
    assert read_dream_cursor(store.cursor) == 1


def test_dream_leaves_no_temp_file(store):
    """原子写用的临时文件，写完必须已经换成正式文件，目录里不能留下 .tmp。

    负对照：把 _atomic_write 里的 os.replace 改成 shutil.copy → 多一个 .tmp，这条红。
    """
    append_fact("新事实", path=store.history)

    store.run_dream(StubProvider())

    assert store.text().startswith("## 用户")
    assert list(store.memory.parent.glob("*.tmp")) == []


# ── 四、负数序号 ─────────────────────────────────────────────


def test_negative_index_is_rejected(store):
    """/memory-undo -1 要报错，不能静默退回**最旧**的那一版。

    负对照：把 restore_snapshot 里的 `if which < 0` 去掉
    → 返回 (True, ...)，而且退到了最旧的一版，这条红。
    """
    store.memory.write_text("第一版", encoding="utf-8")
    append_fact("新事实", path=store.history)
    store.run_dream(StubProvider("第二版的记忆内容，长度差不多"))

    ok, note = restore_snapshot(-1, store.memory, store.snapshots)

    assert not ok
    assert "0" in note
    assert "第一版" not in store.text()                     # 没有被偷偷退回去


def test_index_zero_still_works(store):
    """挡负数不能把正常的"退回上一版"也挡掉。

    负对照：把判断写成 `if which <= 0` → 这条红。
    """
    store.memory.write_text("原来的记忆内容，长度差不多", encoding="utf-8")
    append_fact("新事实", path=store.history)
    store.run_dream(StubProvider("新的记忆内容，长度也差不多"))

    ok, _ = restore_snapshot(0, store.memory, store.snapshots)

    assert ok
    assert "原来的记忆内容" in store.text()


# ── 顺带：流水账写出来的仍然是一行一条合法 JSON ────────────────


def test_every_appended_line_is_valid_json(store):
    """加了补换行那段之后，每一行仍然是一条独立的合法 JSON。

    负对照：把 append_record 里的 json.dumps 换成 str(record) → 解析失败，这条红。
    """
    append_fact("第一条", path=store.history)
    append_fact("第二条", path=store.history)

    lines = store.history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["content"] for line in lines] == ["第一条", "第二条"]
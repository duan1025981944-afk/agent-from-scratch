"""摘要的持久化与恢复。对应第 18 讲。"""
from __future__ import annotations

import pytest

from agent.compaction import is_summary, restore_history
from session import manager
from session import messages as store


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    """把会话目录挪到临时目录，测试不碰真实的 data/sessions/。"""
    monkeypatch.setattr(store, "SESSIONS_DIR", tmp_path)
    return tmp_path


def msgs(n: int) -> list[dict]:
    return [{"role": "user", "content": f"问题 {i}"} for i in range(n)]


# ──────────────────────── 存档不动 ────────────────────────

def test_summary_record_does_not_touch_the_transcript(sessions_dir):
    """写摘要只是追加一张便条，存档里的原文一条不少、一字不改。"""
    session = manager.create("m", "/ws")
    session.save_turn(msgs(4))
    session.save_summary("这是摘要", covered=3)

    assert store.load_messages(session.key) == msgs(4)


def test_count_messages_ignores_summary_records(sessions_dir):
    """摘要记录不是消息，不该被算进条数（--list 里显示的那个数）。"""
    session = manager.create("m", "/ws")
    session.save_turn(msgs(4))
    session.save_summary("这是摘要", covered=3)

    assert store.count_messages(session.key) == 4


# ──────────────────────── 恢复 ────────────────────────

def test_resume_replaces_covered_messages_with_the_summary(sessions_dir):
    """恢复后：前 covered 条换成一条摘要，其余原样。"""
    session = manager.create("m", "/ws")
    session.save_turn(msgs(4))
    session.save_summary("这是摘要", covered=3)

    reloaded = manager.resume(session.key)
    restored = restore_history(reloaded.history, reloaded.summary)

    assert is_summary(restored[0])
    assert "这是摘要" in restored[0]["content"]
    assert restored[1:] == msgs(4)[3:]
    assert reloaded.covered == 3


def test_latest_summary_wins(sessions_dir):
    """压了两次就有两条记录，恢复时用最后那条。"""
    session = manager.create("m", "/ws")
    session.save_turn(msgs(6))
    session.save_summary("旧摘要", covered=2)
    session.save_summary("新摘要", covered=5)

    reloaded = manager.resume(session.key)
    assert reloaded.covered == 5
    assert "新摘要" in restore_history(reloaded.history, reloaded.summary)[0]["content"]


def test_resume_without_summary_returns_full_history(sessions_dir):
    """没压缩过的会话，恢复出来就是全文。"""
    session = manager.create("m", "/ws")
    session.save_turn(msgs(3))

    reloaded = manager.resume(session.key)
    assert restore_history(reloaded.history, reloaded.summary) == msgs(3)
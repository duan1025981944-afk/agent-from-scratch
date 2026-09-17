"""坏行容错的测试。对应第 25 讲。

规矩：**一行坏掉，只丢那一行，其余照常读出来**，并且警告一次。
实现在 storage/jsonl.py 的 iter_records()。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import pytest

from session import messages
from storage.jsonl import iter_records


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    """把会话目录指到临时目录，返回一个可以随便写的会话文件路径。"""
    monkeypatch.setattr(messages, "SESSIONS_DIR", tmp_path)
    return tmp_path / "20260917-160000.jsonl"


def test_half_written_line_does_not_kill_the_session(session_file):
    """★ 老坑：断电留下半行，整个会话读不出来。

    第 25 讲之前：json.loads 直接抛 JSONDecodeError，几百轮对话全丢。
    现在：坏的那行跳过，前后的消息照常读出来。

    负对照：把 iter_records 里的 try/except 去掉 → JSONDecodeError，这条红。
    """
    session_file.write_text(
        '{"_meta": {"model": "deepseek-flash"}}\n'
        '{"role": "user", "content": "第一句"}\n'
        '{"role": "assistant", "content": "第二句"}\n'
        '{"role": "user", "content": "写到一半就断电了',      # ← 半行，没闭合
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="读不懂的行"):
        msgs = messages.load_messages("20260917-160000")

    assert [m["content"] for m in msgs] == ["第一句", "第二句"]


def test_broken_line_warns_once_not_per_line(session_file):
    """坏行常常连着一片，每行都警告会刷屏。一次就够。

    负对照：把 iter_records 里的 warned 标志去掉 → 报 3 次，这条红。
    """
    session_file.write_text(
        '{"role": "user", "content": "好的一行"}\n'
        "坏行一\n坏行二\n坏行三\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning) as caught:
        msgs = messages.load_messages("20260917-160000")

    assert len(msgs) == 1
    assert len(caught) == 1


def test_non_dict_lines_are_skipped(tmp_path):
    """能解析但形状不对的行（数组、数字、字符串）也要跳过。

    下游全是 obj["role"]、obj.get("_meta")，放进来会在别处炸，
    而且报错信息会指向一个和真正原因无关的地方——那种 bug 最难查。

    负对照：把 isinstance(obj, dict) 检查去掉 → 产出 4 条，这条红。
    """
    f = tmp_path / "x.jsonl"
    f.write_text('{"a": 1}\n[1, 2, 3]\n42\n"就是一个字符串"\n', encoding="utf-8")

    with pytest.warns(UserWarning):
        records = list(iter_records(f))

    assert records == [{"a": 1}]


def test_other_readers_also_survive(session_file):
    """load_meta / load_summary / count_messages 也走同一条路，不能只修一个。

    负对照：把 count_messages 改回 json.loads(line) → 这条红。
    """
    session_file.write_text(
        '{"_meta": {"model": "deepseek-flash"}}\n'
        '{"role": "user", "content": "一"}\n'
        "半行坏掉了\n"
        '{"role": "assistant", "content": "二"}\n'
        '{"_summary": {"text": "摘要正文", "covered": 2}}\n',
        encoding="utf-8",
    )
    key = "20260917-160000"

    # load_meta 拿到第一条就 return 了，根本没走到后面那行坏的，所以不警告——
    # 这是生成器的天然好处：没读到的行不会被解析。
    assert messages.load_meta(key) == {"model": "deepseek-flash"}
    with pytest.warns(UserWarning):
        assert messages.load_summary(key)["text"] == "摘要正文"
    with pytest.warns(UserWarning):
        assert messages.count_messages(key) == 2


def test_missing_file_is_silent(tmp_path):
    """文件不存在是正常状态（新装的项目），不该警告、不该报错。

    负对照：把 iter_records 里的 `if not path.exists(): return` 去掉
    → FileNotFoundError，这条红。
    """
    assert list(iter_records(tmp_path / "根本没有这个文件.jsonl")) == []
"""模板加载的测试。对应第 21 讲。

这里守的规矩只有一条：**提示词的正文在 templates/ 下，不在 .py 里。**
"""
from __future__ import annotations

import pytest

from agent import compaction, prompts
from agent.prompts import TEMPLATES_DIR, read_template


def test_reads_a_nested_template():
    """prompts/ 子目录下的文件也要能读到。"""
    text = read_template("prompts", "compaction.md")
    assert text
    assert text == text.strip()                       # 首尾空白已去掉


def test_missing_template_names_the_file():
    """报错要指名道姓，不能只说"没这个文件"。"""
    with pytest.raises(FileNotFoundError, match="NOT_THERE.md"):
        read_template("prompts", "NOT_THERE.md")


def test_compaction_prompt_comes_from_the_file():
    """改 templates/prompts/compaction.md 即生效——正文必须原样来自文件。"""
    on_disk = (TEMPLATES_DIR / "prompts" / "compaction.md").read_text(encoding="utf-8")
    assert compaction.SUMMARY_INSTRUCTION == on_disk.strip()


def test_compaction_prompt_keeps_the_two_load_bearing_instructions():
    """两句话是花了代价才写进去的，别在调措辞时顺手删掉：

    - "失败过的尝试"：不写，Agent 会把失败过的方案再试一遍（学习笔记 §4.4）
    - "不要调用任何工具"：不写，模型可能在写摘要这一步改去调工具，摘要变成空的
    """
    text = compaction.SUMMARY_INSTRUCTION
    assert "失败过的尝试" in text
    assert "不要调用任何工具" in text


def test_templates_dir_has_one_definition():
    """"模板在哪"这件事只能有一个来源，否则挪目录时会只改一半。"""
    from agent import context

    assert context.TEMPLATES_DIR is prompts.TEMPLATES_DIR
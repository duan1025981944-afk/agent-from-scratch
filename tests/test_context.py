"""系统提示分层拼装的测试。对应第 16 讲。"""
from __future__ import annotations

import pytest

from agent import context
from agent.context import TEMPLATES_DIR, build_system_prompt


def test_layers_are_ordered_stable_first():
    """稳定的在前、变动的在后——缓存命中的前提。"""
    prompt = build_system_prompt("/tmp/ws")

    soul = prompt.index("# Soul")
    agents = prompt.index("# 在这个工作区怎么做事")
    runtime = prompt.index("# 运行时")

    assert soul < agents < runtime


def test_runtime_root_is_injected():
    """工作区根目录由代码注入，不写死在模板里。"""
    assert "/tmp/ws" in build_system_prompt("/tmp/ws")
    assert "/tmp/ws" not in (TEMPLATES_DIR / "SOUL.md").read_text(encoding="utf-8")


def test_prompt_comes_from_files_not_code():
    """改模板即生效：模板内容必须原样出现在系统提示里。"""
    prompt = build_system_prompt("/tmp/ws")
    for name in context.LAYERS:
        body = (TEMPLATES_DIR / name).read_text(encoding="utf-8").strip()
        assert body in prompt


def test_missing_template_says_which_file(monkeypatch):
    """模板缺失时，报错要指名道姓，不能是一句 FileNotFoundError。"""
    monkeypatch.setattr(context, "LAYERS", ["NOT_THERE.md"])

    with pytest.raises(FileNotFoundError, match="NOT_THERE.md"):
        build_system_prompt("/tmp/ws")
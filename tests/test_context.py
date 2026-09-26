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

# ─────────────── 第 33 讲：技能清单进系统提示 ───────────────

清单 = "- **returns-policy** — 星辰科技退换货流程。用户问退货、换货、退款时使用。"


def test_skills_sit_between_runtime_and_memory():
    """★ 技能清单排在运行时之后、记忆之前。

    排序的依据是"内容变得勤不勤"：记忆每次 /dream 都可能变，
    技能清单只在装/删技能时才变，所以技能在前、记忆在后。
    排反了的代价：每跑一次 Dream，后面整段清单的缓存都白丢。

    负对照：把 context.py 里 append 技能那段挪到 append 记忆之后 -> 这条红。
    """
    prompt = build_system_prompt("/tmp/ws", memory="- 用户用 Windows", skills=清单)

    assert prompt.index("# 运行时") < prompt.index("# 技能") < prompt.index("# 长期记忆")


def test_no_skills_means_no_section_at_all():
    """★ 一个技能都没有时，整段不出现 —— 连标题都不要。

    空清单比没有清单更坏：模型会以为"有这套机制但一个都没装"，
    可能去猜名字白调一轮 load_skill。和记忆"没有就整段不加"是同一条规矩。

    负对照：把 `if skills:` 去掉（无条件 append）-> 出现空的 "# 技能" 段，这条红。
    """
    prompt = build_system_prompt("/tmp/ws", memory="- 用户用 Windows")

    assert "# 技能" not in prompt
    assert "load_skill" not in prompt            # 引导语也一起不见了


def test_the_guidance_text_comes_from_the_template():
    """引导语的正文在 templates/SKILLS.md 里，改文件即生效，不写死在代码里。

    负对照：把 context.py 里的 read_template('SKILLS.md') 换成一段写死的文字
            -> 模板原文不再逐字出现，这条红。
    """
    prompt = build_system_prompt("/tmp/ws", skills=清单)

    模板 = (TEMPLATES_DIR / "SKILLS.md").read_text(encoding="utf-8").strip()
    assert 模板 in prompt
    assert 清单 in prompt
    assert prompt.index(模板) < prompt.index(清单)      # 先引导语，后清单
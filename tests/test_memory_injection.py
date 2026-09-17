"""长期记忆注入的测试。对应第 24 讲。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

from pathlib import Path

from agent import memory
from agent.context import build_system_prompt
from agent.memory import read_memory
from agent.prompts import TEMPLATES_DIR

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_missing_memory_file_is_not_an_error(tmp_path):
    """刚装好、还没记过任何东西——这是常态，不能报错。

    负对照：删掉 read_memory 里的 `if not path.exists()` → FileNotFoundError。
    """
    assert read_memory(tmp_path / "MEMORY.md") == ""


def test_blank_memory_adds_nothing_to_prompt(tmp_path):
    """只有空白的记忆文件，系统提示里连标题都不该出现。

    负对照：把 build_system_prompt 里的 `if memory:` 去掉 → 出现空的"# 长期记忆"。
    """
    f = tmp_path / "MEMORY.md"
    f.write_text("\n   \n", encoding="utf-8")

    prompt = build_system_prompt("/ws", read_memory(f))

    assert "# 长期记忆" not in prompt
    assert prompt == build_system_prompt("/ws")  # 和"根本没有记忆"逐字相同


def test_memory_is_injected_after_all_other_layers(tmp_path):
    """记忆正文要进系统提示，而且排在最后（变得最勤）。

    负对照：把记忆那段 append 挪到模板层前面 → 顺序断言红。
    """
    f = tmp_path / "MEMORY.md"
    f.write_text("- 用户的项目代号是 蓝鲸-7\n", encoding="utf-8")

    prompt = build_system_prompt("/ws", read_memory(f))

    assert "- 用户的项目代号是 蓝鲸-7" in prompt
    assert prompt.index("# 运行时") < prompt.index("# 长期记忆")


def test_bom_written_by_powershell_is_removed(tmp_path):
    """PowerShell 5.1 的 Set-Content -Encoding utf8 会带 BOM，读出来不能残留。

    负对照：把 read_memory 的 encoding 改回 "utf-8" → 开头多一个 \\ufeff。
    """
    f = tmp_path / "MEMORY.md"
    f.write_bytes("\ufeff- 用户叫 Himeko\n".encode("utf-8"))

    assert read_memory(f) == "- 用户叫 Himeko"


def test_memory_file_lives_in_gitignored_data_not_templates():
    """MEMORY.md 是 Agent 写的、含真实对话里的事实，不能进 templates/，也不能进 git。

    负对照：把 MEMORY_FILE 改成 TEMPLATES_DIR / "MEMORY.md" → 第一条断言红。
    """
    assert not memory.MEMORY_FILE.is_relative_to(TEMPLATES_DIR)

    data_dir = PROJECT_ROOT / "data"
    assert memory.MEMORY_FILE.is_relative_to(data_dir)

    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in ignored
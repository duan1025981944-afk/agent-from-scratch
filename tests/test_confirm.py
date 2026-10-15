"""动手前确认的测试：会改动东西的工具，执行前先问用户（改造 5）。

确认分在两处：
    runner.py   什么时候问、不同意时怎么办      -> 前五个测试
    main.py     在终端里怎么问、怎么算同意      -> 最后两个测试

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

from agent.runner import DENIED_TEXT, AgentRunner, AgentRunSpec
from agent.tools.base import Tool
from agent.tools.loader import discover_tools
from providers.base import ModelReply, Provider, ToolCall


# ─────────────────────────── 测试替身 ───────────────────────────

class ScriptedProvider(Provider):
    """先要求调一次工具，拿到结果后说"好的"。"""

    def __init__(self, tool_name: str, arguments: dict):
        self.replies = [
            ModelReply(
                content="",
                tool_calls=[ToolCall("call_1", tool_name, arguments, None)],
                raw={"role": "assistant", "content": "", "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }]},
            ),
            ModelReply(content="好的", raw={"role": "assistant", "content": "好的"}),
        ]

    async def chat(self, messages, tools=None):
        return self.replies.pop(0)


class SpyTool(Tool):
    """会改动东西的假工具：记下自己有没有被真正执行。"""

    name = "spy"
    description = "测试用"
    parameters = {"type": "object", "properties": {"x": {"type": "integer"}}}

    def __init__(self):
        self.executed = 0

    async def execute(self, x: int = 0) -> str:
        self.executed += 1
        return "做完了"


class ReadOnlySpy(SpyTool):
    name = "spy"
    read_only = True


class Asker:
    """假的"用户"：按预设回答同意或不同意，并记下被问过什么。"""

    def __init__(self, answer: bool):
        self.answer = answer
        self.asked: list[tuple[str, dict]] = []

    def __call__(self, name: str, arguments: dict) -> bool:
        self.asked.append((name, arguments))
        return self.answer


def run_once(tool: Tool, confirm):
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "（测试）"}],
        provider=ScriptedProvider("spy", {"x": 1}),
        tools={"spy": tool},
        confirm=confirm,
    )
    return asyncio.run(AgentRunner().run(spec))


def tool_messages(result) -> list[dict]:
    return [m for m in result.messages if m.get("role") == "tool"]


# ─────────────────────────── runner：什么时候问 ───────────────────────────

def test_yes_means_the_tool_runs(tmp_path):
    """用户同意 → 照常执行。问的时候要带上工具名和参数，用户才知道在批准什么。

    负对照：把 runner 里那行 if 的最后一段改成 not spec.confirm(call.name, {}) → asked 对不上，这条红。
    """
    tool, asker = SpyTool(), Asker(True)

    result = run_once(tool, asker)

    assert asker.asked == [("spy", {"x": 1})]
    assert tool.executed == 1
    assert tool_messages(result)[0]["content"] == "做完了"


def test_no_means_not_executed_and_the_model_is_told():
    """★ 用户拒绝 → 工具不执行；模型收到"用户拒绝了"，这一轮照常走完。

    拒绝也要回一条 tool 消息（带同一个 tool_call_id），否则下一次请求会被接口拒掉。

    负对照：把拒绝分支里的 messages.append(...) 删掉、只留 continue → 没有 tool 消息，这条红。
    """
    tool = SpyTool()

    result = run_once(tool, Asker(False))

    assert tool.executed == 0
    [msg] = tool_messages(result)
    assert msg["tool_call_id"] == "call_1"
    assert msg["content"] == DENIED_TEXT
    assert result.final_content == "好的"


def test_read_only_tools_are_never_asked():
    """只读的工具不问：读个文件也要按 y，人很快就会看都不看直接按。

    负对照：把 runner 里的 `not tool.read_only and` 去掉 → 只读工具也被问，这条红。
    """
    tool, asker = ReadOnlySpy(), Asker(False)

    run_once(tool, asker)

    assert asker.asked == []
    assert tool.executed == 1


def test_without_confirm_nothing_is_asked():
    """不传 confirm 就不问：测试和以后的自动评估都这么用。

    负对照：把 runner 里的 `spec.confirm and` 去掉 → 调 None 报 TypeError，工具没执行，这条红。
    """
    tool = SpyTool()

    run_once(tool, None)

    assert tool.executed == 1


def test_a_new_tool_needs_confirmation_by_default():
    """★ 默认值是"会改动东西"。忘了声明的工具宁可多问一句，也不能悄悄执行。

    负对照：把 base.py 里的 read_only: bool = False 改成 True → 这条红。
    """
    class Forgot(Tool):
        name = "forgot"
        description = "忘了声明 read_only 的工具"
        parameters = {"type": "object", "properties": {}}

        async def execute(self) -> str:
            return ""

    assert Forgot().read_only is False


def test_every_builtin_tool_declares_what_it_does():
    """每个自带工具"问不问"都对：读的不问，改的要问。

    以后新加工具时这条会提醒你：想清楚它算哪一类，再把它加进这张表。

    负对照：把 read_file.py 里的 read_only = True 删掉 → read_file 变成要问，这条红。
    """
    expected = {
        "echo": True,
        "edit_file": False,
        "list_dir": True,
        "load_skill": True,
        "read_file": True,
        "run_command": False,
        "search_text": True,
        "write_file": False,
    }
    actual = {name: tool.read_only for name, tool in discover_tools().items()}

    assert actual == expected


# ─────────────────────────── main.py：在终端里怎么问 ───────────────────────────

def test_only_y_or_yes_means_yes(monkeypatch):
    """★ 默认拒绝：只有 y / yes 算同意，直接回车、n、乱敲都算不同意。

    负对照：把 ask_permission 的最后一行改成 return answer.strip().lower() != "n" → 回车被当成同意，这条红。
    """
    import main

    def answers(text):
        monkeypatch.setattr("builtins.input", lambda prompt="": text)
        return main.ask_permission("run_command", {"command": "git status"})

    assert answers("y") is True
    assert answers(" YES ") is True
    assert answers("") is False
    assert answers("n") is False
    assert answers("好") is False


def test_ctrl_c_at_the_question_counts_as_no(monkeypatch):
    """在 Windows 控制台里，停在 input() 上按 Ctrl+C 抛的是 EOFError。当成拒绝，不能让程序崩。

    负对照：把 ask_permission 里的 except EOFError 那段去掉 → EOFError 冒出来，这条红。
    """
    import main

    def ctrl_c(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", ctrl_c)

    assert main.ask_permission("run_command", {"command": "git status"}) is False

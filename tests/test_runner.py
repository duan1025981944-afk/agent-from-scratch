"""runner 的防御性行为测试。

对应第 7 讲检查点 4（三次撞坏）与第 8 讲（存档与输入分离）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent.runner import AgentRunner, AgentRunSpec
from agent.tools import workspace
from agent.tools.read_file import ReadFileTool
from providers.base import ModelReply, Provider, ToolCall


# ─────────────────────────── 测试替身与工具函数 ───────────────────────────

class FakeProvider(Provider):
    """不联网的假 provider：按剧本逐条返回，并记下每次收到的 messages。

    received 是关键——它让我们能检查「发出去的」和「存下来的」是不是同一个东西。
    """

    def __init__(self, script: list[ModelReply]):
        self._script = list(script)
        self.received: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelReply:
        self.received.append([dict(m) for m in messages])
        if not self._script:
            return text_reply("（剧本结束）")
        return self._script.pop(0)


def tool_reply(name: str, arguments_json: str, call_id: str = "call_fake_1") -> ModelReply:
    """造一条「模型要调工具」的回复。

    参数传的是 JSON 字符串而不是 dict，为的是原样复刻 openai_compat
    里那段解析逻辑——包括它解析失败时的行为。
    """
    try:
        args, err = json.loads(arguments_json or "{}"), None
    except json.JSONDecodeError as exc:
        args, err = {}, f"参数不是合法 JSON：{exc}"

    raw = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments_json},
        }],
    }
    return ModelReply(content="", tool_calls=[ToolCall(call_id, name, args, err)], raw=raw)


def text_reply(text: str) -> ModelReply:
    """造一条「模型直接给答案」的回复，用来让循环 break。"""
    return ModelReply(content=text, raw={"role": "assistant", "content": text})


def run_agent(script: list[ModelReply]):
    """跑一轮，返回 (result, provider)。"""
    provider = FakeProvider(script)
    spec = AgentRunSpec(
        provider=provider,
        initial_messages=[{"role": "user", "content": "（测试）"}],
        tools={"read_file": ReadFileTool()},
    )
    result = asyncio.run(AgentRunner().run(spec))
    return result, provider


def tool_messages(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") == "tool"]


@pytest.fixture
def sandbox(tmp_path):
    """把工作区指向 pytest 的临时目录，测试之间互不影响，跑完自动清理。"""
    workspace.set_root(str(tmp_path))
    return tmp_path


# ─────────────────────────────── 测试用例 ───────────────────────────────

def test_unknown_tool_does_not_crash(sandbox):
    """模型编了个不存在的工具名：循环不能崩，要把可用工具列表告诉它。"""
    result, _ = run_agent([
        tool_reply("delete_everything", '{"path": "."}'),
        text_reply("done"),
    ])

    assert result.final_content == "done"

    msgs = tool_messages(result.messages)
    assert len(msgs) == 1
    assert "delete_everything" in msgs[0]["content"]
    assert "read_file" in msgs[0]["content"], "要告诉模型有哪些工具可用"


def test_every_tool_call_is_answered_by_a_tool_role_message(sandbox):
    """OpenAI 硬规矩：带 tool_call_id 的消息，角色必须是 tool。

    这是 Bug ② 的疫苗。assistant + tool_call_id 是非法组合，
    会让 API 直接报 400，而且这条烂消息会被写进 jsonl 永久复活。
    """
    result, _ = run_agent([
        tool_reply("delete_everything", "{}"),
        text_reply("done"),
    ])

    bad = [m for m in result.messages
           if "tool_call_id" in m and m.get("role") != "tool"]
    assert not bad, f"这些消息带了 tool_call_id 但角色不是 tool：{bad}"


def test_broken_arguments_are_reported_to_model(sandbox):
    """参数 JSON 少个右括号：不能崩，要把解析失败和行动建议一起告诉模型。"""
    result, _ = run_agent([
        tool_reply("read_file", '{"path": ".gitignore"'),   # 故意少一个 }
        text_reply("done"),
    ])

    msgs = tool_messages(result.messages)
    assert len(msgs) == 1
    assert "解析失败" in msgs[0]["content"]
    assert "不要用同样的参数重试" in msgs[0]["content"], "错误必须附带行动建议"


def test_archive_keeps_full_text_while_model_sees_truncated(sandbox):
    """第 8 讲的核心不变量：存档存全文，截断只发生在发送前。

    这是整个测试文件里最重要的一条——它保护的是一个很容易在
    重构中被悄悄破坏的性质（比如把 dict(msg) 写成直接改原件）。
    """
    (sandbox / "big.txt").write_text("星辰科技" * 50_000, encoding="utf-8")  # 20 万字符

    result, provider = run_agent([
        tool_reply("read_file", '{"path": "big.txt"}'),
        text_reply("done"),
    ])

    archived = tool_messages(result.messages)[0]["content"]
    sent = tool_messages(provider.received[-1])[0]["content"]

    assert len(archived) > 100_000, "存档里必须是全文"
    assert len(sent) < 10_000, "发给模型的必须已被截断"
    assert "已截断" in sent, "截断后要告诉模型丢了内容"
    assert archived != sent
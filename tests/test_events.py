"""事件记录的测试：每一圈做了什么、花了多少（改造 6）。

分三处：
    runner.py           什么时候交出事件、事件里有什么      -> 前四个测试
    session/events.py   怎么记、怎么加总                    -> 中间三个测试
    main.py             /cost 命令                          -> 最后一个测试

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio
import json

from agent.runner import AgentRunner, AgentRunSpec
from agent.tools.base import Tool, ToolResult
from providers.base import ModelReply, Provider, ToolCall, Usage
from session.events import EventLog, format_summary, read_events, summarize


# ─────────────────────────── 测试替身 ───────────────────────────

def wants(*calls: tuple[str, dict], usage: Usage | None = None) -> ModelReply:
    """一条"模型要调这几个工具"的回复。calls 里每项是 (工具名, 参数)。"""
    tool_calls = [ToolCall(f"call_{n}", name, args, None) for n, (name, args) in enumerate(calls)]
    raw = {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
        for c in tool_calls
    ]}
    return ModelReply(content="", tool_calls=tool_calls, raw=raw, usage=usage or Usage())


def says(text: str, usage: Usage | None = None) -> ModelReply:
    """一条"模型直接给答案"的回复。"""
    return ModelReply(content=text, raw={"role": "assistant", "content": text}, usage=usage or Usage())


class ScriptedProvider(Provider):
    def __init__(self, *replies: ModelReply):
        self.replies = list(replies)

    async def chat(self, messages, tools=None):
        return self.replies.pop(0)


class Fine(Tool):
    name = "fine"
    description = "总是成功"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    async def execute(self) -> str:
        return "好了"


class Broken(Tool):
    name = "broken"
    description = "总是报告失败"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    async def execute(self) -> str:
        return ToolResult.error("坏了")


class Writer(Tool):
    name = "writer"
    description = "会改动东西"
    parameters = {"type": "object", "properties": {}}

    async def execute(self) -> str:
        return "写好了"


def run_with_events(*replies: ModelReply, confirm=None) -> list[dict]:
    """跑一轮，把 runner 交出来的事件收进一个列表返回。"""
    got: list[dict] = []
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "（测试）"}],
        provider=ScriptedProvider(*replies),
        tools={"fine": Fine(), "broken": Broken(), "writer": Writer()},
        confirm=confirm,
        on_event=got.append,
    )
    asyncio.run(AgentRunner().run(spec))
    return got


# ─────────────────────────── runner：交出什么 ───────────────────────────

def test_every_model_call_reports_its_usage():
    """★ 每调一次模型交一条 model 事件：第几圈、用了几秒、输入输出多少 token。

    负对照：把调模型之后的 _emit(spec, {...}) 删掉 → 一条 model 事件都没有，这条红。
    """
    events = run_with_events(
        wants(("fine", {}), usage=Usage(prompt_tokens=1000, completion_tokens=20, cache_hit_tokens=900)),
        says("完成", usage=Usage(prompt_tokens=1100, completion_tokens=30, cache_hit_tokens=1000)),
    )

    models = [e for e in events if e["type"] == "model"]
    assert [m["round"] for m in models] == [1, 2]
    assert [m["prompt"] for m in models] == [1000, 1100]
    assert [m["cache_hit"] for m in models] == [900, 1000]
    assert [m["output"] for m in models] == [20, 30]
    assert [m["tool_calls"] for m in models] == [1, 0]
    assert all(m["seconds"] >= 0 for m in models)


def test_each_tool_call_reports_how_it_went():
    """★ 每个工具调用交一条 tool 事件，status 分得清：成功、出错、工具名不存在、参数坏了。

    负对照：把执行完那条事件的 "error" if failed else "ok" 写成 "ok" → broken 也成了 ok，这条红。
    """
    bad_args = wants(("fine", {}))
    bad_args.tool_calls.append(ToolCall("call_x", "fine", {}, "参数不是合法 JSON"))
    bad_args.raw["tool_calls"].append(
        {"id": "call_x", "type": "function", "function": {"name": "fine", "arguments": "{"}})

    events = run_with_events(
        wants(("fine", {}), ("broken", {}), ("nope", {})),
        bad_args,
        says("完成"),
    )

    tools = [(e["round"], e["name"], e["status"]) for e in events if e["type"] == "tool"]
    assert tools == [
        (1, "fine", "ok"),
        (1, "broken", "error"),
        (1, "nope", "unknown"),
        (2, "fine", "ok"),
        (2, "fine", "bad_args"),
    ]


def test_a_denied_call_is_recorded_as_denied():
    """用户拒绝的调用也要记：它没执行，但这一圈确实发生过。

    负对照：把拒绝分支里的 _emit(...) 删掉 → 少一条事件，这条红。
    """
    events = run_with_events(wants(("writer", {})), says("好吧"), confirm=lambda name, args: False)

    [tool] = [e for e in events if e["type"] == "tool"]
    assert (tool["name"], tool["status"]) == ("writer", "denied")


def test_without_on_event_nothing_breaks():
    """不传 on_event 就不交：测试和别的地方用 runner 时，不必关心事件。

    负对照：把 _emit 里的 `if spec.on_event:` 去掉 → 调 None 报 TypeError，这条红。
    """
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "（测试）"}],
        provider=ScriptedProvider(wants(("fine", {})), says("完成")),
        tools={"fine": Fine()},
    )

    result = asyncio.run(AgentRunner().run(spec))

    assert result.final_content == "完成"


# ─────────────────────────── session/events.py：记和算 ───────────────────────────

def test_events_are_appended_with_time_and_turn(tmp_path):
    """★ 每条事件补上时间和第几轮；只往后加，旧的一行都不动。

    负对照：把 EventLog.__call__ 里拼 record 的 "turn": self.turn 删掉 → 这条红。
    """
    log = EventLog("s1", tmp_path)
    log.start_turn()
    log({"type": "model", "prompt": 100})
    log.start_turn()
    log({"type": "tool", "name": "fine", "status": "ok"})

    events = read_events("s1", tmp_path)

    assert [(e["turn"], e["type"]) for e in events] == [(1, "turn"), (1, "model"), (2, "turn"), (2, "tool")]
    assert all(e["at"] for e in events)


def test_a_resumed_session_keeps_counting_turns(tmp_path):
    """恢复会话时，轮数接着往下数，不从 1 重来。

    负对照：把 __init__ 里的 self.turn = _last_turn(self.path) 改成 self.turn = 0 → 第三轮又成了 1，这条红。
    """
    first = EventLog("s1", tmp_path)
    first.start_turn()
    first.start_turn()

    again = EventLog("s1", tmp_path)               # 关掉程序、--session 恢复：新造一个
    again.start_turn()

    assert [e["turn"] for e in read_events("s1", tmp_path)] == [1, 2, 3]


def test_summary_adds_everything_up():
    """把事件加总：几轮、几圈、多少 token、哪些工具各几次、结果各几次。

    负对照：把 summarize 里 model 那段的 s["cache_hit"] += ... 删掉 → 命中数是 0，这条红。
    """
    events = [
        {"type": "turn"},
        {"type": "model", "seconds": 2.0, "prompt": 1000, "cache_hit": 900, "output": 50},
        {"type": "tool", "name": "run_command", "status": "denied", "seconds": 0},
        {"type": "model", "seconds": 1.5, "prompt": 1200, "cache_hit": 1100, "output": 80},
        {"type": "turn"},
        {"type": "model", "seconds": 1.0, "prompt": 1300, "cache_hit": 1200, "output": 40},
        {"type": "tool", "name": "read_file", "status": "ok", "seconds": 0.2},
        {"type": "tool", "name": "read_file", "status": "ok", "seconds": 0.3},
    ]

    s = summarize(events)

    assert (s["turns"], s["model_calls"], s["tool_calls"]) == (2, 3, 3)
    assert (s["prompt"], s["cache_hit"], s["output"]) == (3500, 3200, 170)
    assert s["tools"] == {"run_command": 1, "read_file": 2}
    assert s["status"] == {"denied": 1, "ok": 2}
    assert "91%" in format_summary(s, "s1")           # 3200 / 3500


# ─────────────────────────── main.py：/cost ───────────────────────────

def test_cost_command_reads_this_sessions_events(tmp_path, monkeypatch, capsys):
    """/cost 读这个会话的事件文件，打印加总结果。不调模型（provider 传的是 None）。

    顺带守住第 13 讲那个坑：EventLog 不传目录时，要用**换掉之后的** EVENTS_DIR。

    负对照 1：把 main.py 里 /cost 那段删掉 → 打印的是"不认识的命令"，这条红。
    负对照 2：把 EventLog 的签名写成 events_dir: Path = EVENTS_DIR → 事件写进了真的
             data/events/，临时目录里没有文件，这条红（记得事后删掉真目录里的 s1.jsonl）。
    """
    import main
    import session.events

    monkeypatch.setattr(session.events, "EVENTS_DIR", tmp_path)
    log = EventLog("s1")                               # 不传目录：用的是换掉之后的 EVENTS_DIR
    log.start_turn()
    log({"type": "model", "seconds": 1.0, "prompt": 2000, "cache_hit": 1500, "output": 60})

    assert (tmp_path / "s1.jsonl").exists()           # 写进了临时目录，没碰真的 data/

    asyncio.run(main.run_local_command("/cost", None, "s1"))
    out = capsys.readouterr().out

    assert "1 轮对话" in out
    assert "2,000 token" in out
    assert "75%" in out

"""事件记录：Agent 每一圈做了什么、花了多少（改造 6）。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
runner 每调一次模型、每处理一个工具调用，就交出一条**事件**（一个字典）。
这里管两件事：

    记    EventLog：把事件追加进 data/events/{会话 key}.jsonl，每条补上时间和第几轮
    算    summarize / format_summary：把一个会话的事件加总，/cost 命令用

文件里的样子（前两个字段是 EventLog 补的，后面是 runner 交出来的）：

    {"at": "2026-10-16 10:12:03", "turn": 1, "type": "turn"}
    {"at": "...", "turn": 1, "type": "model", "round": 1, "seconds": 2.31,
     "estimated": 783, "prompt": 3784, "cache_hit": 128, "output": 96, "tool_calls": 1}
    {"at": "...", "turn": 1, "type": "tool", "round": 1, "name": "run_command",
     "status": "ok", "seconds": 0.42, "chars": 512}

    turn   第几轮对话（你说一句话 = 一轮）。main.py 每轮开头记一条 {"type": "turn"}
    model  调了一次模型：第几圈、用了几秒、输入多少（其中命中缓存多少）、输出多少
    tool   处理了一个工具调用：哪个工具、结果怎样、用了几秒、结果多长

工具的 status 有五种：

    ok         执行了，正常返回
    error      执行了，但工具报告失败（ToolResult.error，或者抛了异常被兜底）
    denied     用户拒绝了，没执行（改造 5 的确认）
    unknown    模型编了一个不存在的工具名
    bad_args   参数那段 JSON 是坏的

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不是**存档。存档存对话内容，用来恢复会话；事件存"花了多少"，只用来看和算
- runner **不知道**它存在。runner 只管把事件交给订单里的 on_event 函数，
  交给谁、存不存、怎么存，是 main.py 的事——和改造 5 的 confirm 同一个思路
- 存档会回滚，事件**不会**。一轮失败时 main.py 把对话退回 boundary，
  但这一轮里已经调过的模型、花掉的 token 是真花了，事件照实留着

──────────────────────────────────────────────────────────────
为什么单独放 data/events/，不放 data/sessions/
──────────────────────────────────────────────────────────────
session/messages.py 的 list_keys() 把 data/sessions/ 下每个 .jsonl 都当成一个会话。
事件文件放进去，--list 就会多出一堆"会话"。

──────────────────────────────────────────────────────────────
和 nanobot 的差别
──────────────────────────────────────────────────────────────
nanobot 用的是一个钩子类 AgentHook（agent/hook.py），十几个方法：before_iteration、
before_execute_tool、after_execute_tool、after_iteration……循环跑到哪一步就调哪个。
我们只有一个函数、收一个字典。先写死再抽象：等哪天需要在某一步**改变**循环的行为
（而不只是记下来），再考虑钩子类。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from storage.jsonl import append_record, iter_records

# 和 session/messages.py 的 SESSIONS_DIR 同一个基准：锚代码位置，data/ 不进 git
EVENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "events"


class EventLog:
    """把 runner 交出来的事件追加进一个会话的事件文件。

    谁会产生它
        main.py 的 chat()，会话打开之后造一个：events = EventLog(session.key)

    谁会用它
        - runner：它被当作 on_event 传进订单，runner 每有一件事就调 events(事件)
        - main.py：每轮开头调 events.start_turn()

    为什么它是一个"能被当成函数调用"的对象
        runner 要的是一个函数（收一个字典）。可记事件还得知道"现在是第几轮"，
        这个数要跨好几次调用记住。写一个带 __call__ 的类，它既能被调用，
        又能把 turn 存在自己身上。

    它**不会**抛异常
        事件是观察数据。磁盘满了、文件被占用，不该让一轮正在进行的对话失败，
        所以写失败时只提醒一次，之后安静地跳过。

    例子（碰文件系统，见 tests/test_events.py）
        events = EventLog("20261016-101200")
        events.start_turn()                          # 写一条 {"type": "turn"}
        events({"type": "tool", "name": "read_file", "status": "ok"})
    """

    def __init__(self, session_key: str, events_dir: Path | None = None) -> None:
        # 默认值写 None、在这里才取 EVENTS_DIR：测试换掉 EVENTS_DIR 才换得动（第 13 讲那个坑）
        self.path = (events_dir or EVENTS_DIR) / f"{session_key}.jsonl"
        self.turn = _last_turn(self.path)            # 恢复会话时接着往下数，不从 0 重来
        self._warned = False

    def start_turn(self) -> None:
        """新的一轮开始了：轮数加一，并记一条 {"type": "turn"}。"""
        self.turn += 1
        self({"type": "turn"})

    def __call__(self, event: dict[str, Any]) -> None:
        """记一条事件：补上时间和轮数，追加到文件末尾。"""
        record = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "turn": self.turn, **event}
        try:
            append_record(self.path, record)
        except OSError as exc:
            if not self._warned:
                self._warned = True
                print(f"  [事件记录写不进去，这次会话不再记录：{exc}]")


def _last_turn(path: Path) -> int:
    """文件里已经记到第几轮了。没有文件就是 0。"""
    return max((r.get("turn", 0) for r in iter_records(path)), default=0)


def read_events(session_key: str, events_dir: Path | None = None) -> list[dict[str, Any]]:
    """读出一个会话的全部事件，按记录的先后。没有就是空列表。"""
    return list(iter_records((events_dir or EVENTS_DIR) / f"{session_key}.jsonl"))


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """把一堆事件加总成一张表。

    返回什么
        一个字典：

            turns          几轮对话
            model_calls    调了几次模型（= 一共转了几圈）
            model_seconds  调模型一共用了几秒
            prompt         输入 token 合计
            cache_hit      其中命中缓存的
            output         输出 token 合计
            tool_calls     处理了几个工具调用
            tool_seconds   执行工具一共用了几秒
            tools          {工具名: 次数}
            status         {"ok": 次数, "error": 次数, "denied": 次数, ...}

    例子
        >>> s = summarize([
        ...     {"type": "turn"},
        ...     {"type": "model", "seconds": 2.0, "prompt": 1000, "cache_hit": 900, "output": 50},
        ...     {"type": "tool", "name": "read_file", "status": "ok", "seconds": 0.1},
        ...     {"type": "model", "seconds": 1.0, "prompt": 1200, "cache_hit": 1100, "output": 80},
        ... ])
        >>> s["turns"], s["model_calls"], s["prompt"], s["cache_hit"], s["output"]
        (1, 2, 2200, 2000, 130)
        >>> s["tools"], s["status"]
        ({'read_file': 1}, {'ok': 1})
    """
    s: dict[str, Any] = {
        "turns": 0, "model_calls": 0, "model_seconds": 0.0,
        "prompt": 0, "cache_hit": 0, "output": 0,
        "tool_calls": 0, "tool_seconds": 0.0, "tools": {}, "status": {},
    }
    for e in events:
        kind = e.get("type")
        if kind == "turn":
            s["turns"] += 1
        elif kind == "model":
            s["model_calls"] += 1
            s["model_seconds"] += e.get("seconds", 0)
            s["prompt"] += e.get("prompt", 0)
            s["cache_hit"] += e.get("cache_hit", 0)
            s["output"] += e.get("output", 0)
        elif kind == "tool":
            s["tool_calls"] += 1
            s["tool_seconds"] += e.get("seconds", 0)
            name, status = e.get("name", "?"), e.get("status", "?")
            s["tools"][name] = s["tools"].get(name, 0) + 1
            s["status"][status] = s["status"].get(status, 0) + 1
    return s


# 状态的中文名，format_summary 用
_STATUS_NAMES = {"ok": "成功", "error": "出错", "denied": "被拒", "unknown": "工具名不存在", "bad_args": "参数坏了"}


def format_summary(s: dict[str, Any], session_key: str) -> str:
    """把 summarize 的结果写成几行人看的话。

    例子
        >>> print(format_summary(summarize([]), "20261016-101200"))
          会话 20261016-101200：还没有任何记录。
    """
    if not s["turns"] and not s["model_calls"]:
        return f"  会话 {session_key}：还没有任何记录。"

    hit_rate = s["cache_hit"] / s["prompt"] if s["prompt"] else 0.0
    lines = [
        f"  会话 {session_key}：{s['turns']} 轮对话，模型一共转了 {s['model_calls']} 圈",
        f"    调模型    用时 {s['model_seconds']:.1f} 秒",
        f"    输入      {s['prompt']:,} token，其中命中缓存 {s['cache_hit']:,}（{hit_rate:.0%}）",
        f"    输出      {s['output']:,} token",
    ]
    if s["tool_calls"]:
        status = "、".join(f"{_STATUS_NAMES.get(k, k)} {v}" for k, v in s["status"].items())
        tools = "、".join(f"{name} ×{n}" for name, n in s["tools"].items())
        lines.append(f"    工具      {s['tool_calls']} 次（{status}），用时 {s['tool_seconds']:.1f} 秒")
        lines.append(f"              {tools}")
    return "\n".join(lines)

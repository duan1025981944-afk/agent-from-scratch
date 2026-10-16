"""评估考场本身的测试（改造 7）。不调真模型：用按剧本回答的假模型。

真正的五道题是不是"做不出就判不过、照答案做就判过"，由 python -m evals.run --self-check 检查，
它要反复跑全部测试，太慢，不放在这里。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from agent.tools import workspace
from evals.harness import REPO_ROOT, eval_policy, run_task
from evals.run import report
from evals.tasks import PYTHON, Task, no_setup
from providers.base import ModelReply, Provider, ToolCall


# ─────────────────────────── 测试替身 ───────────────────────────

class Script(Provider):
    """按剧本回答的假模型：每次返回剧本里的下一条。"""

    def __init__(self, *replies: ModelReply, delay: float = 0.0):
        self.replies, self.delay = list(replies), delay

    async def chat(self, messages, tools=None):
        await asyncio.sleep(self.delay)
        return self.replies.pop(0)


def call(name: str, args: dict) -> ModelReply:
    return ModelReply(content="", tool_calls=[ToolCall("c1", name, args, None)], raw={
        "role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})


def say(text: str) -> ModelReply:
    return ModelReply(content=text, raw={"role": "assistant", "content": text})


def answer_file_task() -> Task:
    """一道假题：在工作区里写一个 answer.txt，内容是 42。"""

    def check(repo: Path, answer: str) -> tuple[bool, str]:
        f = repo / "answer.txt"
        ok = f.exists() and f.read_text(encoding="utf-8") == "42"
        return ok, "写对了" if ok else "没有写出 answer.txt"

    return Task("fake", "测试", "写一个 answer.txt", no_setup, check, lambda repo: "")


def leftovers() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("nb-eval-*")}


# ─────────────────────────── 没人按 y：规则怎么答 ───────────────────────────

def test_policy_allows_only_pytest_and_read_only_git():
    """★ 评估时没人确认，run_command 只放行两种：指定的 Python 跑 pytest、只读的 git。

    负对照：把 eval_policy 最后一行改成 return True → 什么命令都放行，这条红。
    """
    allowed = [
        f'"{PYTHON}" -m pytest -q',
        f'"{PYTHON}" -m pytest tests/test_history.py -q',
        "git status", "git diff agent/memory.py",
    ]
    denied = [
        "dir",                                    # 不在白名单里
        "python -m pytest -q",                    # 不是指定的那个 Python
        f'"{PYTHON}" -c "import os"',             # 指定的 Python，但不是跑 pytest
        "git commit -am x",                       # git，但会改东西
    ]
    assert all(eval_policy("run_command", {"command": c}) for c in allowed)
    assert not any(eval_policy("run_command", {"command": c}) for c in denied)


def test_policy_refuses_chained_or_redirected_commands():
    """★ 开头合法也不行：后面接着跑别的命令、或者改写去向，一律拒绝。

    负对照：把 eval_policy 里检查 FORBIDDEN_CHARS 那段删掉 → 第一条会被放行，这条红。
    """
    for tail in (" && del a.py", " | more", " > out.txt", " %COMSPEC%", " ; dir"):
        assert not eval_policy("run_command", {"command": f'"{PYTHON}" -m pytest -q{tail}'})


def test_policy_lets_file_edits_through():
    """改文件一律放行：文件类工具有围栏，出不了副本。"""
    assert eval_policy("write_file", {"path": "a.py", "content": ""})
    assert eval_policy("edit_file", {"path": "a.py", "old_text": "x", "new_text": "y"})


# ─────────────────────────── 一道题从头到尾 ───────────────────────────

def test_a_solved_task_passes_in_a_throwaway_copy(monkeypatch):
    """★ 做对了就判过；文件只写进副本，真仓库一点不动；成本记下来；做完副本删掉、工作区改回原样。

    负对照：把 run_task 里的 on_event=events.append 改成 on_event=None → cost 里没有圈数，这条红。
    """
    monkeypatch.setattr(workspace, "_root", Path("D:/某个原来的工作区"))
    before = leftovers()

    r = asyncio.run(run_task(answer_file_task(), Script(
        call("write_file", {"path": "answer.txt", "content": "42"}),
        say("写好了"),
    )))

    assert r.passed, r.detail
    assert r.stop_reason == "completed"
    assert r.cost["model_calls"] == 2
    assert r.cost["status"] == {"ok": 1}
    assert not (REPO_ROOT / "answer.txt").exists()               # 真仓库没被写
    assert leftovers() == before                                 # 临时文件夹删干净了
    assert workspace._root == Path("D:/某个原来的工作区")          # 工作区改回去了


def test_a_refused_command_is_recorded_and_the_task_fails():
    """规则拒绝的命令不会执行，事件里记成 denied；题没做出来就判没过。

    负对照：把 run_task 里的 confirm=eval_policy 改成 confirm=None → 命令被执行、记成 ok，这条红。
    """
    r = asyncio.run(run_task(answer_file_task(), Script(
        call("run_command", {"command": "echo 42 > answer.txt"}),
        say("我试过了"),
    )))

    assert not r.passed
    assert r.cost["status"] == {"denied": 1}


def test_a_task_that_runs_too_long_is_stopped():
    """一道题超时了就停下来，记成 timeout，不让整场评估卡死。

    负对照：把 run_task 里的 asyncio.wait_for(..., timeout) 去掉、直接 await → 要等满 5 秒，
    而且 stop_reason 成了 completed，这条红。
    """
    r = asyncio.run(run_task(answer_file_task(), Script(say("想很久"), delay=5), timeout=0.5))

    assert r.stop_reason == "timeout"
    assert not r.passed


def test_report_counts_the_success_rate():
    """成绩单的成功率：没有题目时也不能除以零。"""
    assert report([]).startswith("成功 0/0（0%）")

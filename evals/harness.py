"""评估的考场：给每道题复制一份仓库，让 Agent 在里面做题，做完判卷、算成本、收拾干净（改造 7）。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一道题从头到尾这样走（run_task）：

    ① 复制    git clone 一份仓库到临时文件夹（"副本"），在副本里埋题，提交成一个起点
    ② 做题    把工作区设成副本，造一张订单交给 runner：
                 confirm  = eval_policy        没人按 y，改由一条规则来回答
                 on_event = events.append      事件收进列表，用来算成本（改造 6）
    ③ 判卷    task.check(副本, 最后的回答)
    ④ 算账    session/events.py 的 summarize 把事件加总
    ⑤ 收拾    删掉临时文件夹，工作区改回原来的

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**定义题目——那是 evals/tasks.py
- 它**不**打印报告——那是 evals/run.py
- 它**不是**沙箱。见下面

──────────────────────────────────────────────────────────────
没人按 y，怎么保证安全
──────────────────────────────────────────────────────────────
评估时 Agent 一口气做完一道题，中间没人确认。挡在前面的是两样东西：

    副本         它改的文件都在临时文件夹里。文件类工具有围栏，出不了副本
    eval_policy  改文件一律放行；run_command 只放行两种命令：
                 用指定的 Python 跑 pytest，以及只读的 git（status / diff / log / show）。
                 命令里有 & | < > ^ ; % 这些字符的一律拒绝——不许接着跑别的命令、不许改写去向

它**防的是误伤，不防恶意**：pytest 会执行工作区里的代码，Agent 要是存心在测试里写
删文件的语句，这两样都拦不住。我们评估的是自己的 Agent、做的是自己出的题，这个风险可以接受。

──────────────────────────────────────────────────────────────
为什么用 git clone，不用复制文件夹
──────────────────────────────────────────────────────────────
副本里要有 git：判卷时要用 git status 看 Agent 有没有偷改测试。
clone 只带**已经提交**的代码——评估的就是提交了的版本，没提交的改动不算。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.context import build_system_prompt
from agent.runner import AgentRunner, AgentRunSpec
from agent.skills import skill_catalog
from agent.tools import workspace
from agent.tools.loader import discover_tools
from evals.tasks import PYTHON, Task
from providers.base import Provider
from session.events import summarize

REPO_ROOT = Path(__file__).resolve().parent.parent

# run_command 放行的两种命令：指定的 Python 跑 pytest；只读的 git。
ALLOWED_PREFIXES = (
    f'"{PYTHON}" -m pytest',
    f"{PYTHON} -m pytest",
    "git status", "git diff", "git log", "git show",
)
# 出现这些字符就拒绝：& | 接着跑别的命令，< > 改写去向，^ 是 cmd 的转义符，% 会展开环境变量
FORBIDDEN_CHARS = "&|<>^;%\n"


def eval_policy(name: str, arguments: dict) -> bool:
    """评估时代替你回答"允许执行吗"。

    改文件一律放行（文件类工具出不了副本）；run_command 只放行白名单里的命令。

    例子
        >>> eval_policy("edit_file", {"path": "a.py"})
        True
        >>> eval_policy("run_command", {"command": "git status"})
        True
        >>> eval_policy("run_command", {"command": "git status && del a.py"})
        False
        >>> eval_policy("run_command", {"command": "dir"})
        False
    """
    if name != "run_command":
        return True
    command = str(arguments.get("command", "")).strip()
    if any(ch in command for ch in FORBIDDEN_CHARS):
        return False
    return command.startswith(ALLOWED_PREFIXES)


@dataclass
class TaskResult:
    """一道题的成绩单。"""

    task: str
    kind: str
    passed: bool
    detail: str                        # 判卷的那句说明
    stop_reason: str                   # completed / max_iterations / timeout / error: ……
    wall_seconds: float                # 从开始做题到做完，墙上的钟走了几秒（不含判卷）
    cost: dict[str, Any] = field(default_factory=dict)     # summarize(事件) 的结果
    answer: str = ""                   # Agent 最后说的话


def clone_repo(dest: Path) -> None:
    """把仓库（已提交的部分）复制一份到 dest。"""
    subprocess.run(["git", "clone", "--quiet", "--local", "--no-hardlinks", str(REPO_ROOT), str(dest)],
                   check=True, capture_output=True)


def commit_baseline(repo: Path) -> None:
    """把埋好题的样子提交成起点，之后 git status 看到的就只有 Agent 的改动。"""
    subprocess.run(["git", "-c", "user.name=eval", "-c", "user.email=eval@localhost",
                    "commit", "-qam", "eval baseline", "--allow-empty"],
                   cwd=repo, check=True, capture_output=True)


def remove_tree(path: Path) -> None:
    """删掉临时文件夹。git 的对象文件是只读的，Windows 上要先去掉只读才删得掉。"""

    def force(func, target, exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=force)


async def run_task(task: Task, provider: Provider, max_iterations: int = 30,
                   timeout: float = 600) -> TaskResult:
    """在一份新副本里做一道题，返回成绩单。副本做完就删。"""
    work = Path(tempfile.mkdtemp(prefix="nb-eval-"))
    repo = work / "repo"
    previous_root = workspace._root               # 做完要改回去：工作区是全局的
    events: list[dict] = []
    try:
        # ① 复制、埋题、提交起点
        clone_repo(repo)
        task.setup(repo)
        commit_baseline(repo)

        # ② 做题
        workspace.set_root(str(repo))
        spec = AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": build_system_prompt(str(repo), "", skill_catalog())},
                {"role": "user", "content": task.prompt},
            ],
            provider=provider,
            tools=discover_tools(),
            max_iterations=max_iterations,
            confirm=eval_policy,           # 没人按 y：由规则来回答
            on_event=events.append,        # 事件收进列表，做完加总
        )
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(AgentRunner().run(spec), timeout)
            stop_reason, answer = result.stop_reason, result.final_content or ""
        except TimeoutError:
            stop_reason, answer = "timeout", ""
        except Exception as exc:          # noqa: BLE001 —— 一道题崩了，不该拖垮整场评估
            stop_reason, answer = f"error: {type(exc).__name__}: {exc}", ""
        wall = time.perf_counter() - started

        # ③ 判卷（工作区先改回去：判卷用的是子进程，跟它无关，但别让全局状态多停留）
        workspace._root = previous_root
        passed, detail = task.check(repo, answer)

        # ④ 算账
        return TaskResult(task.id, task.kind, passed, detail, stop_reason, round(wall, 1),
                          summarize(events), answer)
    finally:
        # ⑤ 收拾
        workspace._root = previous_root
        remove_tree(work)

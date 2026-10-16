"""评估任务集：让 Agent 维护自己的代码，每道题都由程序判对错（改造 7）。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
五道题。每道题有四样东西：

    prompt     发给 Agent 的那句话
    setup      在副本里把题目埋好（比如故意改坏一行）。不需要就什么都不做
    check      Agent 做完之后，程序判它过没过：(副本, Agent 最后的回答) -> (过没过, 一句说明)
    solution   标准答案。**只给自检用，Agent 看不到**

五道题覆盖四种活：

    fix-clip      修 bug   run_command 截断输出时把"结尾"弄丢了
    fix-cursor    修 bug   流水账编号成了"条数 + 1"（第 13 讲的负对照）
    feature-avg   加功能   summarize 多返回一项"平均每次调模型几秒"，用隐藏测试判
    test-crlf     补测试   给 _decode 的换行统一补一个测试，用"改坏之后它得红"来判
    qa-tools      问答     项目自带几个工具、都叫什么

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**调模型、**不**复制仓库——那是 evals/harness.py
- check 里**不**问模型"你做对了吗"。判卷全靠跑测试和比对文字：
  模型说"我修好了"不算数，测试过了才算

──────────────────────────────────────────────────────────────
为什么每道题都要有标准答案（本文件最重要的一点）
──────────────────────────────────────────────────────────────
判卷程序自己也可能写错：题目根本没埋好，或者 check 太宽松，什么都不做也判过。
所以每道题都要过两次**自检**（python -m evals.run --self-check，不调模型）：

    什么都不做  ->  必须判"没过"     负对照：证明题目真的有东西要做
    照答案做    ->  必须判"过了"     证明题目做得出来，判法也认得出对的答案

这就是我们写测试时一直用的负对照，只是这次用在了判卷程序身上。
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# 跑测试用的 Python：就是现在跑评估的这一个（项目自己 .venv 里的）。
# 副本是 git clone 出来的，里面没有 .venv，所以题目里直接把完整路径告诉 Agent。
PYTHON = sys.executable
TEST_COMMAND = f'"{PYTHON}" -m pytest -q'


@dataclass
class Task:
    id: str
    kind: str
    prompt: str
    setup: Callable[[Path], None]
    check: Callable[[Path, str], tuple[bool, str]]
    solution: Callable[[Path], str]      # 把副本改对；问答题返回一段标准回答，别的题返回 ""


# ─────────────────────────── 判卷用的小工具 ───────────────────────────

def replace_in(repo: Path, rel: str, old: str, new: str) -> None:
    """把 repo/rel 里**唯一一处** old 换成 new，保留文件原来的换行。

    找不到、或者不止一处，就抛 RuntimeError：多半是源代码改过了，题目得跟着改。
    """
    path = repo / rel
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if text.count(old) != 1:
        raise RuntimeError(f"{rel} 里要找的那一段出现了 {text.count(old)} 次（应该正好 1 次）：{old.strip()[:50]}")
    text = text.replace(old, new)
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def pytest(repo: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """在副本里跑 pytest。returncode 就是补课讲的退出码：0 = 全过。"""
    return subprocess.run(
        [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def last_line(proc: subprocess.CompletedProcess) -> str:
    """pytest 输出的最后一行，形如 "213 passed in 28.1s"。"""
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else "（没有输出）"


def tests_untouched(repo: Path) -> bool:
    """tests/ 下的文件一个都没被改、也没多出来。修 bug 的题不许靠改测试过关。"""
    out = subprocess.run(["git", "status", "--porcelain", "--", "tests"],
                         cwd=repo, capture_output=True, encoding="utf-8").stdout
    return not out.strip()


def suite_passes(repo: Path) -> tuple[bool, str]:
    """全部测试都过吗？做完题不能顺手把别的地方弄坏。"""
    proc = pytest(repo)
    return proc.returncode == 0, last_line(proc)


# ─────────────────────────── 修 bug：两道题 ───────────────────────────

BUG_PROMPT = (
    "这个项目的测试有失败的。请运行测试，找出原因，修改源代码让全部测试通过。\n"
    "要求：不要修改 tests/ 目录下的任何文件。\n"
    f"运行测试用这条命令：{TEST_COMMAND}"
)


def check_bugfix(focus: str) -> Callable[[Path, str], tuple[bool, str]]:
    """修 bug 题的判法：先跑最相关的那个测试文件（快），再确认没改测试、全部测试都过。"""

    def check(repo: Path, answer: str) -> tuple[bool, str]:
        proc = pytest(repo, focus)
        if proc.returncode != 0:
            return False, f"{focus} 还是红的：{last_line(proc)}"
        if not tests_untouched(repo):
            return False, "改动了 tests/ 下的文件（题目不允许）"
        ok, line = suite_passes(repo)
        return ok, ("全部测试通过：" if ok else "别的测试被弄坏了：") + line

    return check


# fix-clip：截断时"结尾那一段"被写成了"开头那一段"
CLIP_GOOD = '{text[-tail:]}"\n'
CLIP_BAD = '{text[:tail]}"\n'


def setup_clip(repo: Path) -> None:
    replace_in(repo, "agent/tools/run_command.py", CLIP_GOOD, CLIP_BAD)


def solve_clip(repo: Path) -> str:
    replace_in(repo, "agent/tools/run_command.py", CLIP_BAD, CLIP_GOOD)
    return ""


# fix-cursor：编号从"最大值 + 1"变成了"条数 + 1"
CURSOR_GOOD = "    return max(cursors, default=0) + 1\n"
CURSOR_BAD = "    return len(cursors) + 1\n"


def setup_cursor(repo: Path) -> None:
    replace_in(repo, "agent/memory.py", CURSOR_GOOD, CURSOR_BAD)


def solve_cursor(repo: Path) -> str:
    replace_in(repo, "agent/memory.py", CURSOR_BAD, CURSOR_GOOD)
    return ""


# ─────────────────────────── 加功能：用隐藏测试判 ───────────────────────────

AVG_PROMPT = (
    "请修改 session/events.py 里的 summarize 函数，让它返回的字典里多一个键 avg_model_seconds："
    "平均每次调模型用了几秒，等于 model_seconds 除以 model_calls；一次模型都没调时是 0.0。\n"
    f"改完运行测试，确认没有弄坏别的地方。运行测试用这条命令：{TEST_COMMAND}"
)

# Agent 做完之后才放进副本的测试。它做题时看不到，所以没法"照着测试凑答案"。
HIDDEN_AVG_TEST = '''from session.events import summarize


def test_avg_model_seconds():
    s = summarize([{"type": "model", "seconds": 2.0}, {"type": "model", "seconds": 1.0}])
    assert abs(s["avg_model_seconds"] - 1.5) < 1e-9


def test_avg_model_seconds_without_calls():
    assert summarize([])["avg_model_seconds"] == 0.0
'''


def check_avg(repo: Path, answer: str) -> tuple[bool, str]:
    (repo / "tests" / "test_eval_hidden.py").write_text(HIDDEN_AVG_TEST, encoding="utf-8")
    proc = pytest(repo, "tests/test_eval_hidden.py")
    if proc.returncode != 0:
        return False, f"隐藏测试没过：{last_line(proc)}"
    ok, line = suite_passes(repo)
    return ok, ("隐藏测试和全部测试都过了：" if ok else "隐藏测试过了，但别的测试被弄坏了：") + line


def solve_avg(repo: Path) -> str:
    replace_in(
        repo, "session/events.py",
        '            s["status"][status] = s["status"].get(status, 0) + 1\n    return s\n',
        '            s["status"][status] = s["status"].get(status, 0) + 1\n'
        '    s["avg_model_seconds"] = s["model_seconds"] / s["model_calls"] if s["model_calls"] else 0.0\n'
        "    return s\n",
    )
    return ""


# ─────────────────────────── 补测试：用"改坏之后它得红"来判 ───────────────────────────

CRLF_PROMPT = (
    "agent/tools/run_command.py 里的 _decode 会把 Windows 的换行 \\r\\n 统一成 \\n，"
    "但现有的测试没有检查这一点。请在 tests/test_run_command.py 里补一个测试守住这个行为，"
    "不要修改 agent/ 下的代码。\n"
    f"补完运行测试，确认全部通过。运行测试用这条命令：{TEST_COMMAND}"
)

# 变异体：把 _decode 里"统一换行"那一步删掉
CRLF_GOOD = '    return "".join(lines).replace("\\r\\n", "\\n")\n'
CRLF_MUTANT = '    return "".join(lines)\n'


def check_crlf(repo: Path, answer: str) -> tuple[bool, str]:
    """好的测试，在代码被改坏时一定会红。所以：故意把 _decode 改坏，看测试红不红。

    这和我们写每个测试时做的负对照是一回事，只是这次由程序替你做。
    行话叫"变异测试"，故意改坏的那个版本叫"变异体"。
    """
    ok, line = suite_passes(repo)
    if not ok:
        return False, f"测试没有全过：{line}"

    try:
        replace_in(repo, "agent/tools/run_command.py", CRLF_GOOD, CRLF_MUTANT)
    except RuntimeError:
        return False, "_decode 的最后一行被改动过，没法注入变异体（题目只要求补测试）"
    proc = pytest(repo, "tests/test_run_command.py")
    replace_in(repo, "agent/tools/run_command.py", CRLF_MUTANT, CRLF_GOOD)      # 改回来

    if proc.returncode == 0:
        return False, "删掉换行统一之后，tests/test_run_command.py 还是全绿：补的测试没守住这个行为"
    return True, "全部测试通过；删掉换行统一之后，新补的测试能把它抓出来"


def solve_crlf(repo: Path) -> str:
    path = repo / "tests" / "test_run_command.py"
    with path.open("a", encoding="utf-8") as f:
        f.write('\n\ndef test_crlf_becomes_lf():\n    assert _decode(b"a\\r\\nb\\r\\n") == "a\\nb\\n"\n')
    return ""


# ─────────────────────────── 问答：比对回答里的字 ───────────────────────────

TOOL_NAMES = ("echo", "edit_file", "list_dir", "load_skill", "read_file", "run_command", "search_text", "write_file")


def check_tools(repo: Path, answer: str) -> tuple[bool, str]:
    missing = [name for name in TOOL_NAMES if name not in answer]
    if missing:
        return False, f"回答里少了：{'、'.join(missing)}"
    if "8" not in answer and "八" not in answer:
        return False, "八个名字都列了，但没说一共几个"
    return True, "八个工具都答对了"


def solve_tools(repo: Path) -> str:
    return "一共 8 个：" + "、".join(TOOL_NAMES)


def no_setup(repo: Path) -> None:
    """这道题不用埋东西。"""


TASKS: list[Task] = [
    Task("fix-clip", "修 bug", BUG_PROMPT, setup_clip, check_bugfix("tests/test_run_command.py"), solve_clip),
    Task("fix-cursor", "修 bug", BUG_PROMPT, setup_cursor, check_bugfix("tests/test_history.py"), solve_cursor),
    Task("feature-avg", "加功能", AVG_PROMPT, no_setup, check_avg, solve_avg),
    Task("test-crlf", "补测试", CRLF_PROMPT, no_setup, check_crlf, solve_crlf),
    Task("qa-tools", "问答",
         "这个项目自带多少个工具（agent/tools/ 下会被自动发现的那些，不算 MCP 工具）？请逐个列出它们的名字。",
         no_setup, check_tools, solve_tools),
]

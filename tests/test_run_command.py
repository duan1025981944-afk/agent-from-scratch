"""run_command 工具的测试：在工作区里跑一条命令（改造 5）。

命令里的 python 一律写成 sys.executable 的完整路径：
这台机器上 `python` 不一定在 PATH 里，测试不该依赖它。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.payload import MAX_TOOL_RESULT_CHARS
from agent.tools import workspace
from agent.tools.run_command import RunCommandTool, _decode

PY = f'"{sys.executable}"'


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """工作区指到临时目录。"""
    monkeypatch.setattr(workspace, "_root", tmp_path.resolve())
    return tmp_path


def run(command: str, **kwargs) -> str:
    return asyncio.run(RunCommandTool().execute(command, **kwargs))


def is_error(result: str) -> bool:
    return getattr(result, "is_error", False)


def test_output_and_exit_code_come_back(sandbox):
    """最基本的：命令的输出和退出码都交回来。

    负对照：把 return 那行改成只返回输出、不写退出码 → 这条红。
    """
    result = run(f'{PY} -c "print(12345)"')

    assert "退出码：0" in result
    assert "12345" in result
    assert not is_error(result)


def test_nonzero_exit_is_reported_not_treated_as_error(sandbox):
    """★ 退出码不是 0 不算出错：测试没过也是"命令好好跑完了"。

    标成错误的话，runner 会追加"不要用同样的参数重试"，
    可改完代码之后恰恰要用同样的命令再跑一遍测试。

    负对照：退出码不是 0 时改成 return ToolResult.error(...) → 这条红。
    """
    result = run("exit 3")

    assert "退出码：3" in result
    assert not is_error(result)


def test_runs_in_the_workspace_root(sandbox):
    """命令在工作区根目录里跑，不是在启动 nb 的地方，也不是项目目录。

    负对照：把 create_subprocess_shell 里的 cwd=... 去掉 → 打印的是测试进程的目录，这条红。
    """
    result = run(f'{PY} -c "import os; print(os.getcwd())"')

    assert os.path.normcase(str(sandbox.resolve())) in os.path.normcase(result)


def test_error_output_is_included(sandbox):
    """错误输出也要交回来：报错信息往往就写在那里。

    负对照：把 stderr=asyncio.subprocess.STDOUT 去掉 → 错误输出没被接住，这条红。
    """
    result = run(f'{PY} -c "import sys; sys.stderr.write(\'boom\')"')

    assert "boom" in result


# 在另一个 Python 进程里调用 run_command。用法：python -c HELPER 工作区 命令
HELPER = (
    "import asyncio, sys\n"
    "from agent.tools import workspace\n"
    "from agent.tools.run_command import RunCommandTool\n"
    "workspace.set_root(sys.argv[1])\n"
    "print(asyncio.run(RunCommandTool().execute(sys.argv[2], timeout=5)))\n"
)


def test_a_command_waiting_for_input_fails_fast(sandbox):
    """★ 要等人输入的命令立刻失败，而不是卡到超时。

    子进程不另给输入的话，会接上 nb 自己的输入——也就是你的终端，它会一直等你敲字。
    测试里没人敲字，所以这样模拟：在另一个 Python 进程里调用 run_command，
    给那个进程接一根我们**永远不写**的管道当输入。
    timeout=5 是保险：负对照时命令会卡住，5 秒后被杀，测试不会挂死。

    负对照：把 stdin=asyncio.subprocess.DEVNULL 去掉 → 命令接上那根管道，卡满 5 秒，这条红。
    """
    read_end, write_end = os.pipe()                  # write_end 一直不关：读的那头永远等不到"结束"
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-c", HELPER, str(sandbox), f'{PY} -c "input()"'],
            stdin=read_end, capture_output=True, encoding="utf-8", timeout=30,
            cwd=Path(__file__).resolve().parent.parent,          # 项目目录：那边才 import 得到 agent
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    finally:
        os.close(read_end)
        os.close(write_end)

    assert "EOFError" in done.stdout                 # 读到的是"输入结束"，而不是一直等
    assert time.monotonic() - started < 4


def test_timeout_kills_the_whole_process_tree(sandbox):
    """★ 超时要杀掉整棵进程树，不能只杀 shell。

    cmd.exe 起的 python 是它的子进程。只杀 cmd.exe 的话，python 还活着：
    它睡完 2 秒会写出 alive.txt。整棵杀掉的话，这个文件永远不会出现。

    负对照：把超时分支里的 _kill_tree(proc) 换成 proc.kill() → alive.txt 出现，这条红（Windows 上）。
    """
    script = "import time; time.sleep(2); open('alive.txt', 'w').write('x')"
    result = run(f'{PY} -c "{script}"', timeout=1)

    assert is_error(result)
    assert "超过 1 秒" in result

    time.sleep(2.5)                                  # 等过 python 本该醒来的时间
    assert not (sandbox / "alive.txt").exists()


def test_huge_output_keeps_both_ends(sandbox):
    """输出太长时留开头和结尾，并且短到 payload 不会再从中间砍一刀。

    测试失败的汇总在输出的**最后**，只留开头的话正好把它丢了。

    负对照：把 _clip 改成只留开头 text[:MAX_OUTPUT_CHARS] → 没有"尾巴"，这条红。
    """
    script = "print('开头'); print('x' * 50000); print('尾巴')"
    result = run(f'{PY} -c "{script}"')

    assert "开头" in result
    assert "尾巴" in result
    assert "省略" in result
    assert len(result) <= MAX_TOOL_RESULT_CHARS


def test_mixed_utf8_and_gbk_lines_are_both_readable():
    """中文 Windows 上，git 的输出是 UTF-8，cmd 自己的输出（echo、报错）是 GBK，常常混在一起。
    一行一行地解，两种都不能变成乱码。

    负对照：把 _decode 改成整段一起解（先试 UTF-8，不行整段按 GBK）→ UTF-8 那行成了乱码，这条红。
    """
    data = "修复了一个问题\n".encode("utf-8") + "不是内部或外部命令\n".encode("gbk")

    assert _decode(data, fallback="gbk") == "修复了一个问题\n不是内部或外部命令\n"


def test_empty_command_is_rejected(sandbox):
    """空命令不执行，直接告诉模型。

    负对照：把 `if not command.strip()` 那段去掉 → 空命令照样跑，退出码 0，这条红。
    """
    assert is_error(run("   "))


def test_run_command_needs_confirmation():
    """★ 它会改动东西，必须声明 read_only = False，runner 才会先问用户。

    负对照：把 run_command.py 里的 read_only 改成 True → 这条红。
    """
    assert RunCommandTool.read_only is False


def test_python_output_keeps_its_order(sandbox):
    """★ 子进程先往标准输出写、再往标准错误写，交给模型时也得是这个顺序。

    输出接在管子上时，Python 会把标准输出先攒在缓冲区里，标准错误却写一句送一句。
    不处理的话，报错会跑到前面去（补课时实测：模型收到的是"后"在"先"前面）。

    负对照：把 env 里的 PYTHONUNBUFFERED 去掉 → "后"跑到"先"前面，这条红。
    """
    result = run(f'{PY} -c "import sys; print(\'先\'); print(\'后\', file=sys.stderr)"')

    assert result.index("先") < result.index("后")

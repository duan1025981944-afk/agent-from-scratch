r"""run_command 工具：在工作区里跑一条命令，把退出码和输出交回给模型。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
让 Agent 能跑测试、看 git 状态、执行脚本。"改完代码自己验证一遍"靠的就是它。

    模型给一条命令  ->  在工作区根目录里起一个子进程去执行
                    ->  最多等 timeout 秒
                    ->  把"退出码 + 输出"交回去

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**问用户同不同意——那是 runner 的事。本工具声明了 read_only = False，
  runner 执行前会先问（见 agent/runner.py 的 confirm）
- 它**不**是沙箱。命令能做到你自己在终端里能做的一切：读写工作区外面的文件、
  改 Agent 自己的 data/、联网。workspace.py 的围栏和禁区只管文件类工具，
  **拦不住这里**。挡在它前面的只有执行前那一问
- 它**不**支持交互：stdin 接的是空设备，要等人输入的命令会立刻读到"输入结束"
- 它**不**适合一直不结束的命令（起服务器、watch 模式）——超时就整个杀掉

──────────────────────────────────────────────────────────────
在哪个 shell 里跑
──────────────────────────────────────────────────────────────
asyncio.create_subprocess_shell 用的是系统默认的 shell：

    Windows      cmd.exe      能用 &&；没有 ls / cat / grep
    其他系统     /bin/sh

nanobot 在 Windows 上默认用 PowerShell（见它 shell.py 里 ExecTool 的 description）。
我们用 cmd.exe，原因有二：一行代码就能跨平台，不用自己去找 shell；
Windows 自带的 PowerShell 5.1 不支持 &&，而模型最习惯写 `cd x && y`。
看文件、列目录、搜文字本来就有专门的工具，用不着 ls / cat / grep。

──────────────────────────────────────────────────────────────
Windows 上的两个坑
──────────────────────────────────────────────────────────────
1. 编码。中文 Windows 上，cmd 自己的输出（echo、"不是内部或外部命令"）是 GBK，
   git 和 Python 程序的输出又是 UTF-8，一次输出里还常常两种混着。
   所以一行一行地解：先按 UTF-8，解不开再按系统编码（见 _decode）。
   另外给子进程设了 PYTHONIOENCODING=utf-8，让 Python 子进程统一输出 UTF-8。

2. 杀进程。cmd.exe 起的 python 是它的**子进程**。超时的时候只杀 cmd.exe 的话，
   python 还活着，接着干它的活。所以要杀**整棵进程树**：
   Windows 用 taskkill /T，其他系统杀整个进程组。
   nanobot 的 ExecTool._kill_process_tree 也是这两招。

──────────────────────────────────────────────────────────────
退出码不是 0，算不算出错
──────────────────────────────────────────────────────────────
**不算。** 测试没通过、git 报告有冲突，退出码都不是 0，但命令本身好好地跑完了，
输出正是模型要看的东西。要是标成错误，runner 会在后面追加一句
"不要用同样的参数重试"——可改完代码之后，恰恰要用同样的命令再跑一遍测试。

只有"命令根本没跑起来"和"超时被杀"才标成错误。
"""
from __future__ import annotations

import asyncio
import locale
import os
import signal
import subprocess
import sys
from contextlib import suppress

from agent.payload import MAX_TOOL_RESULT_CHARS
from agent.tools import workspace
from agent.tools.base import Tool, ToolResult

IS_WINDOWS = sys.platform == "win32"

# 默认等 60 秒，模型最多能要到 600 秒。两个数和 nanobot 的 ExecTool 一样。
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600

# 输出最多留多少字符。
#
# 为什么比 payload 的 MAX_TOOL_RESULT_CHARS（8,000）小
#     payload 截断时只留**开头**。可命令的输出最要紧的常常在**结尾**——
#     测试失败的汇总、报错的最后一行都在最后。所以这里先自己剪：
#     留开头一小段 + 结尾一大段，并且剪到 payload 不会再动它的长度
#     （留 200 个字符给开头那行"退出码"和中间那行省略说明）。
MAX_OUTPUT_CHARS = MAX_TOOL_RESULT_CHARS - 200
HEAD_CHARS = 2_000

_SHELL_NOTE = (
    "在 Windows 上命令由 cmd.exe 执行：可以用 &&，没有 ls、cat、grep 这些命令。\n"
    "`python` 可能不在 PATH 里：工作区有 .venv 时用 .venv\\Scripts\\python.exe。"
    if IS_WINDOWS
    else "命令由 /bin/sh 执行。"
)


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "在工作区根目录里执行一条命令，返回退出码和输出（标准输出和错误输出合在一起）。\n"
        "适合：跑测试、查看 git 的状态和改动、执行脚本。\n"
        "每次执行前都会先请用户确认，用户可能拒绝。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 看文件、列目录、搜文字——用 read_file、list_dir、search_text\n"
        "- 修改文件——用 edit_file 或 write_file\n"
        "- 需要人在中途输入的命令——这里没有输入，会立刻失败\n"
        "- 一直不结束的命令（启动服务器、watch 模式）——超时会被强制终止\n"
        "\n"
        f"{_SHELL_NOTE}"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令，如 git status 或 .venv\\Scripts\\python.exe -m pytest -q",
            },
            "timeout": {
                "type": "integer",
                "description": f"最多等几秒，默认 {DEFAULT_TIMEOUT}，最大 {MAX_TIMEOUT}。跑完整测试集这类慢命令可以调大",
            },
        },
        "required": ["command"],
    }

    read_only = False        # 有副作用：runner 执行前会先问用户

    async def execute(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        if not command.strip():
            return ToolResult.error("command 不能为空。")
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workspace.get_root(),                  # 在工作区根目录里跑
                stdin=asyncio.subprocess.DEVNULL,          # 不给输入：要等人输入的命令立刻失败，而不是卡住
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,          # 错误输出并进标准输出，先后顺序不乱
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                start_new_session=not IS_WINDOWS,          # 其他系统：自成一个进程组，超时好整组杀
            )
        except OSError as exc:
            return ToolResult.error(f"命令没能启动：{exc}")

        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            _kill_tree(proc)
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 5)
            return ToolResult.error(
                f"命令超过 {timeout} 秒还没结束，已强制终止：{command}\n"
                "如果它本来就要跑这么久，把 timeout 调大再试；"
                "如果它在等人输入，或者是个不会自己结束的命令（服务器、watch 模式），换个做法。"
            )
        except asyncio.CancelledError:
            _kill_tree(proc)                               # 用户按了 Ctrl+C：别把子进程留在后台
            raise

        text = _clip(_decode(out)) or "（没有输出）"
        return f"退出码：{proc.returncode}\n{text}"


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """杀掉 proc 和它起的所有子进程。已经结束了就什么都不做。

    为什么不用 proc.kill()
        proc 是 shell（cmd.exe 或 /bin/sh），真正干活的是它的子进程。
        proc.kill() 只杀 shell，干活的那个会被留下来接着跑。
    """
    if proc.returncode is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],     # /T = 连同子进程，/F = 强制
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)                   # 整个进程组


def _decode(data: bytes, fallback: str | None = None) -> str:
    """把子进程的输出变成文字：**一行一行**地解，先按 UTF-8，不行再按系统编码。

    传入什么
        data:     子进程吐出来的原始字节。
        fallback: UTF-8 解不开时用的编码。不传就用系统编码（中文 Windows 上是 cp936，
                  也就是 GBK）。测试里会显式传 "gbk"，这样换台机器结果也一样。

    为什么要一行一行地解
        一次输出里常常两种编码混着：`git log && echo 完成`，git 吐的是 UTF-8，
        cmd 的 echo 吐的是 GBK。整段一起解的话，UTF-8 解不开整段，
        只好整段按 GBK 解——git 那几行就全成了乱码。这是实测撞到的。
        按行解，每行各用各的编码。

    例子
        >>> _decode("中文".encode("utf-8"))
        '中文'
        >>> _decode("修复\\n".encode("utf-8") + "完成\\n".encode("gbk"), fallback="gbk")
        '修复\\n完成\\n'
        >>> _decode(b"a\\r\\nb\\r\\n")                  # Windows 的换行统一成 \\n
        'a\\nb\\n'
    """
    encoding = fallback or locale.getpreferredencoding(False)
    lines = []
    for line in data.splitlines(keepends=True):
        try:
            lines.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            lines.append(line.decode(encoding, errors="replace"))
    return "".join(lines).replace("\r\n", "\n")


def _clip(text: str) -> str:
    """输出太长时，留开头 HEAD_CHARS 个字符和结尾的一大段，中间写明省略了多少。

    例子
        >>> _clip("短的")
        '短的'
        >>> out = _clip("头" + "x" * 50_000 + "尾")
        >>> out.startswith("头") and out.endswith("尾") and len(out) < MAX_OUTPUT_CHARS + 100
        True
    """
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    tail = MAX_OUTPUT_CHARS - HEAD_CHARS
    dropped = len(text) - MAX_OUTPUT_CHARS
    return f"{text[:HEAD_CHARS]}\n\n…（中间省略 {dropped:,} 个字符）…\n\n{text[-tail:]}"

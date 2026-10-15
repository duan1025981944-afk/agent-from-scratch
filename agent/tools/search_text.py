"""search_text 工具：在文件内容里找一段文字，告诉模型它出现在哪个文件的哪一行。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
找代码用的工具。没有它，模型只能 list_dir 一层层看、再把文件整个读出来；
有了它，可以直接问"def build_payload 写在哪个文件的第几行"。

返回的每一行长这样：

    agent/payload.py:281: def build_payload(

拿到行号之后，再用 read_file 带 offset / limit 去看前后文。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**用正则表达式，按原样匹配。正则功能强，但模型很容易写出不合法的式子
- 它**不**列目录——那是 list_dir
- 它**不**搜这些地方：点开头的目录（.git、.venv）、缓存目录、
  Agent 自己的 data/（那是存档，不是你要找的代码）

──────────────────────────────────────────────────────────────
为什么自己限制输出的大小
──────────────────────────────────────────────────────────────
工具结果超过单条上限会在发送前被截断，而且可能正好切在一行中间。
所以这里先管好自己：最多 MAX_MATCHES 处，每处最多 MAX_LINE_CHARS 个字符，
超出的用一句话说明"一共多少处、怎么缩小范围"。和 list_dir 的 MAX_ENTRIES 同一个道理。

──────────────────────────────────────────────────────────────
没找到算不算出错
──────────────────────────────────────────────────────────────
不算。它是一个正常的答案，所以返回普通字符串而不是 ToolResult.error。
但不能返回空字符串——空的会让模型以为工具坏了。
"""
import os
from pathlib import Path

from agent.tools import workspace
from agent.tools.base import Tool, ToolResult
from agent.tools.workspace import OutsideWorkspace

# 最多列多少处。条目级截断，和 list_dir 的 MAX_ENTRIES 同一个道理：
# 在这里截，模型收到的是"完整的 50 行 + 一句说明"，不会被字符级截断切成半行。
MAX_MATCHES = 50

# 每处最多显示多少个字符。防的是一行特别长的文件（压缩过的 JS、存档 jsonl）。
MAX_LINE_CHARS = 200

# 超过这么大的文件不搜：基本不是人写的源码。
MAX_FILE_BYTES = 1_000_000

# 这些目录不进去。里面不是你要找的东西，而且文件极多。
SKIP_DIRS = {"__pycache__", "node_modules", "venv"}


def _walk(top: Path) -> list[Path]:
    """列出 top 下面所有要搜的文件，按路径排好序（顺序稳定，两次结果才对得上）。"""
    protected = set(workspace.NO_WRITE_DIRS)
    found = []
    for folder, dirs, files in os.walk(top):
        here = Path(folder)
        # 原地改 dirs：os.walk 就不会再往被删掉的那些目录里走
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and d not in SKIP_DIRS and here / d not in protected
        )
        found.extend(here / name for name in sorted(files))
    return found


class SearchTextTool(Tool):
    name = "search_text"
    read_only = True 
    description = (
        "在文件内容里搜索一段文字，返回它出现的每个位置，一行一处：路径:行号: 那一行的内容。\n"
        "适用于：找某个函数、变量、报错信息写在哪个文件的哪一行。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 想知道目录里有哪些文件——用 list_dir\n"
        "- 已经知道在哪个文件、想看前后文——用 read_file，带上 offset 和 limit 只读那一段"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "要找的文字。按原样匹配（不是正则表达式），区分大小写，如 def build_payload",
            },
            "path": {
                "type": "string",
                "description": "在哪里找：一个目录或一个文件，相对于工作区根目录。不写就是整个工作区",
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, pattern: str, path: str = ".") -> str:
        try:
            p = workspace.resolve(path)                  # 只读不写：过围栏就够了
        except OutsideWorkspace as exc:
            return ToolResult.error(str(exc))

        if not p.exists():
            return ToolResult.error(f"路径不存在：{path}")
        if not pattern:
            return ToolResult.error("pattern 不能为空。")

        root = workspace.get_root()
        files = [p] if p.is_file() else _walk(p)

        hits: list[str] = []
        total = 0
        for f in files:
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                      # 二进制文件、读不了的文件：跳过，不让整次搜索失败
            for number, line in enumerate(text.splitlines(), 1):     # 行号从 1 数
                if pattern not in line:
                    continue
                total += 1
                if len(hits) < MAX_MATCHES:
                    shown = line.strip()
                    if len(shown) > MAX_LINE_CHARS:
                        shown = shown[:MAX_LINE_CHARS] + "…"
                    hits.append(f"{f.relative_to(root).as_posix()}:{number}: {shown}")

        if not hits:
            # 没找到不是出错，是一个正常的答案。但要说清楚搜的是什么、在哪搜的。
            return (f"没有找到 {pattern!r}（搜索范围：{path}）。"
                    "匹配是按原样、区分大小写的；可以只搜其中最特别的一个词再试。")
        if total > MAX_MATCHES:
            hits.append(f"…（共 {total} 处，只显示前 {MAX_MATCHES} 处。用 path 缩小范围，或换更具体的 pattern）")
        return "\n".join(hits)

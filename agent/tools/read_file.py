"""read_file 工具：读取一个文本文件的完整内容。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
Agent 最常用的工具。它让模型能"看见"磁盘上的文件。

代码本身很简单——解析路径、几个前置检查、读文件。真正花心思的是
**description 和错误信息**，见下面两节。

──────────────────────────────────────────────────────────────
为什么 description 里要写"不要用于……"
──────────────────────────────────────────────────────────────
只写"能干什么"的话，模型会在不该用的场合也用它，然后浪费一整圈。

有实测数字：Skill 描述没有反例时选对率 73%，加上反例后升到 85%。
工具描述同理。**反例不是可选项，是路由准不准的关键。**

这里写了三条反例，每条都对应一种真实会发生的误用：

    想看目录里有什么   -> 模型会用 read_file 读目录，然后拿到一条报错
    读 .docx/.pdf      -> 二进制文件，读出来是乱码或者直接失败
    靠猜路径试探       -> 猜不中只会浪费一轮，先 list_dir 确认再读

第三条最有价值。上一次实测里，用户说"读一下 agent/runner"（少了 .py），
模型先调了 list_dir 确认文件名，再调 read_file——**正是这条反例在起作用**。

──────────────────────────────────────────────────────────────
为什么错误信息要写这么细
──────────────────────────────────────────────────────────────
工具报错对模型来说和工具成功是同一件事——**都是一段文本**。
它读完这段文本，自己决定下一步。所以错误信息的质量，直接决定它能不能自己爬起来。

    差：  "Error"                         模型只能盲目重试
    好：  "xxx 是一个目录，不是文件"        模型知道该换 list_dir

这个文件里四种失败各写了一句具体的说明，其中"是目录"那条还**反向指路**，
直接告诉模型该用哪个工具。
"""
from agent.payload import MAX_TOOL_RESULT_CHARS
from agent.tools import workspace
from agent.tools.base import Tool, ToolResult
from agent.tools.workspace import OutsideWorkspace

# 一次最多返回这么多字符。比发送前的单条上限（MAX_TOOL_RESULT_CHARS）少一点，
# 给末尾那句"第几行到第几行"的说明留出位置——这样 read_file 的结果
# 不会再被发送前的截断切成半截。
PAGE_CHARS = MAX_TOOL_RESULT_CHARS - 500


def _page(content: str, offset: int | None, limit: int | None) -> str:
    r"""从 content 里取出要的那几行；一次装不下就只给前面装得下的，并说明怎么接着读。

    传入什么
        content: 文件全文。
        offset:  从第几行开始（行号从 1 数）。None = 从头。
        limit:   最多几行。None = 装得下多少给多少。

    返回什么
        整个文件都装得下  ->  原样返回全文，不加任何说明（和分段读出现之前一样）
        只给了其中一段    ->  那几行 + 末尾一句说明：
                              …（第 1–251 行，文件共 534 行。接着读：offset=252）
        offset / limit 不对 ->  ToolResult.error，并说出文件一共几行

    三条规矩
        整行整行地给   不会把一行切成两半
        至少给一行     哪怕这一行本身就超长——那种情况交给发送前的截断去处理
        不超过 PAGE_CHARS  所以结果不会再被 agent/payload.py 切一刀

    例子
        >>> _page("a\nb\n", None, None)
        'a\nb\n'
        >>> _page("a\nb\nc\n", 2, 1)
        'b\n\n…（第 2–2 行，文件共 3 行。接着读：offset=3）'
    """
    lines = content.splitlines(keepends=True)
    total = len(lines)

    start = 1 if offset is None else offset
    if start < 1 or (total and start > total):
        return ToolResult.error(f"offset={start} 超出范围：这个文件共 {total} 行，行号从 1 开始。")
    if limit is not None and limit < 1:
        return ToolResult.error(f"limit={limit} 不对：至少要读 1 行。")

    # 行号从 1 数，列表下标从 0 数：第 start 行在列表里的下标是 start - 1
    first = start - 1
    wanted = lines[first:] if limit is None else lines[first:first + limit]

    picked: list[str] = []
    size = 0
    for line in wanted:
        if picked and size + len(line) > PAGE_CHARS:
            break                     # 再加这一行就装不下了。但至少给一行
        picked.append(line)
        size += len(line)

    end = start - 1 + len(picked)     # 这次读到了第几行
    body = "".join(picked)
    if start == 1 and end == total:
        return body                   # 整个文件都在这里了：原样返回，不加说明

    tail = "已到文件末尾" if end == total else f"接着读：offset={end + 1}"
    return f"{body.rstrip()}\n\n…（第 {start}–{end} 行，文件共 {total} 行。{tail}）"


class ReadFileTool(Tool):
    """读取一个 UTF-8 文本文件，返回原文；文件太长时一段一段地给。

    谁会产生它
        agent/tools/loader.py 的 discover_tools()，启动时自动实例化。

    谁会调它
        模型。这是四个工具里被调用最多的一个。

    类属性
        name         "read_file"
        description  用途 + **三条反例**（见文件顶部说明）
        parameters   必填的 path；可选的 offset（从第几行开始）和 limit（最多几行）

    文件太长怎么办（改造 3 改的）
        以前：工具返回全文，发给模型之前由 agent/payload.py 截到单条上限。
        结果是超过 8,000 字符的文件，模型永远只看得到开头——连本项目的 main.py 都读不全。

        现在：工具自己分段（见 _page）。一次只给装得下的那几行，末尾告诉模型
        "这是第几行到第几行、接着读用哪个 offset"，模型照着就能把文件读完。

        代价：大文件在存档里存的是每次读到的那一段，不再是全文——
        "调大阈值后历史立刻按新值生效"这一条，对 read_file 的大文件不再成立。
        换来的是模型真的能把任意大小的文件读完，值得。
        发送前的截断还在，继续给别的工具（MCP、单行超长的文件）兜底。

    例子
        >>> tool = ReadFileTool()
        >>> tool.to_schema()["function"]["name"]
        'read_file'
        >>> "不要用于" in tool.description          # 反例写在描述里
        True
    """

    name = "read_file"
    read_only = True 

    description = (
        "读取一个文本文件，返回文件里的原文。\n"
        "适用于：查看配置文件、日志、源代码、Markdown 笔记等文本文件。\n"
        "文件太长时只返回开头的一段，并在末尾注明这是第几行到第几行、接着读要用的 offset。\n"
        "也可以用 offset（从第几行开始，行号从 1 数）和 limit（最多读几行）只读其中一段。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 想知道某个目录里有哪些文件——这个工具只能读单个文件，读目录会报错\n"
        "- 读取 .docx / .xlsx / .pdf 等二进制文件——会失败\n"
        "- 靠猜路径来试探文件在不在——猜不中只会浪费一轮，先确认路径再读"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": r"文件路径，可以是相对路径（config.json）或绝对路径（D:\VibeCoding\notes.md）",
            },
            "offset": {
                "type": "integer",
                "description": "从第几行开始，行号从 1 数。不写就从头读",
            },
            "limit": {
                "type": "integer",
                "description": "最多读几行。不写就尽量多读",
            },
        },
        "required": ["path"],
    }

    async def execute(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        """读取文件，返回内容或一条说明为什么读不了的错误。

        传入什么
            path: 文件路径。相对路径相对于**工作区根目录**，不是当前目录。
                  绝对路径也接受，但会被越界检查拦住（除非它正好在工作区内）。

        返回什么
            成功：文件内容（普通 str）。装不下时只有一段，末尾带着接着读的 offset，见 _page。
            失败：ToolResult.error(...)，是个带 is_error 标记的字符串。

            调用方（runner）统一用 str(result) 拿文本，用 result.is_error
            判断要不要追加修正建议。

        四种失败，各有各的说明
            越界          路径跑出工作区。消息来自 workspace.resolve()，
                          里面带着工作区根目录，模型知道边界在哪
            文件不存在     "文件不存在：xxx"
            是个目录       "xxx 是一个目录，不是文件。read_file 只能读取单个文件。"
                          —— **反向指路**，模型看完知道该换 list_dir
            不是 UTF-8     "可能是二进制文件（如图片、Word 文档）"

        为什么必须显式写 encoding="utf-8"
            Windows 上 Python 的默认编码是 GBK（简体中文系统）。
            不写的话，读任何含中文的 UTF-8 文件都会抛 UnicodeDecodeError——
            而且这个坑在 Linux/macOS 上完全不会出现，很难想到。

        为什么不抛异常
            见 agent/tools/base.py 的 ToolResult.error 说明：
            抛异常会中断整个循环，Agent 就只剩"成功或崩溃"两条路。
        """
        try:
            p = workspace.resolve(path)
        except OutsideWorkspace as exc:
            return ToolResult.error(str(exc))

        if not p.exists():
            return ToolResult.error(f"文件不存在：{path}")

        if p.is_dir():
            # 反向指路：不只说"不行"，还告诉模型该用哪个工具
            return ToolResult.error(
                f"{path} 是一个目录，不是文件。read_file 只能读取单个文件。"
            )

        try:
            # Windows 上必须显式指定 utf-8，否则默认按 GBK 读，中文文件会炸
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.error(
                f"{path} 不是 UTF-8 文本文件，可能是二进制文件（如图片、Word 文档）。"
            )
        except OSError as exc:
            # 权限不足、文件被占用、路径太长等等，都落在这里
            return ToolResult.error(f"读取 {path} 失败：{exc}")

        return _page(content, offset, limit)

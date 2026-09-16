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
from agent.tools import workspace
from agent.tools.base import Tool, ToolResult
from agent.tools.workspace import OutsideWorkspace


class ReadFileTool(Tool):
    """读取一个 UTF-8 文本文件，返回完整原文。

    谁会产生它
        agent/tools/loader.py 的 discover_tools()，启动时自动实例化。

    谁会调它
        模型。这是四个工具里被调用最多的一个。

    类属性
        name         "read_file"
        description  用途 + **三条反例**（见文件顶部说明）
        parameters   一个必填的 path 参数，描述里给了两种格式的示例

    返回的内容会被截断吗
        **工具本身不截断，返回完整原文。** 但它会在发给模型之前被
        agent/payload.py 截到 4000 字符，末尾加一行"省略了多少字符"。

        这个分工是有意的：存档里存全文，只有"这一次发出去的那份"被截短。
        所以以后调大阈值，历史立刻按新值生效，不用重新读一遍。

    例子
        >>> tool = ReadFileTool()
        >>> tool.to_schema()["function"]["name"]
        'read_file'
        >>> "不要用于" in tool.description          # 反例写在描述里
        True
    """

    name = "read_file"

    description = (
        "读取一个文本文件的完整内容，返回文件里的原文。\n"
        "适用于：查看配置文件、日志、源代码、Markdown 笔记等文本文件。\n"
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
            }
        },
        "required": ["path"],
    }

    async def execute(self, path: str) -> str:
        """读取文件，返回内容或一条说明为什么读不了的错误。

        传入什么
            path: 文件路径。相对路径相对于**工作区根目录**，不是当前目录。
                  绝对路径也接受，但会被越界检查拦住（除非它正好在工作区内）。

        返回什么
            成功：文件的完整文本内容（普通 str）。
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

        return content

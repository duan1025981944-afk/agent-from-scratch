r"""edit_file 工具：把文件里的一小段文字换成另一段，其余部分不动。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
改已有文件用的工具。模型只要说"把这段原文换成这段新内容"，
别的地方程序原样保留。

为什么需要它：write_file 要模型给出**整个文件**。文件一大，模型既看不全，
重写时也容易把别的地方抄错。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**新建文件——那是 write_file
- 它**不**按行号改——行号在两次读取之间可能已经变了，原文不会骗人
- 它**不**猜：原文对不上、或者对上了不止一处，一个字都不改

──────────────────────────────────────────────────────────────
old_text 出现几次，决定做什么
──────────────────────────────────────────────────────────────
    0 次   不改。告诉模型"和原文对不上"，让它先 read_file 看清楚
    1 次   替换
    多次   不改。告诉模型出现了几次，让它多带几行上下文

多次时为什么不改第一处：程序不知道模型想改哪一处。猜错了文件被悄悄改坏，
回执还写着"已修改"，谁都发现不了。**宁可明确拒绝，也不要悄悄做错。**

──────────────────────────────────────────────────────────────
换行为什么要特别处理
──────────────────────────────────────────────────────────────
Windows 上的文件常常用 \r\n 换行，模型看到的原文（read_file 给的）是 \n 的。
所以比对前统一成 \n，写回去前再还原成文件原来的样子——
不然改一行，git 会认为整个文件每一行都变了。

──────────────────────────────────────────────────────────────
和禁区的关系
──────────────────────────────────────────────────────────────
它会写文件，所以走 workspace.resolve_for_write()，和 write_file 一样
不能碰 Agent 自己的 data/（不变量 6）。tests/test_edit_file.py 里有一条专门守这个。
"""
from agent.tools import workspace
from agent.tools.base import Tool, ToolResult
from agent.tools.workspace import OutsideWorkspace, ProtectedPath


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "修改一个已有文件的一小部分：把 old_text 换成 new_text，其余内容原样保留。\n"
        "old_text 必须和文件里的原文一字不差（包括空格、缩进、换行），"
        "而且在文件里只能出现一次；出现多次时请多带几行上下文让它唯一。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 新建文件，或者整个文件都要重写——用 write_file\n"
        "- 还没看过原文就凭印象改——先用 read_file 或 search_text 看清楚要改的那几行\n"
        f"- 修改 {workspace.NO_WRITE_DIRS[0]} 目录里的文件——"
        "那里存的是 Agent 自己的聊天存档和长期记忆，只能读、不能改"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要修改的文件，相对于工作区根目录，如 agent/runner.py"},
            "old_text": {"type": "string", "description": "文件里要被换掉的原文，必须一字不差，且在文件里只出现一次"},
            "new_text": {"type": "string", "description": "换上去的新内容。想删掉这一段就传空字符串"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    read_only = False        # 显式声明：这个工具有副作用

    async def execute(self, path: str, old_text: str, new_text: str) -> str:
        try:
            p = workspace.resolve_for_write(path)        # 会写文件：多过一道禁区检查
        except (OutsideWorkspace, ProtectedPath) as exc:
            return ToolResult.error(str(exc))

        if not p.is_file():
            return ToolResult.error(f"文件不存在：{path}。新建文件请用 write_file。")
        if not old_text:
            return ToolResult.error("old_text 不能为空。要往空文件里写内容请用 write_file。")
        if old_text == new_text:
            return ToolResult.error("old_text 和 new_text 完全一样，没有要改的地方。")

        try:
            raw = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.error(f"{path} 不是 UTF-8 文本文件，无法修改。")
        except OSError as exc:
            return ToolResult.error(f"读取 {path} 失败：{exc}")

        # 换行统一成 \n 再比对：模型看到的原文（read_file 给的）就是 \n 的。
        # 写回去时再还原成文件原来的换行，免得整个文件的换行都被改掉。
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        old = old_text.replace("\r\n", "\n")
        new = new_text.replace("\r\n", "\n")

        count = text.count(old)
        if count == 0:
            return ToolResult.error(
                f"在 {path} 里没找到 old_text。它必须和原文一字不差（包括空格和缩进）。"
                "请先用 read_file 看一下要改的那几行，再照着原文复制。"
            )
        if count > 1:
            return ToolResult.error(
                f"old_text 在 {path} 里出现了 {count} 次，不知道该改哪一处。"
                "请多带几行上下文，让它在文件里只出现一次。"
            )

        line = text[: text.index(old)].count("\n") + 1   # 改动从第几行开始
        updated = text.replace(old, new, 1)
        if crlf:
            updated = updated.replace("\n", "\r\n")

        try:
            p.write_bytes(updated.encode("utf-8"))
        except OSError as exc:
            return ToolResult.error(f"写入 {path} 失败：{exc}")

        return (f"已修改 {path}：从第 {line} 行起，"
                f"{old.count(chr(10)) + 1} 行换成了 {new.count(chr(10)) + 1} 行。")

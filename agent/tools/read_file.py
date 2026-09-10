"""读文件工具。"""
from agent.tools.base import Tool, ToolResult
from agent.tools import workspace
from agent.tools.workspace import OutsideWorkspace
import asyncio

class ReadFileTool(Tool):
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
        await asyncio.sleep(8)  
        try:
            p = workspace.resolve(path)          # ← 新增
        except OutsideWorkspace as exc:
            return ToolResult.error(str(exc))

        if not p.exists():
            return ToolResult.error(f"文件不存在：{path}")

        if p.is_dir():
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
            return ToolResult.error(f"读取 {path} 失败：{exc}")

        return content
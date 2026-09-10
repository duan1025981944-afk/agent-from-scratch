"""写文件工具。"""
from agent.tools.base import Tool, ToolResult
from agent.tools import workspace
from agent.tools.workspace import OutsideWorkspace

MAX_CONTENT_CHARS = 100_000


class WriteFileTool(Tool):
    name = "write_file"

    description = (
        "把文本内容写入一个文件。目标文件已存在时会被完全覆盖。\n"
        "父目录不存在时会自动创建。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 只想修改文件的一部分——本工具会覆盖整个文件，"
        "要改一部分请先用 read_file 读出原文，改好后把完整内容传进来\n"
        "- 写入二进制内容——本工具只写 UTF-8 文本\n"
        "- 追加内容——本工具不支持追加，同样需要先读后写"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件路径，相对于工作区根目录"},
            "content": {"type": "string", "description": "要写入的完整文本内容"},
        },
        "required": ["path", "content"],
    }

    read_only = False        # 显式声明：这个工具有副作用

    async def execute(self, path: str, content: str) -> str:
        try:
            p = workspace.resolve(path)
        except OutsideWorkspace as exc:
            return ToolResult.error(str(exc))

        if len(content) > MAX_CONTENT_CHARS:
            return ToolResult.error(
                f"内容过长（{len(content):,} 字符，上限 {MAX_CONTENT_CHARS:,}）。"
                "请分成多个文件，或只写必要的部分。"
            )

        if p.is_dir():
            return ToolResult.error(f"{path} 是一个目录，无法写入。")

        existed = p.exists()
        old_size = p.stat().st_size if existed else 0

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult.error(f"写入 {path} 失败：{exc}")

        # ★ 回执要说清楚"发生了什么"，尤其是覆盖
        if existed:
            return (f"已覆盖 {path}：原 {old_size:,} 字节 → "
                    f"新 {len(content.encode('utf-8')):,} 字节。")
        return f"已创建 {path}，写入 {len(content.encode('utf-8')):,} 字节。"

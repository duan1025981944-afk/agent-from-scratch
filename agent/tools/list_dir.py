"""列目录工具。"""
from agent.tools.base import Tool, ToolResult
from agent.tools import workspace
from agent.tools.workspace import OutsideWorkspace

MAX_ENTRIES = 200          # 条目级截断，比 runner 的字符级截断更早也更干净


class ListDirTool(Tool):
    name = "list_dir"

    description = (
        "列出一个目录下的文件和子目录。\n"
        "返回结果每行一个条目：目录以 / 结尾，文件后面标着字节数。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 查看文件内容——本工具只列出名字，读内容要用 read_file\n"
        "- 递归遍历整棵目录树——本工具只列一层，要往下看就对子目录再调一次"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径，相对或绝对均可。用 . 表示当前目录",
            }
        },
        "required": ["path"],
    }

    async def execute(self, path: str) -> str:
        try:
            p = workspace.resolve(path)          # ← 新增
        except OutsideWorkspace as exc:
            return ToolResult.error(str(exc))

        if not p.exists():
            return ToolResult.error(f"目录不存在：{path}")

        if not p.is_dir():
            # 反向指路：告诉模型该用哪个工具
            return ToolResult.error(
                f"{path} 是一个文件，不是目录。读取文件内容请用 read_file。"
            )

        try:
            # 目录排前面（is_file() False=0），各自按名字不分大小写排序
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except OSError as exc:
            return ToolResult.error(f"读取目录 {path} 失败：{exc}")

        if not entries:
            return f"{path} 是一个空目录。"

        lines = []
        for e in entries[:MAX_ENTRIES]:
            if e.is_dir():
                lines.append(f"{e.name}/")           # 尾部斜杠 = 目录标记
            else:
                try:
                    lines.append(f"{e.name}  ({e.stat().st_size:,} 字节)")
                except OSError:
                    lines.append(f"{e.name}  (大小未知)")

        if len(entries) > MAX_ENTRIES:
            lines.append(f"…（共 {len(entries)} 个条目，只显示前 {MAX_ENTRIES} 个）")

        return "\n".join(lines)
"""write_file 工具：把文本内容写入一个文件。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
四个工具里**唯一有副作用的那个**。前三个（echo / read_file / list_dir）
再怎么调用也只是读，这个会真的改动磁盘。

所以它比别的工具多做三件事：

    1. description 里把"会完全覆盖"说到最狠
    2. 加了内容长度上限
    3. 回执里说清楚"覆盖了什么"——原多少字节、新多少字节

──────────────────────────────────────────────────────────────
为什么"整文件覆盖"是最危险的一种改法
──────────────────────────────────────────────────────────────
模型想改一行，但只有 write_file 可用时，它必须把**整个文件重新输出一遍**。
只要中间有一行记错或漏掉，其余内容全部丢失——这是 Agent 改文件时最常见的事故。

nanobot 的做法是提供三种粒度的工具：

    write_file    首次创建，或有意的整文件重写
    edit_file     从原文复制出来的小范围精确替换
    apply_patch   一次改多处

用错的代价是**不对称的**：用 edit_file 只提供"旧文本 -> 新文本"，
其余部分**物理上不可能**被动到。

我们只有 write_file，所以只能靠两道软约束顶着：
description 里的反例，以及 templates/AGENTS.md 里那条
"不要在没有被要求时创建新文件或新目录"。

**这是已知的缺口，不是设计完备。** 真要自主跑长任务之前，
应该先补一个 edit_file，以及执行前的用户确认机制（记在待办里）。
"""
from agent.tools import workspace
from agent.tools.base import Tool, ToolResult
from agent.tools.workspace import OutsideWorkspace, ProtectedPath

# 单次写入的字符上限。
#
# 防的是模型一口气生成一个巨大的文件——那通常意味着它跑偏了
# （比如把整个对话历史写进文件），而不是真有这么多内容要写。
MAX_CONTENT_CHARS = 100_000


class WriteFileTool(Tool):
    """把文本写入文件。**目标文件已存在时会被完全覆盖。**

    谁会产生它
        agent/tools/loader.py 的 discover_tools()，启动时自动实例化。

    谁会调它
        模型。

    类属性
        name         "write_file"
        description  用途 + **三条反例**，全都在强调"它会覆盖整个文件"
        parameters   两个必填参数：path 和 content
        read_only    False —— 显式声明这个工具有副作用

    read_only 这个属性现在有人用吗
        **没有。** 它是给将来准备的：

            - 用户确认机制：只对 read_only=False 的工具弹确认
            - 并发执行：只读工具可以并发，有副作用的必须串行
              （nanobot 用 concurrency_safe 做这件事）

        现在写上它，是因为"这个工具有没有副作用"是**工具自己最清楚**的事，
        应该由它自己声明，而不是将来在别处维护一张名单。

    例子
        >>> tool = WriteFileTool()
        >>> tool.read_only
        False
        >>> "完全覆盖" in tool.description
        True
    """

    name = "write_file"

    description = (
        "把文本内容写入一个文件。目标文件已存在时会被完全覆盖。\n"
        "父目录不存在时会自动创建。\n"
        "\n"
        "不要用于以下情况：\n"
        "- 只想修改文件的一部分——本工具会覆盖整个文件，"
        "要改一部分请先用 read_file 读出原文，改好后把完整内容传进来\n"
        "- 写入二进制内容——本工具只写 UTF-8 文本\n"
        "- 追加内容——本工具不支持追加，同样需要先读后写\n"
        "- 写入 data/ 目录——那里存的是聊天存档和长期记忆，只能读、不能改"
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
        """写文件，返回一句说清"发生了什么"的回执。

        传入什么
            path:    目标文件路径，相对于工作区根目录。
                     **父目录不存在会自动创建**，不需要先建目录。
            content: 要写入的**完整**文本内容。不是追加，不是片段。

        返回什么
            成功：一句回执，两种形态——

                已创建 notes.md，写入 1,234 字节。
                已覆盖 notes.md：原 5,678 字节 → 新 1,234 字节。

            失败：ToolResult.error(...)

        为什么回执要这么详细
            **覆盖是不可逆的。** 回执里写清楚"原来多大、现在多大"，
            模型（和看日志的人）能立刻发现异常：

                已覆盖 README.md：原 8,084 字节 → 新 42 字节

            一眼就知道出事了——本来要改一行，结果把文件干掉了。
            只回一句"写入成功"的话，这个事故要等很久才会被发现。

        为什么用字节数而不是字符数
            字节数是**文件在磁盘上的真实大小**，和文件管理器里显示的一致，
            可以直接对得上。（代价是和截断说明的单位不统一，见待办。）

        四种失败
            越界          路径跑出工作区
            内容过长       超过 MAX_CONTENT_CHARS，提示分成多个文件
            目标是目录     不能往目录上写文件
            写入失败       权限不足、磁盘满、文件被占用等（OSError）

        为什么先检查长度、再检查是不是目录
            长度检查不碰磁盘，最便宜，放前面。这是个小习惯：
            **便宜的检查排前面，贵的排后面。**

        注意：它不做备份
            覆盖之前不留旧版本。要回滚只能靠 git。
            这也是"真要放权跑长任务前得先有回退能力"那条原则的一个缺口。
        """
        # 用 resolve_for_write 而不是 resolve：写入多一道禁区检查（data/ 不许写）
        try:
            p = workspace.resolve_for_write(path)
        except (OutsideWorkspace, ProtectedPath) as exc:
            return ToolResult.error(str(exc))

        if len(content) > MAX_CONTENT_CHARS:
            return ToolResult.error(
                f"内容过长（{len(content):,} 字符，上限 {MAX_CONTENT_CHARS:,}）。"
                "请分成多个文件，或只写必要的部分。"
            )

        if p.is_dir():
            return ToolResult.error(f"{path} 是一个目录，无法写入。")

        # 先记下覆盖前的状态，写完才好在回执里做对比
        existed = p.exists()
        old_size = p.stat().st_size if existed else 0

        try:
            p.parent.mkdir(parents=True, exist_ok=True)     # 父目录不存在就建
            p.write_text(content, encoding="utf-8")         # 显式 utf-8，理由同 read_file
        except OSError as exc:
            return ToolResult.error(f"写入 {path} 失败：{exc}")

        # ★ 回执要说清楚"发生了什么"，尤其是覆盖
        if existed:
            return (f"已覆盖 {path}：原 {old_size:,} 字节 → "
                    f"新 {len(content.encode('utf-8')):,} 字节。")
        return f"已创建 {path}，写入 {len(content.encode('utf-8')):,} 字节。"
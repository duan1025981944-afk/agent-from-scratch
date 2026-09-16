"""list_dir 工具：列出一个目录下的文件和子目录。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
Agent 的"眼睛"——它让模型能先看清目录里有什么，再决定读哪个文件。

实测里它经常是**第一个被调用的工具**：用户说"读一下 xxx"但名字不准确时，
模型会先 list_dir 确认真实文件名，再 read_file。这正是 read_file 的
description 里那条反例（"不要靠猜路径试探"）在起作用。

──────────────────────────────────────────────────────────────
输出格式为什么这么设计
──────────────────────────────────────────────────────────────
    agent/                    <- 目录：末尾加斜杠
    config/
    main.py  (3,603 字节)      <- 文件：标上大小
    README.md  (8,084 字节)

**目录排前面、文件排后面**，各自按名字不分大小写排序。
排序固定的好处是：同一个目录两次调用结果一模一样，能命中提示缓存。

标出字节数是为了让模型**提前判断值不值得读**——看到一个 500KB 的日志文件，
它可以选择不读，或者读之前先问一句。

──────────────────────────────────────────────────────────────
一个已知的单位不一致
──────────────────────────────────────────────────────────────
这里报的是**字节**，而 agent/payload.py 截断工具结果时报的是**字符**。
中文一个字符是 3 个字节，两处混用时模型可能算错。

记在待办里，还没统一。目前影响不大，因为它俩很少同时出现在一句话里。
"""
from agent.tools import workspace
from agent.tools.base import Tool, ToolResult
from agent.tools.workspace import OutsideWorkspace

# 最多列多少个条目。
#
# 条目级截断，比 agent/payload.py 的字符级截断**更早也更干净**：
# 在这里截，模型收到的是"完整的 200 行 + 一句说明"；
# 等到字符级截断才动手的话，最后一行可能被切成半截，看着像出了故障。
MAX_ENTRIES = 200


class ListDirTool(Tool):
    """列出一个目录下的文件和子目录（只列一层，不递归）。

    谁会产生它
        agent/tools/loader.py 的 discover_tools()，启动时自动实例化。

    谁会调它
        模型。通常在"要读文件但不确定路径"时先调它。

    类属性
        name         "list_dir"
        description  用途 + 输出格式说明 + **两条反例**
        parameters   一个必填的 path 参数，说明里点出 "." 表示当前目录

    两条反例分别在挡什么
        "查看文件内容"     -> 挡住模型拿 list_dir 当 read_file 用
        "递归遍历整棵树"   -> 说清它只列一层，要往下看就对子目录再调一次。
                             不写的话模型可能以为一次就能拿到全部结构，
                             然后基于不完整的信息下结论

    例子
        >>> tool = ListDirTool()
        >>> tool.to_schema()["function"]["name"]
        'list_dir'
        >>> "只列一层" in tool.description
        True
    """

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
        """列出目录内容。

        传入什么
            path: 目录路径。"." 表示工作区根目录。

        返回什么
            成功：每行一个条目的多行字符串。形如

                agent/
                config/
                main.py  (3,603 字节)

            空目录：一句 "xxx 是一个空目录。"（不是空字符串——
                    空字符串会让模型以为工具坏了）

            失败：ToolResult.error(...)

        三种失败
            越界          消息来自 workspace.resolve()
            目录不存在     "目录不存在：xxx"
            是个文件       "xxx 是一个文件，不是目录。读取文件内容请用 read_file。"
                          —— **反向指路**，和 read_file 那边正好互相指

        排序那行是怎么回事
            key=lambda e: (e.is_file(), e.name.lower())

            Python 排元组时**从左到右逐项比较**。第一项 e.is_file() 是布尔值，
            False(0) 排在 True(1) 前面 —— 于是目录在前、文件在后。
            第一项相同时才比第二项，也就是按小写文件名排。

        条目多于 200 个时
            只列前 200 个，末尾加一句 "…（共 N 个条目，只显示前 200 个）"。
            告诉模型"还有更多"很重要，否则它会以为这就是全部。
        """
        try:
            p = workspace.resolve(path)
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
            # 返回一句话而不是空字符串——空字符串会让模型以为工具坏了
            return f"{path} 是一个空目录。"

        lines = []
        for e in entries[:MAX_ENTRIES]:
            if e.is_dir():
                lines.append(f"{e.name}/")           # 尾部斜杠 = 目录标记
            else:
                try:
                    lines.append(f"{e.name}  ({e.stat().st_size:,} 字节)")
                except OSError:
                    # 单个文件取不到大小不该让整次调用失败，标一句"未知"继续
                    lines.append(f"{e.name}  (大小未知)")

        if len(entries) > MAX_ENTRIES:
            lines.append(f"…（共 {len(entries)} 个条目，只显示前 {MAX_ENTRIES} 个）")

        return "\n".join(lines)

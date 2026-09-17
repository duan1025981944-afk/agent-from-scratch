"""把系统提示按"变动频率"分层拼装。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一条流水线：读几个 .md 文件，按固定顺序拼成一整条系统提示。

    SOUL.md      我是谁                  几乎不变      <- 缓存永远命中
    AGENTS.md    在这个工作区怎么做事      跟项目走
    运行时        工作区根目录            换目录启动才变
    长期记忆      MEMORY.md 正文          每次 Dream 后可能变（第 24 讲）

**稳定的在前，变动的在后。** 这个顺序不是为了好看，是为了省钱，
理由见下面"为什么顺序这么重要"。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**知道模板文件在磁盘哪个位置——那是 agent/prompts.py 的事
- 它**不**知道系统提示的**内容**是什么。内容全在 templates/ 下的 .md 里，
  想改 Agent 的性格或规矩，改那些文件，这个文件一个字都不用动
- 它**不**负责压缩、不负责发送

──────────────────────────────────────────────────────────────
为什么顺序这么重要
──────────────────────────────────────────────────────────────
模型会缓存已经算过的提示前缀，命中的部分价格便宜十倍
（DeepSeek：命中 0.014 美元/百万 token，未命中 0.14）。

但命中的判断是**从第 0 个 token 开始逐个匹配**的——中间改一个字，
从那个位置往后全部失效。

    上一次：  [SOUL][AGENTS][运行时: D:/proj-a]
    这一次：  [SOUL][AGENTS][运行时: D:/proj-b]
              └─ 命中 ─┘    └─ 从这里开始全部重算 ─┘

如果把"工作区根目录"这种每次可能不同的东西夹在中间，后面所有内容
（包括几十 KB 的对话历史）都得按未命中价重算。所以**变动的一律往后放**。

实测：第 14 讲那次对话，第一圈就命中了 764 token，正是工具定义加系统提示
这一整段稳定前缀。

──────────────────────────────────────────────────────────────
怎么加一层
──────────────────────────────────────────────────────────────
往 LAYERS 列表末尾加一个文件名就行，函数不用改。

**但这只适用于 templates/ 下的模板。** 第 24 讲原本打算把 MEMORY.md
也加进 LAYERS，实测 test_context.py 立刻红了三个：read_template 只认
templates/，找不到就抛 FileNotFoundError；而"还没记过任何东西、文件不存在"
对记忆来说是常态，不是错误。

所以记忆走另一条路：由调用方（main.py）用 agent/memory.py 读好，
当作参数传进来。两者的区别：

    templates/SOUL.md 等      data/memory/MEMORY.md
    作者（人）写              Agent 自己（经 Dream）写
    进 git                    不进 git（含真实对话里的事实）
    不存在 = bug，该报错       不存在 = 常态，当作没有记忆

记忆排在运行时之后：实际使用中几乎总在同一个目录启动，运行时那段很少变；
记忆却每整合一次就可能变。两者谁前谁后，缓存上的差别只有运行时那一段
（约 17 token，用 tokens.py 的尺子量过），所以直接按"谁变得勤谁靠后"排。
"""
from __future__ import annotations

# TEMPLATES_DIR 在这里重新导出，是为了让 agent.context.TEMPLATES_DIR 仍然可用
# （tests/test_context.py 就是这么引的）；真正的定义在 prompts.py，
# 那里是"模板文件在哪"这件事的唯一来源。
#
# noqa: F401 是告诉代码检查工具"我知道这个导入没在本文件里用到，是故意的"。
from agent.prompts import TEMPLATES_DIR, read_template  # noqa: F401

# 按变动频率从低到高排列。**顺序就是拼装顺序，改这个列表就改了系统提示的结构。**
# 只放 templates/ 下的模板：MEMORY.md **不能**加进来，原因见文件顶部"怎么加一层"。
LAYERS = ["SOUL.md", "AGENTS.md"]


def build_system_prompt(workspace_root: str, memory: str = "") -> str:
    """读模板、拼成一条完整的系统提示。

    谁会用它
        main.py，在模块级初始化时调一次：

            SYSTEM_PROMPT = build_system_prompt(str(root), read_memory())

        只调一次、之后每轮复用——因为它不变，才能一直命中缓存。

    传入什么
        workspace_root: 工作区的绝对路径（字符串）。
                        它是**运行时信息**：同一份代码，在不同目录启动
                        就会不一样，所以不能写进 .md 文件，只能由代码注入。
        memory:         长期记忆正文，由 agent/memory.py 的 read_memory() 读好传进来。
                        不传或传 ""（没记过东西）表示没有记忆，**整段不出现**，
                        连标题都不加。

                        为什么收"文本"而不收"路径"：让这个函数保持纯粹，
                        同样的输入永远得到同样的输出、不碰磁盘。记忆存在哪、
                        怎么读、读不到怎么办，全是 memory.py 的事。

    返回什么
        一整条系统提示（str），各层之间用空行隔开。形如：

            # Soul

            我是 mini_nanobot，一个能读写文件的助手。
            ……

            # 在这个工作区怎么做事

            - 所有文件操作都限制在工作区根目录及其子目录内……
            ……

            # 运行时

            工作区根目录：D:\\VibeCoding\\Agent\\mini_nanobot

            # 长期记忆

            - 用户的项目代号是 蓝鲸-7
            ……

    拿到之后怎么用
        塞进 messages 的第一条：

            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + 历史

        注意这条 system 消息**只活在内存里，永远不写进存档**
        （它在写盘边界之前）。所以改了 SOUL.md 之后恢复旧会话，
        用的是新的系统提示，不是当初那份。

    会抛什么
        FileNotFoundError —— LAYERS 里某个文件不存在。
        因为 main.py 在模块级就调它，所以这个错会在**启动时**出现。

    例子
        >>> prompt = build_system_prompt("D:/proj")
        >>> prompt.index("# Soul") < prompt.index("# 运行时")   # 稳定的在前
        True
        >>> "D:/proj" in prompt                                # 运行时信息被注入了
        True

        没有记忆时，整段不出现；有记忆时，排在最后：

        >>> "# 长期记忆" in build_system_prompt("D:/proj")
        False
        >>> build_system_prompt("D:/proj", memory="- 用户叫 Himeko").endswith(
        ...     "# 长期记忆\\n\\n- 用户叫 Himeko")
        True
    """
    parts = [read_template(name) for name in LAYERS]

    # 运行时信息：换目录启动才会变，放在模板层之后
    parts.append(f"# 运行时\n\n工作区根目录：{workspace_root}")

    # 长期记忆：变得最勤，放最后。没有就整段不加——
    # 一个空标题既浪费 token，又会让模型以为"记忆是空的"是件值得注意的事
    if memory:
        parts.append(f"# 长期记忆\n\n{memory}")

    return "\n\n".join(parts)
"""模板文件的唯一入口：所有提示词文本都从这里读。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一扇门。项目里凡是要读 templates/ 下的文件，都从这里走。

它只有二十几行代码，但解决了一个具体问题：**"模板放在哪"这件事只能有一个
地方知道**。agent/context.py（拼系统提示）和 agent/compaction.py（压缩提示词）
都要读模板，如果它们各自写一遍

    Path(__file__).resolve().parent.parent / "templates"

以后挪目录就要改两处，而且很容易只改一处、另一处过很久才发现。

有个测试专门盯着这一点（tests/test_prompts.py）：

    assert context.TEMPLATES_DIR is prompts.TEMPLATES_DIR

用 `is` 而不是 `==`，因为两个模块各算一遍路径的话 `==` 也会通过，
只有 `is` 能看出它们是不是同一个对象。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**知道模板内容是干什么用的（是身份？是指令？它不关心）
- 它**不**做缓存，每次调用都真的去读一次文件
- 它**不**做模板渲染（没有变量替换、没有条件分支）——就是读纯文本

──────────────────────────────────────────────────────────────
目录约定
──────────────────────────────────────────────────────────────
    templates/
    ├── SOUL.md              我是谁（作者预设，几乎不变）
    ├── AGENTS.md            在这个工作区怎么做事（跟项目走）
    └── prompts/             内部指令
        └── compaction.md    压缩历史时给模型下的指令

**为什么分两层**：

    根目录下的      每次请求都发出去，是模型"人格"的一部分
    prompts/ 下的   只在特定动作时用一次，不进主对话的上下文

SOUL.md 是"你是谁"，模型每一圈都要看见；compaction.md 是"现在请你把这段
历史压一压"，只在压缩那一次用。放在不同层级，一眼就能看出这个区别。

按计划，prompts/ 以后还会长出记忆整合（阶段五）、子 Agent 系统提示（阶段八）。
"""
from __future__ import annotations

from pathlib import Path

# 模板目录。锚在 __file__（代码位置），不锚在 cwd（启动目录）——
# 模板是代码的一部分，跟着代码走；只有"工作区"才该跟着你在哪儿启动走。
#
# parent.parent 是因为这个文件在 agent/ 下，要往上跳两层才到项目根目录：
#     .../mini_nanobot/agent/prompts.py
#         .parent        -> .../mini_nanobot/agent
#         .parent.parent -> .../mini_nanobot
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def read_template(*parts: str) -> str:
    """读一个模板文件，返回去掉首尾空白的纯文本。

    谁会用它
        - agent/context.py：读 SOUL.md、AGENTS.md 拼系统提示
        - agent/compaction.py：读 prompts/compaction.md 当压缩指令

    传入什么
        *parts: 相对 templates/ 的路径片段，**一层一个参数**。

                    read_template("SOUL.md")
                        -> templates/SOUL.md

                    read_template("prompts", "compaction.md")
                        -> templates/prompts/compaction.md

                为什么不直接传 "prompts/compaction.md" 一个字符串：
                斜杠在 Windows 和 Linux 上写法不同，拆成多个参数交给
                Path.joinpath 拼，两边都对。

    返回什么
        文件内容（str），**首尾空白已经用 .strip() 去掉**。

        去空白是为了拼装时干净：模板文件末尾常常多一个换行，
        不去掉的话拼起来会出现莫名其妙的空行。

    会抛什么
        FileNotFoundError —— 文件不存在。错误消息里**带完整路径**，
        并说明"提示词都放在 templates/ 下，不写在代码里"。

        为什么要自己拼这句话：原生的 FileNotFoundError 只说"没这个文件"，
        不说"它本来该在哪、是干什么的"。配置和资源缺失是最高频的问题，
        多写三行错误信息，能省下别人十分钟。

    什么时候会被调用
        **在 import 阶段。** agent/compaction.py 顶部有一句

            SUMMARY_INSTRUCTION = read_template("prompts", "compaction.md")

        所以模板文件缺失的话，**程序一启动就报错**，而不是等聊到一半
        真的要压缩时才炸。这是有意的：启动期炸掉比运行期炸掉好排查得多。

    改了模板要重启吗
        要。系统提示和压缩指令都是在 import 时读一次、之后复用。
        改完 .md 文件，重新跑一次 python main.py 就生效——**但不用改代码**，
        这正是"提示词外化"要的效果。

    例子
        >>> text = read_template("SOUL.md")
        >>> text.startswith("# Soul")
        True
        >>> text == text.strip()                 # 首尾空白已去掉
        True

        找不到时报错，消息里带着文件名：

        >>> try:
        ...     read_template("prompts", "不存在.md")
        ... except FileNotFoundError as exc:
        ...     print("不存在.md" in str(exc))
        True
    """
    path = TEMPLATES_DIR.joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(
            f"缺少模板文件：{path}\n"
            f"（提示词都放在 templates/ 下，不写在代码里；"
            f"缺了就说明文件被删了或者路径写错了。）"
        )
    return path.read_text(encoding="utf-8").strip()

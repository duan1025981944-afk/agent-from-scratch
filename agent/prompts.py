"""模板文件的唯一入口：所有提示词文本都从这里读。

**为什么单独开一个模块**：`context.py`（系统提示）和 `compaction.py`（压缩提示词）
都要读 `templates/` 下的文件。如果各自写一遍 `Path(__file__).parent.parent / "templates"`，
以后挪目录就要改两个地方，而且很容易只改一处——交接文档里"两套路径基准"那条待办
说的就是这种事。一件事只让一个地方知道。

**目录约定**：

    templates/
    ├── SOUL.md              # 系统提示的常驻层（谁：作者预设，多久变：几乎不变）
    ├── AGENTS.md            # 在这个工作区怎么做事
    └── prompts/             # 内部用的提示词——不是给模型看的身份，是给它下的指令
        └── compaction.md    # 压缩历史时用的指令

顶层的 `SOUL.md` / `AGENTS.md` 是**每次请求都发出去**的常驻内容；
`prompts/` 下的是**特定动作才用一次**的指令。放在不同层级，是为了一眼看出这个区别。
"""
from __future__ import annotations

from pathlib import Path

# 模板目录锚在代码位置，不锚在 cwd——从任何目录启动都要找得到
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def read_template(*parts: str) -> str:
    """读一个模板文件，去掉首尾空白。

    read_template("SOUL.md")                 → templates/SOUL.md
    read_template("prompts", "compaction.md") → templates/prompts/compaction.md

    找不到时报错要指名道姓：配置和资源缺失是最高频的问题，
    原生的 FileNotFoundError 只说"没这个文件"，不说"它本来该在哪、是干什么的"。
    """
    path = TEMPLATES_DIR.joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(
            f"缺少模板文件：{path}\n"
            f"（提示词都放在 templates/ 下，不写在代码里；"
            f"缺了就说明文件被删了或者路径写错了。）"
        )
    return path.read_text(encoding="utf-8").strip()
"""长期记忆：MEMORY.md 在哪、怎么读。对应第 24 讲。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
**语义记忆**的存取入口。MEMORY.md 里放的是"以后换个会话还用得上的事实"，
启动时由 main.py 读出来，交给 context.py 拼进系统提示。

阶段五会一点点往这里加东西（日记账、Dream 整合、快照……），
第 24 讲只做最小的一件事：**读**。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**拼系统提示——那是 context.py 的事，它只拿到一段文本
- 它**不**写 MEMORY.md——写入只归 Dream（第 27 讲），主 Agent 碰不到（第 24 讲下半）
- 它**不**是 templates/。见下面

──────────────────────────────────────────────────────────────
为什么 MEMORY.md 不放在 templates/（本文件最重要的一点）
──────────────────────────────────────────────────────────────
templates/ 和 MEMORY.md 看起来都是"拼进系统提示的 .md"，但本质完全不同：

    ┌──────────────┬──────────────────────┬──────────────────────────┐
    │              │ templates/SOUL.md 等  │ data/memory/MEMORY.md    │
    ├──────────────┼──────────────────────┼──────────────────────────┤
    │ 谁写         │ 作者（人）            │ Agent 自己（经 Dream）   │
    │ 进不进 git   │ 进，推到 GitHub       │ 不进，含真实对话里的事实 │
    │ 不存在时     │ 是 bug，应该报错      │ 是常态（刚装好还没记过） │
    └──────────────┴──────────────────────┴──────────────────────────┘

nanobot 也是这么分的：包里的 templates/memory/MEMORY.md 只是个空模板，
真正被读写的是工作区里的 memory/MEMORY.md（见 nanobot/utils/helpers.py
的 sync_workspace_templates，和 agent/memory.py 的 MemoryStore.__init__）。

路径锚在代码位置、放在 data/ 下，和 session/messages.py 的 SESSIONS_DIR 同一个
基准。data/ 已经在 .gitignore 里。
"""
from __future__ import annotations

from pathlib import Path

# 记忆文件的唯一位置。测试里用 tmp_path 代替，不会碰到真文件。
MEMORY_FILE = Path(__file__).resolve().parent.parent / "data" / "memory" / "MEMORY.md"


def read_memory(path: Path = MEMORY_FILE) -> str:
    """
    读出长期记忆的正文；没有记忆时返回空字符串。

    谁会用它
        main.py，启动时读一次，结果传给 build_system_prompt()。

    传入什么
        path: 记忆文件路径。平时不用传，用默认的 MEMORY_FILE；
              测试里传 tmp_path 下的文件。

    返回什么
        str。去掉首尾空白后的正文。以下情况都返回 ""：
            - 文件不存在（刚装好、还没 Dream 过——这是常态，不是错误）
            - 文件存在但是空的，或者只有空白

    为什么用 utf-8-sig 而不是 utf-8
        Windows PowerShell 5.1 的 `Set-Content -Encoding utf8` 会在文件开头
        写一个 BOM（\\ufeff）。用 utf-8 读，这个字符会原样留在开头，而且
        str.strip() **去不掉它**（它不算空白）。utf-8-sig 读的时候会自动吃掉 BOM，
        没有 BOM 的文件也照常读。

    例子（碰文件系统，所以不写成 doctest，见 tests/test_memory_injection.py）
        文件不存在           -> ""
        文件内容 "\\n  \\n"   -> ""
        文件内容 "- 用户叫 Himeko\\n" -> "- 用户叫 Himeko"
    """
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig").strip()
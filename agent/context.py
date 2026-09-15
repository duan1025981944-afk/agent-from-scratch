"""把系统提示按"变动频率"分层拼装。

排序规则：**稳定的在前，变动的在后**。
缓存从第 0 个 token 开始逐个匹配，中间改一个字，后面全部失效。
把永远不变的放最前面，它们每次都能命中缓存。

    ┌─ SOUL.md    我是谁            几乎不变   ← 缓存永远命中
    ├─ AGENTS.md  在这个工作区怎么做事  跟项目走
    └─ 运行时     工作区根目录等       每次运行可能不同
"""
from __future__ import annotations

# TEMPLATES_DIR 在这里重新导出，是为了让老的 agent.context.TEMPLATES_DIR 仍然可用；
# 真正的定义在 prompts.py，那里是"模板文件在哪"这件事的唯一来源。
from agent.prompts import TEMPLATES_DIR, read_template  # noqa: F401

# 按变动频率从低到高排列。阶段五的 MEMORY.md 往这个列表末尾加一行即可。
LAYERS = ["SOUL.md", "AGENTS.md"]


def build_system_prompt(workspace_root: str) -> str:
    """读模板、拼成一条系统提示。改模板即生效，不用改代码。"""
    parts = [read_template(name) for name in LAYERS]

    # 运行时信息放最后：它每次运行都可能不同，放前面会让后面全部错过缓存
    parts.append(f"# 运行时\n\n工作区根目录：{workspace_root}")

    return "\n\n".join(parts)
"""把存档 messages 加工成"这一次要发给模型的东西"。

阶段三检查点 2：现在里面只有截断一件事。
阶段四的上下文分层、压缩，都会长在这个文件里。
"""
from __future__ import annotations

from typing import Any

# 从 runner.py 搬过来的
MAX_TOOL_RESULT_CHARS = 4000

def _truncate(content: str) -> str:
    """超长的工具结果剪短，并告诉模型丢了多少。"""
    dropped = len(content) - MAX_TOOL_RESULT_CHARS
    return (
        content[:MAX_TOOL_RESULT_CHARS]
        + f"\n\n…（内容过长，已截断，省略 {dropped:,} 个字符）"
    )


def build_payload(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """输入：完整存档。输出：这一次要发出去的消息列表。

    绝不修改 messages 本身——这是本函数唯一的铁律。
    """
    payload: list[dict[str, Any]] = []

    for msg in messages:
        # 只有工具结果需要截断，其他角色原样放行
        if msg.get("role") != "tool":
            payload.append(msg)
            continue

        content = msg.get("content") or ""
        if len(content) <= MAX_TOOL_RESULT_CHARS:
            payload.append(msg)
            continue

        # ★ 关键三行：先复制，再改副本，原件一个字都不碰
        shrunk = dict(msg)                       # 复制出一个新字典
        shrunk["content"] = _truncate(content)
        payload.append(shrunk)

    return payload
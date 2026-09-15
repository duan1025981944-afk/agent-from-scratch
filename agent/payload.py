"""把存档 messages 加工成"这一次要发给模型的东西"。

阶段三检查点 2：截断单条过长的工具结果。
第 13 讲：总量超预算时，把旧的工具结果换成占位说明。
阶段四的上下文分层、压缩，都会长在这个文件里。
"""
from __future__ import annotations

from typing import Any

from agent.tokens import estimate_message_tokens, estimate_tokens

# 从 runner.py 搬过来的
MAX_TOOL_RESULT_CHARS = 4000

# ── 两个阈值，不是一个 ──────────────────────────────────────────────
# 清理工具结果不花钱，所以早一点动手；摘要压缩要多调一次模型，留到最后。
# 数值对齐 Anthropic 上下文管理 API 的默认值（clear 100k / compact 150k）。
CLEAR_TRIGGER = 100_000       # 超过这个数就开始清理旧工具结果（便宜）
CONTEXT_BUDGET = 190_000      # 超过这个数就必须摘要压缩（贵）

# 一旦开始清理，至少要清掉这么多，否则干脆不清。
# 理由：清理会改动 payload 中段，从那里往后的缓存全部失效（要按未命中价重算）。
# 每轮只清一点点，等于每轮都付一次缓存重建的钱。
# 对应 Anthropic 的 clear_at_least 参数。
CLEAR_AT_LEAST = 20_000

# 最近几条工具结果不清——模型可能还在用。对应 Anthropic 的 keep（默认 3）。
KEEP_TOOL_RESULTS = 3

# 旧工具结果被省略时，模型看到的替代文字
# 要讲清两件事：发生了什么、需要的话怎么找回来
PLACEHOLDER = "[这条工具结果较早，为节省上下文已省略。原文仍在会话存档里；如果还需要，请重新调用工具。]"

def _truncate(content: str) -> str:
    """超长的工具结果剪短，并告诉模型丢了多少。"""
    dropped = len(content) - MAX_TOOL_RESULT_CHARS
    return (
        content[:MAX_TOOL_RESULT_CHARS]
        + f"\n\n…（内容过长，已截断，省略 {dropped:,} 个字符）"
    )


def _protected_from(payload: list[dict[str, Any]], keep: int) -> int:
    """从这个下标开始（含）的消息都不清。两条保护取更宽的那条：

    1. 最近 keep 条工具结果——模型可能还要回看（Anthropic 默认 keep=3）
    2. 最后一次工具调用的**整批**结果——模型正要用它们作答，
       一次并行调了 5 个工具时，只留 3 条会把它正在用的削掉
    """
    tool_indexes = [i for i, m in enumerate(payload) if m.get("role") == "tool"]
    if not tool_indexes:
        return len(payload)                               # 没有工具结果，无事可做
    if len(tool_indexes) <= keep:
        return 0                                          # 总共就这么几条，全保留

    by_count = tool_indexes[-keep]
    by_batch = len(payload)
    for i in range(len(payload) - 1, -1, -1):
        if payload[i].get("tool_calls"):
            by_batch = i
            break
    return min(by_count, by_batch)


def _replace_old_tool_results(
    payload: list[dict[str, Any]],
    trigger: int,
    clear_at_least: int = CLEAR_AT_LEAST,
    keep: int = KEEP_TOOL_RESULTS,
) -> list[dict[str, Any]]:
    """超过 trigger 时，从最旧的工具结果开始换成 PLACEHOLDER。

    三条规则：
    1. 从最旧的开始换——已经换掉的，以后历史再变长也不会被换回来
    2. 最近几条 / 最新那一批不换（见 _protected_from）
    3. **要么清够 clear_at_least，要么一条都不清**——清一点点不值得打断缓存
    """
    used = estimate_tokens(payload)
    if used <= trigger:
        return payload                                    # 没到阈值，什么都不做

    protected_from = _protected_from(payload, keep)
    # 目标：既要降到阈值以下，又至少要清掉 clear_at_least
    target = min(trigger, used - clear_at_least)

    cleared: list[tuple[int, dict[str, Any]]] = []        # 先记下来，够不够再说
    saved_total = 0

    for i in range(protected_from):
        if used - saved_total <= target:
            break                                         # 清够了就停
        msg = payload[i]
        if msg.get("role") != "tool" or msg.get("content") == PLACEHOLDER:
            continue

        replaced = dict(msg)                              # 复制，不碰原件
        replaced["content"] = PLACEHOLDER
        saved = estimate_message_tokens(msg) - estimate_message_tokens(replaced)
        if saved <= 0:
            continue                                      # 原本就比占位说明短，换了反而更长

        cleared.append((i, replaced))
        saved_total += saved

    if saved_total < min(clear_at_least, used - trigger):
        return payload                                    # 清不够，不如不清——留给摘要压缩

    for i, replaced in cleared:
        payload[i] = replaced                             # 换的是 payload 列表里的一格，不是 messages
    return payload


def build_payload(
    messages: list[dict[str, Any]],
    clear_trigger: int = CLEAR_TRIGGER,
    clear_at_least: int = CLEAR_AT_LEAST,
    keep: int = KEEP_TOOL_RESULTS,
) -> list[dict[str, Any]]:
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

    # 第二步：总量过了清理阈值，就把旧的工具结果换成占位说明
    return _replace_old_tool_results(payload, clear_trigger, clear_at_least, keep)


def measure_payload(
    payload: list[dict[str, Any]], budget: int = CONTEXT_BUDGET
) -> tuple[int, bool]:
    """量一下这份 payload：大约多少 token、超没超预算。只量，不改。"""
    used = estimate_tokens(payload)
    return used, used > budget
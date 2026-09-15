"""
尺子：估算一份 messages 发给模型时大约占多少 token。

- 只估算：真实数字要等 DeepSeek 回复后才知道，而"要不要压缩"必须在发送之前决定
- 零依赖：不装 tokenizer，用"字符数 × 系数"
- 目前只提供函数，谁都还没调用它；第 12 讲由 build_payload 调用
"""

import json

# 系数来自第 11 讲的实测（DeepSeek V4 分词器）：
#   非 ASCII 字符：中文 0.60，─ → ✅ 这类符号 0.58~0.73  → 统一按 0.6
#   ASCII 字符：   Markdown 文档 0.32，Python 代码 0.21  → 取 0.3（一个系数盖不住两种内容，偏高一侧）
NON_ASCII_RATIO = 0.6
ASCII_RATIO = 0.3

# 每条消息除了文字，还有 role 标记之类的"包装"，按每条 4 个 token 算（数值借鉴 nanobot）
PER_MESSAGE_OVERHEAD = 4


def estimate_text_tokens(text: str) -> int:
    """一段文字大约多少 token：ASCII 和非 ASCII 分开乘系数。

    为什么按 ASCII 分：ASCII 字符（英文、数字、代码符号、空格）在 UTF-8 里占 1 个字节，
    分词器很容易把它们合并（8 个缩进空格 = 1 个 token）；
    中文和 ─ → ✅ 这类符号都占 2~4 个字节，合并得少，实测都在 0.6 左右。
    """
    non_ascii = sum(1 for ch in text if not ch.isascii())
    ascii_count = len(text) - non_ascii
    return round(non_ascii * NON_ASCII_RATIO + ascii_count * ASCII_RATIO)


def _message_text(msg: dict) -> str:
    """一条消息里所有会发给模型的文字。

    不能只看 content：模型决定调工具时，content 往往是空的，
    但 tool_calls 里那段 {"name": "read_file", "arguments": ...} 的 JSON 一样占 token。
    """
    parts = []
    if isinstance(msg.get("content"), str):
        parts.append(msg["content"])
    if msg.get("tool_calls"):
        parts.append(json.dumps(msg["tool_calls"], ensure_ascii=False))
    if isinstance(msg.get("tool_call_id"), str):
        parts.append(msg["tool_call_id"])
    return "".join(parts)


def estimate_message_tokens(msg: dict) -> int:
    """量一条。以后压缩时要靠它找出"最重的是哪几条"。"""
    return estimate_text_tokens(_message_text(msg)) + PER_MESSAGE_OVERHEAD


def estimate_tokens(messages: list[dict]) -> int:
    """量一整份。以后触发时要靠它判断"总量超没超"。"""
    return sum(estimate_message_tokens(m) for m in messages)